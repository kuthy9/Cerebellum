"""Sandboxed Jinja2 expressions and templates.

Two evaluation modes:
* conditions (`when`, validate rules, eval assertions) are lenient: a missing value is falsy and
  an expression that cannot be evaluated because of a missing value is False;
* value templates (params, body, prompt, ...) are strict: referencing a missing value raises
  TemplateError naming the template.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from jinja2 import (
    ChainableUndefined,
    StrictUndefined,
    TemplateSyntaxError,
    Undefined,
    UndefinedError,
    nodes,
)
from jinja2.exceptions import SecurityError
from jinja2.parser import Parser
from jinja2.sandbox import SandboxedEnvironment

from cerebellum.errors import TemplateError

_SINGLE_EXPR = re.compile(r"^\s*\{\{((?:(?!\{\{|\}\}).)+)\}\}\s*$", re.S)


def _finalize(value: Any) -> Any:
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


class _Env(SandboxedEnvironment):
    """Mappings resolve `a.b` as a key lookup, so data keys like `items` never hit dict methods."""

    def getattr(self, obj: Any, attribute: str) -> Any:
        if isinstance(obj, Mapping):
            try:
                return obj[attribute]
            except KeyError:
                return self.undefined(obj=obj, name=attribute)
        return super().getattr(obj, attribute)


def _make_env(undefined: type[Undefined]) -> _Env:
    env = _Env(undefined=undefined, autoescape=False, finalize=_finalize)
    env.globals.update(len=len, min=min, max=max, abs=abs, round=round)
    return env


_STRICT = _make_env(StrictUndefined)
_LENIENT = _make_env(ChainableUndefined)


def is_template(text: str) -> bool:
    return "{{" in text or "{%" in text


def check_expression(expr: str) -> None:
    try:
        _LENIENT.compile_expression(expr)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"invalid expression {expr!r}: {exc.message}") from exc


def check_template(value: Any) -> None:
    """Recursively compile every template string inside `value`."""
    if isinstance(value, str):
        if is_template(value):
            try:
                _LENIENT.from_string(value)
            except TemplateSyntaxError as exc:
                raise TemplateError(f"invalid template {value!r}: {exc.message}") from exc
    elif isinstance(value, Mapping):
        for item in value.values():
            check_template(item)
    elif isinstance(value, list):
        for item in value:
            check_template(item)


def expression_step_refs(expr: str) -> set[str]:
    """Step ids a valid expression reads as `steps.<id>` or `steps['<id>']`."""
    return _step_refs(Parser(_LENIENT, expr, state="variable").parse_expression())


def template_step_refs(value: Any) -> set[str]:
    """Step ids read by the valid template strings inside `value`. A computed lookup such as
    `steps[name]` cannot be known before the run and is left out."""
    if isinstance(value, str):
        return _step_refs(_LENIENT.parse(value)) if is_template(value) else set()
    if isinstance(value, Mapping):
        return set().union(*(template_step_refs(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(template_step_refs(item) for item in value))
    return set()


def _step_refs(tree: nodes.Node) -> set[str]:
    refs: set[str] = set()
    for node in tree.find_all((nodes.Getattr, nodes.Getitem)):
        if not (isinstance(node.node, nodes.Name) and node.node.name == "steps"):
            continue
        if isinstance(node, nodes.Getattr):
            refs.add(node.attr)
        elif isinstance(node.arg, nodes.Const) and isinstance(node.arg.value, str):
            refs.add(node.arg.value)
    return refs


def eval_condition(expr: str, ctx: Mapping[str, Any]) -> bool:
    try:
        result = _LENIENT.compile_expression(expr, undefined_to_none=False)(**ctx)
    except UndefinedError:
        return False
    except TemplateSyntaxError as exc:
        raise TemplateError(f"invalid expression {expr!r}: {exc.message}") from exc
    except SecurityError as exc:
        raise TemplateError(f"unsafe expression {expr!r}: {exc}") from exc
    return bool(result)


def render(value: Any, ctx: Mapping[str, Any], *, strict: bool = True) -> Any:
    """Render templates inside `value` recursively. A string that is exactly one `{{ expr }}`
    keeps the native type of the expression result."""
    if isinstance(value, str):
        return _render_str(value, ctx, strict)
    if isinstance(value, Mapping):
        return {key: render(item, ctx, strict=strict) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, ctx, strict=strict) for item in value]
    return value


def _render_str(text: str, ctx: Mapping[str, Any], strict: bool) -> Any:
    if not is_template(text):
        return text
    env = _STRICT if strict else _LENIENT
    single = _SINGLE_EXPR.match(text)
    try:
        if single:
            result = env.compile_expression(single.group(1), undefined_to_none=False)(**ctx)
            if isinstance(result, Undefined):
                if strict:
                    raise TemplateError(f"cannot render {text!r}: value is undefined")
                return None
            return result
        return env.from_string(text).render(**ctx)
    except UndefinedError as exc:
        if strict:
            raise TemplateError(f"cannot render {text!r}: {exc.message}") from exc
        return None
    except (TemplateSyntaxError, SecurityError) as exc:
        raise TemplateError(f"cannot render {text!r}: {exc}") from exc
