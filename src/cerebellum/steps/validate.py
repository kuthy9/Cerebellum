"""`validate` step: deterministic business rules. Any failing rule fails the step."""

from __future__ import annotations

from typing import Any

from cerebellum.errors import StepError
from cerebellum.spec.expressions import eval_condition
from cerebellum.steps.base import StepRuntime


async def run_validate(rt: StepRuntime) -> Any:
    rules = rt.step.rules
    failed = [rule.message for rule in rules if not eval_condition(rule.expr, rt.ctx)]
    if failed:
        raise StepError(
            "; ".join(failed), retryable=False, kind="validation", details={"failed": failed}
        )
    return {"passed": True, "checked": len(rules)}
