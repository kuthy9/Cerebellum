"""`http` step: REST call with an idempotency key that is stable across retries and resumes."""

from __future__ import annotations

import time
from typing import Any

from cerebellum.connectors.base import ConnectorError, HttpConnector
from cerebellum.connectors.rest import redact_headers
from cerebellum.errors import StepError
from cerebellum.spec.expressions import render
from cerebellum.steps.base import StepRuntime, elapsed_ms


async def run_http(rt: StepRuntime) -> Any:
    step = rt.step
    path = render(step.path, rt.ctx)
    body = render(step.body, rt.ctx)
    headers = render(step.headers, rt.ctx)
    query = render(step.query, rt.ctx)
    connector = await rt.connectors.get(step.connector)
    if not isinstance(connector, HttpConnector):
        raise StepError(
            f"connector {step.connector!r} is not an HTTP connector",
            retryable=False,
            kind="config",
        )
    key = f"{rt.run_id}:{step.id}"
    sent_headers = {**connector.default_headers, **headers, "Idempotency-Key": key}
    trace: dict[str, Any] = {
        "connector": step.connector,
        "method": step.method,
        "path": path,
        "request": {"headers": redact_headers(sent_headers), "query": query, "body": body},
    }
    started = time.perf_counter()
    try:
        response = await connector.request(
            step.method, path, json=body, headers=headers, query=query, idempotency_key=key
        )
    except ConnectorError as exc:
        rt.record_call(
            "connector",
            {
                **trace,
                "ok": False,
                "status": exc.details.get("status"),
                "response": {"body": exc.details.get("body")},
                "error": str(exc),
                "duration_ms": elapsed_ms(started),
            },
        )
        raise
    rt.record_call(
        "connector",
        {
            **trace,
            "ok": True,
            "status": response.status,
            "response": {"headers": response.headers, "body": response.body},
            "duration_ms": elapsed_ms(started),
        },
    )
    return {"status": response.status, "body": response.body, "headers": response.headers}
