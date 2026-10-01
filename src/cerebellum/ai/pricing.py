"""USD prices per million tokens. Override or extend with CEREBELLUM_PRICING_FILE (JSON:
{"model": {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0}})."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cerebellum.ai.base import Usage


@dataclass(frozen=True)
class ModelPrice:
    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None

    def cost(self, usage: Usage) -> float:
        cache_read = self.cache_read if self.cache_read is not None else self.input * 0.1
        cache_write = self.cache_write if self.cache_write is not None else self.input * 1.25
        total = (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_read_input_tokens * cache_read
            + usage.cache_creation_input_tokens * cache_write
        )
        return round(total / 1_000_000, 8)


DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-fable-5-1": ModelPrice(10.0, 50.0),
    "claude-opus-5-5": ModelPrice(4.0, 20.0, cache_read=0.20),
    "claude-opus-5": ModelPrice(5.0, 25.0),
    "claude-sonnet-5-5": ModelPrice(2.0, 10.0, cache_read=0.20),
    "claude-sonnet-5": ModelPrice(2.0, 10.0),
    "claude-haiku-4-5": ModelPrice(1.0, 5.0),
}


class Pricing:
    def __init__(self, prices: dict[str, ModelPrice] | None = None):
        self.prices = dict(DEFAULT_PRICES if prices is None else prices)

    @classmethod
    def load(cls, path: Path | None) -> Pricing:
        pricing = cls()
        if path is None:
            return pricing
        data = json.loads(path.read_text(encoding="utf-8"))
        for model, entry in data.items():
            pricing.prices[model] = ModelPrice(
                float(entry["input"]),
                float(entry["output"]),
                entry.get("cache_read"),
                entry.get("cache_write"),
            )
        return pricing

    def cost(self, model: str, usage: Usage) -> float:
        price = self.prices.get(model)
        return price.cost(usage) if price else 0.0
