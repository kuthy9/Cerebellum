"""Provider-agnostic AI contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from cerebellum.config import DEFAULT_AI_MAX_TOKENS
from cerebellum.errors import StepError
from cerebellum.spec.models import MockRule


class AIError(StepError):
    """An AI provider call failed."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class AIRequest:
    model: str
    prompt: str
    schema: dict[str, Any]
    system: str | None = None
    effort: str | None = None
    max_tokens: int = DEFAULT_AI_MAX_TOKENS
    mock_rules: tuple[MockRule, ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIResult:
    text: str
    model: str
    usage: Usage
    cost_usd: float
    mock: bool
    stop_reason: str
    latency_ms: float


class AIProvider(Protocol):
    name: str
    mock: bool

    async def generate(self, request: AIRequest, messages: list[dict[str, Any]]) -> AIResult: ...
