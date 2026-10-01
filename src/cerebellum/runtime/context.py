"""The expression context steps see: input, params, run and every step's projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cerebellum.runtime.store import RunRecord, StepRecord


def build_context(
    run: RunRecord,
    steps: Mapping[str, StepRecord],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "input": run.input,
        "params": run.params,
        "run": {"id": run.run_id, "cost_usd": run.cost_usd},
        "steps": {
            step_id: {
                "output": record.output,
                "status": record.status.value,
                "error": record.error,
                "attempts": record.attempts,
            }
            for step_id, record in steps.items()
        },
    }
    if extra:
        context.update(extra)
    return context
