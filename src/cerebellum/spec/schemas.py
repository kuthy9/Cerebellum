"""JSON Schema helpers for structured AI outputs."""

from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

_NESTED_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")
_NESTED_MAPS = ("properties", "$defs", "definitions", "patternProperties")


def strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy of `schema` where every object forbids additional properties (Claude structured
    outputs require it). Explicit `additionalProperties` values are kept."""
    result = copy.deepcopy(schema)
    _strictify_in_place(result)
    return result


def _strictify_in_place(node: Any) -> None:
    if not isinstance(node, dict):
        return
    if node.get("type") == "object" or "properties" in node:
        node.setdefault("additionalProperties", False)
    for key in _NESTED_MAPS:
        children = node.get(key)
        if isinstance(children, dict):
            for child in children.values():
                _strictify_in_place(child)
    for key in _NESTED_LISTS:
        children = node.get(key)
        if isinstance(children, list):
            for child in children:
                _strictify_in_place(child)
    if isinstance(node.get("items"), dict):
        _strictify_in_place(node["items"])


def schema_problems(schema: dict[str, Any]) -> list[str]:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return [exc.message]
    if schema.get("type") != "object":
        return ["output_schema must have type: object at the root"]
    return []


def validation_errors(schema: dict[str, Any], instance: Any) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(instance), key=lambda e: [str(p) for p in e.absolute_path]
    )
    return [_format(error) for error in errors]


def _format(error: Any) -> str:
    path = "/".join(str(part) for part in error.absolute_path)
    return f"{path or '<root>'}: {error.message}"


def minimal_instance(schema: dict[str, Any]) -> Any:
    """Deterministic smallest value that satisfies the common schema keywords."""
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            return minimal_instance(schema[key][0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        return {
            name: minimal_instance(properties.get(name, {})) for name in schema.get("required", [])
        }
    if kind == "array":
        return [minimal_instance(schema.get("items", {})) for _ in range(schema.get("minItems", 0))]
    if kind == "string":
        return "x" * schema.get("minLength", 0)
    if kind in ("number", "integer"):
        if "minimum" in schema:
            value = schema["minimum"]
        elif "exclusiveMinimum" in schema:
            value = schema["exclusiveMinimum"] + 1
        else:
            value = 0
        return int(value) if kind == "integer" else value
    if kind == "boolean":
        return False
    return None
