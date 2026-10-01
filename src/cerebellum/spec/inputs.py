"""Validate run input against the workflow's input declaration and resolve params."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from cerebellum.config import MAX_NESTING_DEPTH
from cerebellum.errors import SpecError, SpecIssue
from cerebellum.spec.models import Workflow

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


TOO_DEEP = f"is nested more than {MAX_NESTING_DEPTH} levels deep"


def nests_deeper_than(value: Any, limit: int = MAX_NESTING_DEPTH) -> bool:
    """Whether lists and dicts nest more than `limit` levels deep in `value` ([] is one level,
    [[]] two). Iterative, so any depth is safe, and a value that contains itself (a YAML alias
    inside its own anchor) is infinitely deep."""
    deepest: dict[int, int] = {}  # id of a list or dict → the deepest level it was seen at
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        item, level = pending.pop()
        if not isinstance(item, dict | list):
            continue
        if level > limit:
            return True
        if deepest.get(id(item), 0) >= level:
            continue  # shared (YAML aliases): seen at least this deep already
        deepest[id(item)] = level
        children = item.values() if isinstance(item, dict) else item
        pending.extend((child, level + 1) for child in children)
    return False


def validate_input(wf: Workflow, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SpecError([SpecIssue("input", "run input must be a JSON object")])
    issues: list[SpecIssue] = []
    for name, field in wf.input.items():
        value = data.get(name)
        if value is None:
            if field.required:
                issues.append(SpecIssue(f"input.{name}", "is required"))
            continue
        # First: copying, showing or walking a value this deep fails (a RecursionError).
        if nests_deeper_than(value):
            issues.append(SpecIssue(f"input.{name}", TOO_DEEP))
        elif not _TYPE_CHECKS[field.type](value):
            issues.append(
                SpecIssue(
                    f"input.{name}",
                    f"expected {field.type}, got {type(value).__name__} {value!r}",
                )
            )
        elif field.enum is not None and value not in field.enum:
            issues.append(SpecIssue(f"input.{name}", f"must be one of {field.enum}"))
        else:
            issues += _non_finite_issues(value, f"input.{name}")
    for key in sorted(set(data) - set(wf.input), key=str):
        issues.append(SpecIssue(f"input.{key}", "is not declared in the workflow"))
    if issues:
        raise SpecError(issues)
    return dict(data)


def resolve_params(wf: Workflow, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - set(wf.params))
    issues = [SpecIssue(f"params.{key}", "is not declared in the workflow") for key in unknown]
    for key, value in overrides.items():
        if key in wf.params:
            if nests_deeper_than(value):
                issues.append(SpecIssue(f"params.{key}", TOO_DEEP))
            else:
                issues += _non_finite_issues(value, f"params.{key}")
    if issues:
        raise SpecError(issues)
    return {**wf.params, **overrides}


def _non_finite_issues(value: Any, path: str) -> list[SpecIssue]:
    """The first NaN or infinity anywhere in `value`, as an issue. A run's input and params are
    JSON, which has neither (the dashboard can only show such a number as null)."""
    if isinstance(value, float):
        if math.isfinite(value):
            return []
        return [SpecIssue(path, f"must be a finite number, got {value!r}")]
    items: Iterable[tuple[str, Any]]
    if isinstance(value, dict):
        items = ((f"{path}.{key}", item) for key, item in value.items())
    elif isinstance(value, list):
        items = ((f"{path}[{index}]", item) for index, item in enumerate(value))
    else:
        return []
    for where, item in items:
        found = _non_finite_issues(item, where)
        if found:
            return found
    return []
