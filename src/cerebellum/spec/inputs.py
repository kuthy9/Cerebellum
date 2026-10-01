"""Validate run input against the workflow's input declaration and resolve params."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

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
        if not _TYPE_CHECKS[field.type](value):
            issues.append(
                SpecIssue(
                    f"input.{name}",
                    f"expected {field.type}, got {type(value).__name__} {value!r}",
                )
            )
        elif field.enum is not None and value not in field.enum:
            issues.append(SpecIssue(f"input.{name}", f"must be one of {field.enum}"))
    for key in sorted(set(data) - set(wf.input), key=str):
        issues.append(SpecIssue(f"input.{key}", "is not declared in the workflow"))
    if issues:
        raise SpecError(issues)
    return dict(data)


def resolve_params(wf: Workflow, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - set(wf.params))
    if unknown:
        raise SpecError(
            [SpecIssue(f"params.{key}", "is not declared in the workflow") for key in unknown]
        )
    return {**wf.params, **overrides}
