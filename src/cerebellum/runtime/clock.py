"""Injectable time source so retries, leases and approval timeouts are testable."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> float:
        return time.time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeClock:
    """Deterministic clock: sleep() advances time instantly and records the requested delay."""

    def __init__(self, start: float = 1_790_000_000.0):
        self._now = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += seconds
        await asyncio.sleep(0)
