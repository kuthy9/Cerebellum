"""Turn the event log into spans: one per step attempt, children for LLM / connector calls."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from cerebellum.runtime.store import EventRecord

_STEP_END = {"step.succeeded", "step.failed", "step.retrying"}


@dataclass(frozen=True)
class Span:
    span_id: str
    parent_id: str | None
    step_id: str | None
    kind: str  # "step" | "llm" | "connector"
    label: str
    start: float
    end: float | None
    status: str  # running | waiting | retrying | succeeded | failed
    detail: dict[str, Any]


def call_label(kind: str, data: dict[str, Any]) -> str:
    if kind == "llm":
        label = f"llm {data.get('model', '')}".strip()
        if data.get("mock"):
            label += " (mock)"
        if data.get("repair"):
            label += f" · repair {data['repair']}"
        return label
    if "method" in data:
        return f"{data['method']} {data.get('path', '')} → {data.get('status') or 'error'}"
    return f"sql {data.get('operation', 'query')}"


def build_spans(events: Iterable[EventRecord]) -> list[Span]:
    spans: dict[str, Span] = {}
    for event in events:
        if event.type == "step.started" and event.span_id:
            spans[event.span_id] = Span(
                span_id=event.span_id,
                parent_id=None,
                step_id=event.step_id,
                kind="step",
                label=f"{event.step_id} #{event.data.get('attempt', 1)}",
                start=event.ts,
                end=None,
                status="running",
                detail={
                    "type": event.data.get("type"),
                    "fallback_for": event.data.get("fallback_for"),
                },
            )
        elif event.span_id in spans and spans[event.span_id].kind == "step":
            span = spans[event.span_id]
            if event.type in _STEP_END:
                spans[event.span_id] = replace(
                    span, end=event.ts, status=event.type.split(".", 1)[1]
                )
            elif event.type == "step.waiting":
                spans[event.span_id] = replace(span, status="waiting")
        elif event.type in ("llm.call", "connector.call") and event.span_id:
            kind = event.type.split(".", 1)[0]
            duration = float(event.data.get("duration_ms") or 0.0) / 1000
            spans[event.span_id] = Span(
                span_id=event.span_id,
                parent_id=event.parent_span_id,
                step_id=event.step_id,
                kind=kind,
                label=call_label(kind, event.data),
                start=event.ts - duration,
                end=event.ts,
                status="succeeded" if event.data.get("ok", True) else "failed",
                detail=event.data,
            )
    return list(spans.values())
