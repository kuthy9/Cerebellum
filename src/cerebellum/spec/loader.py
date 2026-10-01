"""Load workflow YAML, interpolate connector env vars, validate structure and semantics."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Mapping
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from cerebellum.errors import SpecError, SpecIssue, TemplateError
from cerebellum.spec.expressions import (
    check_expression,
    check_template,
    expression_step_refs,
    is_template,
    template_step_refs,
)
from cerebellum.spec.models import (
    AiStep,
    ApprovalStep,
    HttpStep,
    QueryStep,
    TaskStep,
    ValidateStep,
    Workflow,
)
from cerebellum.spec.schemas import schema_problems

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_CONNECTOR_TYPES = {"query": "postgres", "http": "rest"}
# Workflow fields whose items are unions discriminated by `type`.
_TAGGED_FIELDS = ("steps", "fallbacks", "connectors")
# What pydantic appends to an error location when a mapping key itself is invalid.
_KEY_MARKER = "[key]"


def load_workflow(path: str | Path, *, env: Mapping[str, str] | None = None) -> Workflow:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError([SpecIssue(str(path), f"cannot read file: {exc.strerror}")]) from exc
    return parse_workflow(text, base_dir=path.resolve().parent, env=env)


def parse_workflow(
    text: str,
    *,
    base_dir: str | Path = ".",
    env: Mapping[str, str] | None = None,
    require_env: bool = True,
) -> Workflow:
    """With `require_env=False` (for display only: no connector may be opened), an unset
    connector variable is kept as written instead of being an issue."""
    env = os.environ if env is None else env
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError([SpecIssue("<yaml>", str(exc))]) from exc
    if not isinstance(data, dict):
        raise SpecError([SpecIssue("<root>", "workflow must be a YAML mapping")])

    issues: list[SpecIssue] = []
    if "connectors" in data:
        data["connectors"] = _interpolate(
            data["connectors"], env, "connectors", issues, require_env
        )
    if issues:
        raise SpecError(issues)

    try:
        workflow = Workflow.model_validate(data)
    except ValidationError as exc:
        raise SpecError(
            [SpecIssue(issue_path(_untagged(err["loc"], data)), err["msg"]) for err in exc.errors()]
        ) from exc

    issues = semantic_issues(workflow)
    if issues:
        raise SpecError(issues)

    base = str(Path(base_dir))
    workflow.source_yaml = text
    workflow.base_dir = base
    workflow.digest = hashlib.sha256(f"{base}\0{text}".encode()).hexdigest()[:16]
    return workflow


def issue_path(loc: tuple[Any, ...]) -> str:
    out = ""
    for i, part in enumerate(loc):
        if part == _KEY_MARKER:
            continue
        if loc[i + 1 : i + 2] == (_KEY_MARKER,):
            out += f" (key {part})" if out else f"(key {part})"
        elif isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out or "<root>"


def _untagged(loc: tuple[Any, ...], data: dict[str, Any]) -> tuple[Any, ...]:
    """Drop the union tag pydantic puts after a step or connector in an error location
    (steps[0].query.sql -> steps[0].sql); a field that shares the tag's name is kept."""
    if len(loc) < 3 or loc[0] not in _TAGGED_FIELDS:
        return loc
    try:
        tag = data[loc[0]][loc[1]]["type"]
    except (KeyError, IndexError, TypeError):
        return loc
    return (*loc[:2], *loc[3:]) if loc[2] == tag else loc


def _interpolate(
    value: Any, env: Mapping[str, str], path: str, issues: list[SpecIssue], required: bool
) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if env.get(name):
                return env[name]
            if default is not None:
                return default
            if not required:
                return match.group(0)
            issues.append(SpecIssue(path, f"environment variable {name} is not set"))
            return ""

        return _ENV_REF.sub(replace, value)
    if isinstance(value, dict):
        return {
            key: _interpolate(item, env, f"{path}.{key}", issues, required)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _interpolate(item, env, f"{path}[{i}]", issues, required)
            for i, item in enumerate(value)
        ]
    return value


