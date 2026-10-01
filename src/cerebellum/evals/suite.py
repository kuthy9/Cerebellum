"""Eval suite files: which workflow, which cases, and what each case must produce."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cerebellum.errors import SpecError, SpecIssue, TemplateError
from cerebellum.sandbox.payments import FailMode
from cerebellum.spec.expressions import check_expression
from cerebellum.spec.inputs import validate_input
from cerebellum.spec.loader import issue_path, load_workflow
from cerebellum.spec.models import Identifier, Workflow

Decision = Literal["approved", "rejected"]
# What an `expect` path may start with, and the fields each root exposes (see checks.run_view).
PATH_ROOTS = ("status", "error", "output", "steps", "tasks", "approvals", "run")
STEP_FIELDS = ("status", "output", "attempts", "error")
ROOT_FIELDS = {
    "tasks": ("count", "open"),
    "approvals": ("count",),
    "run": ("id", "cost_usd", "duration_s"),
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvalDefaults(_Model):
    approval: Decision = "approved"
    sandbox: str = "never"
    mock: bool = False


class EvalCase(_Model):
    id: Identifier
    description: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    approval: Decision | None = None
    sandbox: str | None = None
    expect: dict[str, Any] = Field(default_factory=dict)
    asserts: list[str] = Field(default_factory=list, alias="assert")


class EvalSuite(_Model):
    suite: Identifier
    description: str = ""
    workflow: str
    defaults: EvalDefaults = Field(default_factory=EvalDefaults)
    cases: list[EvalCase] = Field(min_length=1)


@dataclass(frozen=True)
class LoadedSuite:
    suite: EvalSuite
    workflow: Workflow
    path: Path

    def decision(self, case: EvalCase) -> Decision:
        return case.approval or self.suite.defaults.approval

    def fail_mode(self, case: EvalCase) -> str:
        return case.sandbox or self.suite.defaults.sandbox


def load_suite(path: str | Path, *, env: Mapping[str, str] | None = None) -> LoadedSuite:
    """Parse and check a suite, and load the workflow it names (relative to the suite file)."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError([SpecIssue(str(path), f"cannot read file: {exc.strerror}")]) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError([SpecIssue("<yaml>", str(exc))]) from exc
    if not isinstance(data, dict):
        raise SpecError([SpecIssue("<root>", "an eval suite must be a YAML mapping")])
    try:
        suite = EvalSuite.model_validate(data)
    except ValidationError as exc:
        raise SpecError(
            [SpecIssue(issue_path(err["loc"]), err["msg"]) for err in exc.errors()]
        ) from exc
    try:
        workflow = load_workflow(path.parent / suite.workflow, env=env)
    except SpecError as exc:
        raise SpecError(
            [SpecIssue(f"workflow: {issue.path}", issue.message) for issue in exc.issues]
        ) from exc
    issues = suite_issues(suite, workflow)
    if issues:
        raise SpecError(issues)
    return LoadedSuite(suite, workflow, path.resolve())


def suite_issues(suite: EvalSuite, workflow: Workflow) -> list[SpecIssue]:
    issues = _fail_mode_issues("defaults.sandbox", suite.defaults.sandbox)
    step_ids = {*workflow.step_ids, *workflow.fallback_ids}
    seen: set[str] = set()
    for index, case in enumerate(suite.cases):
        where = f"cases[{index}]"
        if case.id in seen:
            issues.append(SpecIssue(f"{where}.id", f"duplicate case id {case.id!r}"))
        seen.add(case.id)
        if case.sandbox is not None:
            issues += _fail_mode_issues(f"{where}.sandbox", case.sandbox)
        try:
            validate_input(workflow, case.input)
        except SpecError as exc:
            issues += [SpecIssue(f"{where}.{issue.path}", issue.message) for issue in exc.issues]
        if not case.expect and not case.asserts:
            issues.append(SpecIssue(where, "a case needs at least one expect entry or assert"))
        for key in case.expect:
            problem = path_problem(key, step_ids)
            if problem:
                issues.append(SpecIssue(f"{where}.expect.{key}", problem))
        for position, expr in enumerate(case.asserts):
            try:
                check_expression(expr)
            except TemplateError as exc:
                issues.append(SpecIssue(f"{where}.assert[{position}]", str(exc)))
    return issues


def path_problem(path: str, step_ids: Collection[str]) -> str | None:
    """Why an `expect` path can never resolve, or None when it is well formed."""
    root, *rest = segments = path.split(".")
    if "" in segments:
        return "has an empty segment"
    if root not in PATH_ROOTS:
        return f"unknown path {root!r}; paths start with one of: {', '.join(PATH_ROOTS)}"
    if root in ("status", "error"):
        return f"{root} has no fields" if rest else None
    if root == "output":
        return None
    if root == "steps":
        if len(rest) < 2:
            return f"use steps.<step id>.<{'|'.join(STEP_FIELDS)}>"
        step_id, field, *deeper = rest
        if step_id not in step_ids:
            return f"unknown step {step_id!r}"
        if field not in STEP_FIELDS:
            return f"unknown step field {field!r}; use one of: {', '.join(STEP_FIELDS)}"
        if deeper and field != "output":
            return f"steps.{step_id}.{field} has no fields"
        return None
    fields = ROOT_FIELDS[root]
    if len(rest) != 1 or rest[0] not in fields:
        return f"use {root}.<{'|'.join(fields)}>"
    return None


def _fail_mode_issues(where: str, mode: str) -> list[SpecIssue]:
    try:
        FailMode.parse(mode)
    except ValueError as exc:
        return [SpecIssue(where, str(exc))]
    return []
