"""Connector contracts, registry and a lazily-opening connector pool."""

from __future__ import annotations

import abc
import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from cerebellum.errors import CerebellumError, StepError


class ConnectorError(StepError):
    """A connector call failed; `retryable` follows the error-classification table."""


@dataclass(frozen=True)
class HealthStatus:
    ok: bool
    detail: str
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ConnectorEnv:
    home: Path
    base_dir: Path
    http_transports: Mapping[str, httpx.AsyncBaseTransport] = field(default_factory=dict)


class Connector(abc.ABC):
    def __init__(self, name: str):
        self.name = name

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @abc.abstractmethod
    async def health(self) -> HealthStatus: ...


class SqlConnector(Connector):
    @abc.abstractmethod
    async def query(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def execute(self, sql: str, params: Mapping[str, Any]) -> int: ...


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any
    headers: dict[str, str]
    elapsed_ms: float


class HttpConnector(Connector):
    @property
    def default_headers(self) -> dict[str, str]:
        return {}

    @abc.abstractmethod
    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> HttpResponse: ...


Factory = Callable[[str, Any, ConnectorEnv], Connector]
_REGISTRY: dict[str, Factory] = {}


def register_connector(type_name: str) -> Callable[[Factory], Factory]:
    def decorator(factory: Factory) -> Factory:
        _REGISTRY[type_name] = factory
        return factory

    return decorator


def create_connector(name: str, spec: Any, env: ConnectorEnv) -> Connector:
    factory = _REGISTRY.get(spec.type)
    if factory is None:
        raise CerebellumError(f"no connector registered for type {spec.type!r}")
    return factory(name, spec, env)


def jsonable(value: Any) -> Any:
    """Convert driver values (Decimal, dates, bytes, UUID) into JSON-friendly values."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, UUID):
        return str(value)
    return value


class ConnectorPool:
    """Opens each connector on first use and closes them all together."""

    def __init__(self, specs: Mapping[str, Any], env: ConnectorEnv):
        self._specs = dict(specs)
        self._env = env
        self._open: dict[str, Connector] = {}
        self._lock = asyncio.Lock()

    async def get(self, name: str) -> Connector:
        async with self._lock:
            if name not in self._open:
                if name not in self._specs:
                    raise CerebellumError(f"connector {name!r} is not declared")
                connector = create_connector(name, self._specs[name], self._env)
                await connector.open()
                self._open[name] = connector
            return self._open[name]

    async def close(self) -> None:
        connectors, self._open = list(self._open.values()), {}
        for connector in connectors:
            try:
                await connector.close()
            except Exception:  # closing is best effort; the run outcome is already recorded
                pass