def semantic_issues(wf: Workflow) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    located = [(f"steps[{i}]", step, False) for i, step in enumerate(wf.steps)] + [
        (f"fallbacks[{i}]", step, True) for i, step in enumerate(wf.fallbacks)
    ]
    main = set(wf.step_ids)
    fallbacks = set(wf.fallback_ids)
    all_ids = main | fallbacks

    seen: set[str] = set()
    for path, step, _ in located:
        if step.id in seen:
            issues.append(SpecIssue(f"{path}.id", f"duplicate step id '{step.id}'"))
        seen.add(step.id)

    upstream = _upstream(wf)
    owners: dict[str, str] = {}
    for step in wf.steps:
        if step.on_failure:
            owners.setdefault(step.on_failure.fallback, step.id)

    def stray_ref(step_id: str, ref: str) -> str | None:
        """Why `steps.<ref>` has no settled value when main step `step_id` runs, or None."""
        before = upstream[step_id]
        if ref == step_id or ref in before:
            return None
        if ref in main:
            return (
                f"step '{ref}' is not upstream of '{step_id}' "
                "(not in its needs, directly or transitively)"
            )
        if ref in fallbacks:
            if owners.get(ref) in before:
                return None
            return f"fallback '{ref}' does not belong to a step upstream of '{step_id}'"
        return f"unknown step '{ref}'"

    fallback_users: dict[str, str] = {}
    for path, step, is_fallback in located:
        if is_fallback:
            if step.needs:
                issues.append(SpecIssue(f"{path}.needs", "fallback steps cannot declare needs"))
            if step.when:
                issues.append(SpecIssue(f"{path}.when", "fallback steps cannot declare when"))
            if step.on_failure:
                issues.append(
                    SpecIssue(f"{path}.on_failure", "fallback steps cannot declare on_failure")
                )
        else:
            for dep in step.needs:
                if dep == step.id:
                    issues.append(SpecIssue(f"{path}.needs", "a step cannot depend on itself"))
                elif dep in fallbacks:
                    issues.append(
                        SpecIssue(
                            f"{path}.needs",
                            f"'{dep}' is a fallback step and cannot be a dependency",
                        )
                    )
                elif dep not in main:
                    issues.append(SpecIssue(f"{path}.needs", f"unknown step '{dep}'"))
            if step.on_failure:
                target = step.on_failure.fallback
                if target not in fallbacks:
                    issues.append(
                        SpecIssue(
                            f"{path}.on_failure.fallback",
                            f"unknown fallback '{target}' (declare it under fallbacks:)",
                        )
                    )
                elif target in fallback_users:
                    issues.append(
                        SpecIssue(
                            f"{path}.on_failure.fallback",
                            f"fallback '{target}' is already used by step "
                            f"'{fallback_users[target]}'",
                        )
                    )
                else:
                    fallback_users[target] = step.id

        if isinstance(step, ApprovalStep):
            if is_fallback:
                issues.append(SpecIssue(path, "approval steps cannot be fallbacks"))
            if step.retry.max or step.on_failure:
                issues.append(SpecIssue(path, "approval steps cannot declare retry or on_failure"))
            for ref in step.show:
                if ref not in all_ids:
                    issues.append(SpecIssue(f"{path}.show", f"unknown step '{ref}'"))

        if isinstance(step, QueryStep | HttpStep):
            expected = _CONNECTOR_TYPES[step.type]
            spec = wf.connectors.get(step.connector)
            if spec is None:
                issues.append(
                    SpecIssue(f"{path}.connector", f"unknown connector '{step.connector}'")
                )
            elif spec.type != expected:
                issues.append(
                    SpecIssue(
                        f"{path}.connector",
                        f"step type '{step.type}' needs a '{expected}' connector, "
                        f"'{step.connector}' is '{spec.type}'",
                    )
                )

        if isinstance(step, QueryStep) and is_template(step.sql):
            issues.append(
                SpecIssue(f"{path}.sql", "SQL is never templated; use :name bind parameters")
            )

        if isinstance(step, AiStep):
            for problem in schema_problems(step.output_schema):
                issues.append(SpecIssue(f"{path}.output_schema", problem))

        # Fallbacks run after a failure and may read any main step.
        issues.extend(_expression_issues(path, step, None if is_fallback else stray_ref))

    graph = {step.id: {dep for dep in step.needs if dep in main} for step in wf.steps}
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        issues.append(SpecIssue("steps", f"dependency cycle: {' -> '.join(exc.args[1])}"))

    for key, value in wf.output.items():
        try:
            check_template(value)
        except TemplateError as exc:
            issues.append(SpecIssue(f"output.{key}", str(exc)))
    return issues


def _upstream(wf: Workflow) -> dict[str, set[str]]:
    """Each main step's transitive needs: the steps that have settled before it runs."""
    main = set(wf.step_ids)
    needs = {step.id: [dep for dep in step.needs if dep in main] for step in wf.steps}
    upstream: dict[str, set[str]] = {}
    for step_id, direct in needs.items():
        found: set[str] = set()
        pending = list(direct)
        while pending:
            dep = pending.pop()
            if dep not in found:
                found.add(dep)
                pending.extend(needs[dep])
        upstream[step_id] = found
    return upstream


def _expression_issues(
    path: str, step: Any, stray_ref: Callable[[str, str], str | None] | None
) -> list[SpecIssue]:
    issues: list[SpecIssue] = []

    def refs(field: str, found: set[str]) -> None:
        if stray_ref is None:
            return
        for ref in sorted(found):
            problem = stray_ref(step.id, ref)
            if problem:
                issues.append(SpecIssue(f"{path}.{field}", problem))

    def expr(field: str, value: str) -> None:
        try:
            check_expression(value)
        except TemplateError as exc:
            issues.append(SpecIssue(f"{path}.{field}", str(exc)))
        else:
            refs(field, expression_step_refs(value))

    def template(field: str, value: Any) -> None:
        try:
            check_template(value)
        except TemplateError as exc:
            issues.append(SpecIssue(f"{path}.{field}", str(exc)))
        else:
            refs(field, template_step_refs(value))

    if step.when:
        expr("when", step.when)
    if isinstance(step, QueryStep):
        template("params", step.params)
    elif isinstance(step, HttpStep):
        template("path", step.path)
        template("body", step.body)
        template("headers", step.headers)
        template("query", step.query)
    elif isinstance(step, AiStep):
        template("prompt", step.prompt)
        if step.system:
            template("system", step.system)
        for i, rule in enumerate(step.mock):
            if rule.when:
                expr(f"mock[{i}].when", rule.when)
            template(f"mock[{i}].output", rule.output)
    elif isinstance(step, ValidateStep):
        for i, rule in enumerate(step.rules):
            expr(f"rules[{i}].expr", rule.expr)
    elif isinstance(step, ApprovalStep):
        template("title", step.title)
    elif isinstance(step, TaskStep):
        template("title", step.title)
        template("payload", step.payload)
    return issues
