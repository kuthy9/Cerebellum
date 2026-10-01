import random

import pytest

from cerebellum.runtime.retry import backoff_delay
from cerebellum.spec.models import RetryPolicy


def test_exponential_backoff_is_capped():
    policy = RetryPolicy(max=5, base=1.0, max_delay=5.0)
    assert [backoff_delay(policy, n) for n in range(1, 6)] == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_fixed_backoff():
    policy = RetryPolicy(max=3, backoff="fixed", base=0.5)
    assert [backoff_delay(policy, n) for n in (1, 2, 3)] == [0.5, 0.5, 0.5]


def test_jitter_stays_within_bounds():
    policy = RetryPolicy(max=1, base=2.0)
    rng = random.Random(1)
    delays = [backoff_delay(policy, 1, jitter=0.1, rng=rng) for _ in range(50)]
    assert all(1.8 <= d <= 2.2 for d in delays)
    assert len(set(delays)) > 1
    assert backoff_delay(policy, 1, jitter=0.0) == pytest.approx(2.0)
