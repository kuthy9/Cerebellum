"""USD prices per million tokens. Override or extend with CEREBELLUM_PRICING_FILE (JSON:
{"model": {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0}})."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cerebellum.ai.base import Usage
from cerebellum.errors import ConfigError


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
        """Defaults plus the prices in `path`; raises ConfigError when the file is unusable."""
        pricing = cls()
        if path is None:
            return pricing
        where = f"CEREBELLUM_PRICING_FILE {path}"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot read {where}: {exc.strerror or exc}") from exc
        except ValueError as exc:  # invalid JSON or not UTF-8
            raise ConfigError(f"{where} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{where} must be a JSON object that maps model names to prices")
        for model, entry in data.items():
            try:
                pricing.prices[model] = _model_price(entry)
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(
                    f"{where}: {model!r} needs non-negative numeric input and output prices "
                    "per million tokens (optional: cache_read, cache_write), e.g. "
                    '{"input": 4.0, "output": 20.0}'
                ) from exc
        return pricing

    def cost(self, model: str, usage: Usage) -> float:
        price = self.prices.get(model)
        return price.cost(usage) if price else 0.0


def _price(value: Any) -> float:
    """A JSON number that is finite and not negative; booleans and strings are not prices."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"a price must be a JSON number, not {value!r}")
    try:
        price = float(value)
    except OverflowError:  # a JSON integer too large for a float
        raise ValueError("a price must be finite, not an integer too large for a float") from None
    if not math.isfinite(price) or price < 0:
        raise ValueError(f"a price must be finite and not negative, not {value!r}")
    return price


def _model_price(entry: Any) -> ModelPrice:
    if not isinstance(entry, dict):
        raise TypeError("a model's prices must be a JSON object")

    def optional(key: str) -> float | None:
        value = entry.get(key)
        return None if value is None else _price(value)

    return ModelPrice(
        _price(entry["input"]),
        _price(entry["output"]),
        optional("cache_read"),
        optional("cache_write"),
    )
