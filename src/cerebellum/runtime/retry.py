"""Backoff schedule for step retries."""

from __future__ import annotations

import random

from cerebellum.spec.models import RetryPolicy


def backoff_delay(
    policy: RetryPolicy,
    attempt: int,
    *,
    jitter: float = 0.0,
    rng: random.Random | None = None,
) -> float:
    """Delay before retry number `attempt` (1-based). Jitter is a ± fraction of the delay."""
    if policy.backoff == "fixed":
        delay = policy.base
    else:
        delay = policy.base * (2 ** (attempt - 1))
    delay = min(delay, policy.max_delay)
    if jitter:
        delay *= 1 + (rng or random).uniform(-jitter, jitter)
    return max(delay, 0.0)
