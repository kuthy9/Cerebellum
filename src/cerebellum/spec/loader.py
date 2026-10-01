"""Load workflow YAML, interpolate connector env vars, validate structure and semantics."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from cerebellum.errors import SpecError, SpecIssue, TemplateError
from cerebellum.spec.expressions import check_expression, check_template, is_template
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
            [SpecIssue(issue_path(err["loc"]), err["msg"]) for err in exc.errors()]
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
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out or "<root>"


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

        issues.extend(_expression_issues(path, step))

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


def _expression_issues(path: str, step: Any) -> list[SpecIssue]:
    issues: list[SpecIssue] = []

    def expr(field: str, value: str) -> None:
        try:
            check_expression(value)
        except TemplateError as exc:
            issues.append(SpecIssue(f"{path}.{field}", str(exc)))

    def template(field: str, value: Any) -> None:
        try:
            check_template(value)
        except TemplateError as exc:
            issues.append(SpecIssue(f"{path}.{field}", str(exc)))

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
