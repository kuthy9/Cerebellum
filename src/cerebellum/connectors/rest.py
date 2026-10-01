"""REST connector on httpx with error classification and header redaction."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

import httpx

from cerebellum.connectors.base import (
    ConnectorEnv,
    ConnectorError,
    HealthStatus,
    HttpConnector,
    HttpResponse,
    register_connector,
)

REDACTED = "***"
_SENSITIVE = re.compile(r"authorization|token|secret|password|cookie|key", re.I)
_NOT_SENSITIVE = {"idempotency-key"}
_RESPONSE_HEADERS = ("content-type", "location", "retry-after", "x-request-id", "idempotency-key")
_MAX_TEXT_BODY = 2000


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        key: REDACTED if key.lower() not in _NOT_SENSITIVE and _SENSITIVE.search(key) else value
        for key, value in headers.items()
    }


def _decode_body(response: httpx.Response) -> Any:
    if not response.content:
        return None
    if "json" in response.headers.get("content-type", ""):
        try:
            return response.json()
        except ValueError:
            pass
    text = response.text
    return text if len(text) <= _MAX_TEXT_BODY else text[:_MAX_TEXT_BODY] + "…"


class RestConnector(HttpConnector):
    def __init__(self, name: str, spec: Any, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(name)
        self.spec = spec
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def default_headers(self) -> dict[str, str]:
        return dict(self.spec.headers)

    async def open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.spec.base_url,
                headers=self.spec.headers,
                timeout=self.spec.timeout,
                transport=self._transport,
            )

    async def close(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> HttpResponse:
        await self.open()
        assert self._client is not None
        merged = dict(headers or {})
        if idempotency_key:
            merged["Idempotency-Key"] = idempotency_key
        started = time.perf_counter()
        try:
            response = await self._client.request(
                method, path, json=json, headers=merged, params=dict(query) if query else None
            )
        except httpx.TimeoutException as exc:
            raise ConnectorError(
                f"{method} {path} timed out", retryable=True, kind="timeout"
            ) from exc
        except httpx.TransportError as exc:
            raise ConnectorError(
                f"{method} {path} connection failed: {exc}", retryable=True, kind="connection"
            ) from exc
        elapsed = (time.perf_counter() - started) * 1000
        result = HttpResponse(
            status=response.status_code,
            body=_decode_body(response),
            headers={k: v for k, v in response.headers.items() if k.lower() in _RESPONSE_HEADERS},
            elapsed_ms=elapsed,
        )
        if response.status_code >= 400:
            retryable = response.status_code >= 500 or response.status_code in (408, 429)
            raise ConnectorError(
                f"{method} {path} returned HTTP {response.status_code}",
                retryable=retryable,
                kind="http_status",
                details={"status": result.status, "body": result.body, "elapsed_ms": elapsed},
            )
        return result

    async def health(self) -> HealthStatus:
        await self.open()
        assert self._client is not None
        started = time.perf_counter()
        target = f"{self.spec.base_url}{self.spec.health_path}"
        try:
            response = await self._client.get(
                self.spec.health_path, timeout=min(self.spec.timeout, 5.0)
            )
        except httpx.HTTPError as exc:
            return HealthStatus(False, f"{target} unreachable: {exc}")
        elapsed = (time.perf_counter() - started) * 1000
        return HealthStatus(
            response.status_code < 400, f"GET {target} → HTTP {response.status_code}", elapsed
        )


@register_connector("rest")
def _make_rest(name: str, spec: Any, env: ConnectorEnv) -> RestConnector:
    return RestConnector(name, spec, transport=env.http_transports.get(name))
