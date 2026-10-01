"""What an eval case can observe about its run, and how its expectations are checked."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from cerebellum.runtime.store import ApprovalRecord, RunRecord, StepRecord, TaskRecord
from cerebellum.spec.expressions import eval_condition

MISSING: Any = object()


@dataclass(frozen=True)
class Check:
    kind: str  # "expect": a dotted path compared with a value; "assert": an expression
    target: str
    passed: bool
    expected: Any = None
    actual: Any = None
    missing: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def run_view(
    run: RunRecord,
    steps: Mapping[str, StepRecord],
    tasks: Sequence[TaskRecord],
    approvals: Sequence[ApprovalRecord],
) -> dict[str, Any]:
    """The values `expect` paths and `assert` expressions can read."""
    duration = None if run.ended_at is None else round(run.ended_at - run.created_at, 6)
    return {
        "status": run.status.value,
        "error": run.error,
        "output": run.output,
        "input": run.input,
        "params": run.params,
        "steps": {
            step_id: {
                "status": record.status.value,
                "output": record.output,
                "attempts": record.attempts,
                "error": record.error,
            }
            for step_id, record in steps.items()
        },
        "tasks": {
            "count": len(tasks),
            "open": sum(1 for task in tasks if task.status == "open"),
        },
        "approvals": {"count": len(approvals)},
        "run": {"id": run.run_id, "cost_usd": run.cost_usd, "duration_s": duration},
    }


def resolve(view: Any, path: str) -> Any:
    """Follow a dotted path through mappings and list indexes; MISSING when it leads nowhere."""
    value = view
    for part in path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return MISSING
    return value


def same(actual: Any, expected: Any) -> bool:
    """Equality for YAML expectations: booleans are not numbers; numbers compare by value."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return isinstance(actual, bool) and isinstance(expected, bool) and actual == expected
    if isinstance(actual, int | float) and isinstance(expected, int | float):
        return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
    return actual == expected


def check_case(
    expect: Mapping[str, Any], asserts: Sequence[str], view: Mapping[str, Any]
) -> list[Check]:
    checks: list[Check] = []
    for path, expected in expect.items():
        actual = resolve(view, path)
        if actual is MISSING:
            checks.append(Check("expect", path, False, expected, None, missing=True))
        else:
            checks.append(Check("expect", path, same(actual, expected), expected, actual))
    for expr in asserts:
        try:
            checks.append(Check("assert", expr, eval_condition(expr, view)))
        except Exception as exc:  # an assertion that cannot be evaluated failed; it must not crash
            checks.append(Check("assert", expr, False, actual=f"{type(exc).__name__}: {exc}"))
    return checks
