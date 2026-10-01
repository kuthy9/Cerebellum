"""`query` step: parameterised SQL through a SQL connector."""

from __future__ import annotations

import re
import time
from typing import Any

from cerebellum.connectors.base import ConnectorError, SqlConnector
from cerebellum.errors import StepError
from cerebellum.spec.expressions import render
from cerebellum.steps.base import StepRuntime, elapsed_ms

_READ_STATEMENTS = frozenset({"select", "show", "explain", "values", "pragma"})
_MAIN_STATEMENTS = _READ_STATEMENTS | {"insert", "update", "delete", "merge", "replace"}
# One SQL token: whitespace or a comment, quoted text, a word, a parenthesis or anything else.
_SQL_TOKEN = re.compile(
    r"""
    \s+ | --[^\n]* | /\*.*?\*/
    | '(?:[^']|'')*' | "(?:[^"]|"")*" | \$(?P<tag>(?:[A-Za-z_]\w*)?)\$.*?\$(?P=tag)\$
    | (?P<word>[A-Za-z_]\w*) | (?P<paren>[()]) | .
    """,
    re.S | re.X,
)


def returns_rows(sql: str) -> bool:
    """Whether a statement produces rows: a read, or a write with a RETURNING clause.

    Only top-level words count: comments, quoted text and anything in parentheses (CTE bodies,
    subqueries) are skipped, and a WITH clause is looked past to the statement it introduces.
    """
    words: list[str] = []
    depth = 0
    for token in _SQL_TOKEN.finditer(sql):
        if token["paren"]:
            depth += 1 if token["paren"] == "(" else -1
        elif token["word"] and depth == 0:
            words.append(token["word"].lower())
    if not words:
        return False
    verb = words[0]
    if verb == "with":
        verb = next((word for word in words[1:] if word in _MAIN_STATEMENTS), "select")
    return verb in _READ_STATEMENTS or "returning" in words


async def run_query(rt: StepRuntime) -> Any:
    step = rt.step
    params = render(step.params, rt.ctx)
    connector = await rt.connectors.get(step.connector)
    if not isinstance(connector, SqlConnector):
        raise StepError(
            f"connector {step.connector!r} is not a SQL connector", retryable=False, kind="config"
        )
    reading = returns_rows(step.sql)
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
