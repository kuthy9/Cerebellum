"""`task` step: open a manual task in the Cerebellum inbox."""

from __future__ import annotations

from typing import Any

from cerebellum.spec.expressions import render
from cerebellum.steps.base import StepRuntime


async def run_task(rt: StepRuntime) -> Any:
    step = rt.step
    title = render(step.title, rt.ctx)
    payload = render(step.payload, rt.ctx)
    task_id = rt.create_task(title, step.assignee, payload)
    return {"task_id": task_id, "title": title, "assignee": step.assignee}
