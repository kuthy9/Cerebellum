"""Deterministic offline provider driven by the step's explicit `mock:` rules."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from cerebellum.ai.base import AIRequest, AIResult, Usage
from cerebellum.spec.expressions import eval_condition, render
from cerebellum.spec.schemas import minimal_instance


class MockProvider:
    name = "mock"
    mock = True

    def __init__(
        self,
        *,
        latency: tuple[float, float] = (0.05, 0.3),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        seed: int = 7,
    ):
        self._latency = latency
        self._sleep = sleep
        self._rng = random.Random(seed)

    async def generate(self, request: AIRequest, messages: list[dict[str, Any]]) -> AIResult:
        started = time.perf_counter()
        low, high = self._latency
        if high > 0:
            await self._sleep(self._rng.uniform(low, high))
        output = self._pick(request)
        return AIResult(
            text=json.dumps(output, ensure_ascii=False),
            model=request.model,
            usage=Usage(),
            cost_usd=0.0,
            mock=True,
            stop_reason="end_turn",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    @staticmethod
    def _pick(request: AIRequest) -> Any:
        for rule in request.mock_rules:
            if rule.when is None or eval_condition(rule.when, request.context):
                return render(rule.output, request.context, strict=False)
        return minimal_instance(request.schema)
