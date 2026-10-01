"""`query` step: parameterised SQL through a SQL connector."""

from __future__ import annotations

import re
import time
from typing import Any

from cerebellum.connectors.base import ConnectorError, SqlConnector
from cerebellum.errors import StepError
from cerebellum.spec.expressions import render
from cerebellum.steps.base import StepRuntime, elapsed_ms

_READ = re.compile(r"^\s*(select|with|show|explain|values|pragma)\b", re.I)


async def run_query(rt: StepRuntime) -> Any:
    step = rt.step
    params = render(step.params, rt.ctx)
    connector = await rt.connectors.get(step.connector)
    if not isinstance(connector, SqlConnector):
        raise StepError(
            f"connector {step.connector!r} is not a SQL connector", retryable=False, kind="config"
        )
    reading = bool(_READ.match(step.sql))
    trace = {
        "connector": step.connector,
        "operation": "query" if reading else "execute",
        "sql": step.sql,
        "params": params,
    }
    started = time.perf_counter()
    try:
        if reading:
            result: Any = await connector.query(step.sql, params)
        else:
            result = await connector.execute(step.sql, params)
    except ConnectorError as exc:
        rt.record_call(
            "connector",
            {**trace, "ok": False, "error": str(exc), "duration_ms": elapsed_ms(started)},
        )
        raise
    count_key = "rows" if reading else "rowcount"
    count = len(result) if reading else result
    rt.record_call(
        "connector", {**trace, "ok": True, count_key: count, "duration_ms": elapsed_ms(started)}
    )
    if not reading:
        return {"rowcount": result}
    return _apply_expect(step.expect, result)


def _apply_expect(expect: str, rows: list[dict[str, Any]]) -> Any:
    count = len(rows)
    if expect == "one":
        if count != 1:
            raise StepError(
                f"expected exactly one row, got {count}",
                retryable=False,
                kind="expect",
                details={"rows": count},
            )
        return rows[0]
    if expect == "many" and count == 0:
        raise StepError("expected at least one row, got 0", retryable=False, kind="expect")
    if expect == "none" and count != 0:
        raise StepError(
            f"expected no rows, got {count}",
            retryable=False,
            kind="expect",
            details={"rows": count},
        )
    return rows
