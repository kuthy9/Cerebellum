"""`cerebellum new`: draft a workflow from a plain-language description with Claude. The draft
goes through the same loader as every workflow; its issues are sent back for a repair."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cerebellum.ai.base import AIProvider, AIRequest
from cerebellum.config import DEFAULT_NEW_ATTEMPTS
from cerebellum.errors import CerebellumError, SpecError, SpecIssue
from cerebellum.spec.loader import parse_workflow
from cerebellum.spec.models import Workflow
from cerebellum.templates import template_path

DRAFT_HEADER = "# AI-generated draft: review it before running.\n"
DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"yaml": {"type": "string"}, "summary": {"type": "string"}},
    "required": ["yaml", "summary"],
    "additionalProperties": False,
}
SYSTEM_PROMPT = (
    "You design Cerebellum workflows: declarative YAML that orchestrates SQL queries, REST "
    "calls, structured LLM steps, deterministic validation rules, human approvals and manual "
    "tasks. Return the complete workflow YAML in `yaml` and one sentence on what it does in "
    "`summary`."
)
STEP_GUIDE = """\
Every step: id, type, needs, when, timeout, retry {max, backoff, base, max_delay},
  on_failure {fallback}, description.
- query: connector (postgres), sql with :name binds only (never templated), params,
  expect one|many|none|any
- http: connector (rest), method, path, body, headers, query; an Idempotency-Key is sent
  automatically
- ai: prompt, system, output_schema (a JSON Schema object), effort low|medium|high,
  mock [{when, output}] so it also runs offline
- validate: rules [{expr, message}]; any false rule fails the run
- approval: title, show [step ids], timeout, on_timeout approve|reject; pauses the run for a human
- task: title, assignee, payload; opens a manual task (the usual fallback)
Rules:
- Conditions (when, rules.expr) and templates ("{{ ... }}") are Jinja over input, params,
  steps.<id>.output/status/error/attempts and run; fallbacks also see failure.step and
  failure.error. An optional input may be absent: write `input.x | default('')`, not `input.x`.
- A step may only read steps.<id> of itself and of steps it needs, directly or transitively;
  fallbacks and `output` may read any step.
- Durations are strings such as 500ms, 30s, 5m, 24h.
- Connectors are declared once under `connectors`: postgres {dsn, seed} or rest
  {base_url, headers, timeout}. Only connector values may use ${VAR:-default}; always give a
  default (dsn "sandbox" is a local SQLite sandbox).
- Fallback steps live under `fallbacks` and run only when a step's retries are exhausted.
- `output` maps the business result with templates. Give every ai step mock rules.
"""
_FENCE = re.compile(r"^\s*```(?:ya?ml)?\s*\n(.*?)\n?```\s*$", re.S)


def workflow_json_schema() -> dict[str, Any]:
    """The workflow definition's JSON Schema, without the fields the loader fills in."""
    schema = Workflow.model_json_schema()
    for name in ("source_yaml", "base_dir", "digest"):
        schema.get("properties", {}).pop(name, None)
    return schema


def build_prompt(description: str) -> str:
    example = (template_path("refund") / "workflow.yaml").read_text(encoding="utf-8")
    schema = json.dumps(workflow_json_schema(), separators=(",", ":"))
    return (
        "Write a Cerebellum workflow for this business process.\n\n"
        f"<process>\n{description.strip()}\n</process>\n\n"
        f"<step_types>\n{STEP_GUIDE}</step_types>\n\n"
        f"<workflow_json_schema>\n{schema}\n</workflow_json_schema>\n\n"
        f"<example_workflow>\n{example}</example_workflow>"
    )


@dataclass(frozen=True)
class Draft:
    yaml: str
    summary: str
    workflow: Workflow
    attempts: int
    cost_usd: float


class DraftError(CerebellumError):
    """Every draft the model wrote failed validation."""

    def __init__(self, attempts: int, issues: list[SpecIssue]):
        self.attempts = attempts
        self.issues = issues
        super().__init__(f"the draft is still invalid after {attempts} attempt(s)")


async def draft_workflow(
    provider: AIProvider,
    description: str,
    *,
    model: str,
    base_dir: Path,
    env: Mapping[str, str] | None = None,
    attempts: int = DEFAULT_NEW_ATTEMPTS,
) -> Draft:
    prompt = build_prompt(description)
    request = AIRequest(model=model, prompt=prompt, schema=DRAFT_SCHEMA, system=SYSTEM_PROMPT)
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    cost = 0.0
    issues: list[SpecIssue] = []
    for attempt in range(1, attempts + 1):
        result = await provider.generate(request, messages)
        cost += result.cost_usd
        try:
            reply = json.loads(result.text)
            text = _unfence(str(reply["yaml"]))
            summary = str(reply.get("summary", ""))
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            issues = [
                SpecIssue("<reply>", "the reply was not the requested JSON object with `yaml`")
            ]
        else:
            try:
                workflow = parse_workflow(text, base_dir=base_dir, env=env)
            except SpecError as exc:
                issues = exc.issues
            else:
                return Draft(text, summary, workflow, attempt, cost)
        messages = [
            *messages,
            {"role": "assistant", "content": result.text},
            {"role": "user", "content": _repair_prompt(issues)},
        ]
    raise DraftError(attempts, issues)


def _unfence(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text


def _repair_prompt(issues: list[SpecIssue]) -> str:
    listed = "\n".join(f"- {issue.path}: {issue.message}" for issue in issues[:20])
    return f"The workflow failed validation:\n{listed}\nReturn the corrected, complete workflow."
