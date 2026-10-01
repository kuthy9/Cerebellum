"""Parse human-friendly durations such as 500ms, 30s, 5m, 24h."""

from __future__ import annotations

import re

_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)\s*$")


def parse_duration(value: object) -> float:
    """Return seconds. Plain numbers are treated as seconds."""
    if isinstance(value, bool):
        raise ValueError(f"invalid duration {value!r}")
    if isinstance(value, int | float):
        if value < 0:
            raise ValueError(f"duration must be >= 0, got {value!r}")
        return float(value)
    if isinstance(value, str):
        match = _PATTERN.match(value)
        if match:
            return float(match.group(1)) * _UNITS[match.group(2)]
    raise ValueError(f"invalid duration {value!r}; use e.g. 500ms, 30s, 5m, 24h")
