"""What a step executor receives and the shared helpers they use."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from cerebellum.ai.base import AIProvider
from cerebellum.config import Settings


class CallRecorder(Protocol):
    def __call__(self, kind: str, data: dict[str, Any], cost_usd: float = 0.0) -> None: ...


class TaskCreator(Protocol):
    def __call__(self, title: str, assignee: str, payload: dict[str, Any]) -> str: ...


class ConnectorSource(Protocol):
    async def get(self, name: str) -> Any: ...


@dataclass
class StepRuntime:
    run_id: str
    step: Any  # one of the cerebellum.spec.models step classes
    attempt: int
    span_id: str
    ctx: Mapping[str, Any]
    connectors: ConnectorSource
    ai: AIProvider
    settings: Settings
    record_call: CallRecorder
    create_task: TaskCreator


StepExecutor = Callable[[StepRuntime], Awaitable[Any]]


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
