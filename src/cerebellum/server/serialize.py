"""JSON shapes returned by the dashboard API, and the JSON text they are sent as."""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from cerebellum.runtime.store import (
    ApprovalRecord,
    EvalResultRecord,
    EvalRunRecord,
    EventRecord,
    RunRecord,
    StepRecord,
    TaskRecord,
)
from cerebellum.runtime.trace import Span
from cerebellum.spec.models import Workflow

if TYPE_CHECKING:
    from cerebellum.server.catalog import WorkflowEntry


def finite(value: Any) -> Any:
    """`value` with every NaN, Infinity and -Infinity (in nested dicts and lists too) as None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: finite(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [finite(item) for item in value]
    return value


def dumps(value: Any, *, default: Callable[[Any], Any] | None = None) -> str:
    """The JSON text of every API response and event-stream message. JSON has no NaN or
    Infinity (browsers refuse them, Starlette's JSONResponse raises), but stored records can
    hold them: a query or AI output, an older run, an infinite cost. They are sent as null, as
    the browser's JSON.stringify does, so a number field stays a number or null."""

    def encode(data: Any) -> str:
        return json.dumps(
            data, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=default
        )

    try:
        return encode(value)
    except ValueError:  # a non-finite number somewhere: rare, so only then copy the value
        return encode(finite(value))


class APIResponse(JSONResponse):
    """The JSON response of every dashboard endpoint and error handler (see `dumps`)."""

    def render(self, content: Any) -> bytes:
        return dumps(content).encode("utf-8")


def run_json(run: RunRecord, *, stale: bool = False) -> dict[str, Any]:
    data = dataclasses.asdict(run)
    data["status"] = run.status.value
    data["stale"] = stale
    data["duration_s"] = None if run.ended_at is None else run.ended_at - run.created_at
    return data


def step_json(record: StepRecord) -> dict[str, Any]:
    data = dataclasses.asdict(record)
    data["status"] = record.status.value
    data["duration_s"] = record.duration
    return data


def _with_run(data: dict[str, Any], run: RunRecord | None) -> dict[str, Any]:
    if run is not None:
        data["workflow_name"] = run.workflow_name
        data["run_status"] = run.status.value
    return data


def approval_json(approval: ApprovalRecord, run: RunRecord | None = None) -> dict[str, Any]:
    return _with_run(dataclasses.asdict(approval), run)


def task_json(task: TaskRecord, run: RunRecord | None = None) -> dict[str, Any]:
    return _with_run(dataclasses.asdict(task), run)


def event_json(event: EventRecord) -> dict[str, Any]:
    return dataclasses.asdict(event)


def span_json(span: Span) -> dict[str, Any]:
    return dataclasses.asdict(span)


def eval_run_json(record: EvalRunRecord, *, stale: bool = False) -> dict[str, Any]:
    data = dataclasses.asdict(record)
    data["stale"] = stale
    data["pass_rate"] = record.pass_rate
    data["duration_s"] = None if record.ended_at is None else record.ended_at - record.created_at
    data["ai_first_pass_rate"] = (
        record.ai_first_ok / record.ai_first_try if record.ai_first_try else None
    )
    return data


def eval_result_json(result: EvalResultRecord) -> dict[str, Any]:
    data = dataclasses.asdict(result)
    data["regression"] = result.regression
    return data


def graph_json(workflow: Workflow) -> dict[str, Any]:
    users = {s.on_failure.fallback: s.id for s in workflow.steps if s.on_failure}
    return {
        "steps": [
            {
                "id": step.id,
                "type": step.type,
                "description": step.description,
                "needs": list(step.needs),
                "when": step.when,
                "fallback": step.on_failure.fallback if step.on_failure else None,
            }
            for step in workflow.steps
        ],
        "fallbacks": [
            {
                "id": step.id,
                "type": step.type,
                "description": step.description,
                "fallback_for": users.get(step.id),
            }
            for step in workflow.fallbacks
        ],
    }


def workflow_summary(entry: WorkflowEntry) -> dict[str, Any]:
    wf = entry.workflow
    return {
        "id": entry.id,
        "name": wf.name,
        "version": wf.version,
        "description": wf.description,
        "source": entry.source,
        "path": entry.path,
        "digest": wf.digest,
        "steps": len(wf.steps),
        "runs": entry.runs,
        "last_run_at": entry.last_run_at,
    }


def workflow_detail(entry: WorkflowEntry) -> dict[str, Any]:
    wf = entry.workflow
    return {
        **workflow_summary(entry),
        "yaml": wf.source_yaml,
        "graph": graph_json(wf),
        "params": wf.params,
        "input": {name: field.model_dump() for name, field in wf.input.items()},
        "samples": entry.samples,
    }
