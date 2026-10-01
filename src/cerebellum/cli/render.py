"""Terminal rendering — tech-minimal: monochrome, one cyan accent, geometric status glyphs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from rich.console import Group
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from cerebellum.connectors import HealthStatus
from cerebellum.errors import SpecIssue
from cerebellum.evals.targets import Target
from cerebellum.runtime.store import (
    ApprovalRecord,
    EvalResultRecord,
    EvalRunRecord,
    RunRecord,
    StepRecord,
    TaskRecord,
)
from cerebellum.runtime.trace import Span
from cerebellum.spec.models import Workflow

ACCENT = "cyan"
MUTED = "grey50"

STEP_GLYPHS: dict[str, tuple[str, str]] = {
    "pending": ("○", MUTED),
    "running": ("◐", ACCENT),
    "retrying": ("↻", "yellow"),
    "waiting": ("⏸", "yellow"),
    "succeeded": ("●", "green"),
    "failed": ("✕", "red"),
    "skipped": ("⊘", MUTED),
    "cancelled": ("⊗", MUTED),
    "recovered": ("⤳", "magenta"),
}
RUN_GLYPHS: dict[str, tuple[str, str]] = {
    "running": ("◐", ACCENT),
    "waiting_approval": ("⏸", "yellow"),
    "succeeded": ("●", "green"),
    "failed": ("✕", "red"),
    "rejected": ("✕", "red"),
    "needs_attention": ("⤳", "magenta"),
}
SPAN_STYLES = {
    "succeeded": "green",
    "failed": "red",
    "retrying": "yellow",
    "waiting": "yellow",
    "running": ACCENT,
    "interrupted": "yellow",  # cut short by a crash or shutdown; resume runs another attempt
    "cancelled": MUTED,
}


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def fmt_cost(usd: float) -> str:
    return f"${usd:.4f}" if usd else ""


def fmt_age(ts: float, now: float) -> str:
    delta = max(now - ts, 0.0)
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def clip(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def header(title: str, subtitle: str = "") -> Text:
    text = Text()
    text.append(" CEREBELLUM ", style=f"bold black on {ACCENT}")
    text.append(f"  {title}", style="bold")
    if subtitle:
        text.append(f"  {subtitle}", style=MUTED)
    return text


def run_status(status: str, *, stale: bool = False) -> Text:
    glyph, style = RUN_GLYPHS.get(status, ("·", ""))
    text = Text(f"{glyph} {status.replace('_', ' ')}", style=style)
    if stale:
        text.append("  stale", style="bold red")
    return text


def _status_label(status: str) -> str:
    return "awaiting approval" if status == "waiting" else status


def _note(record: StepRecord) -> str:
    parts: list[str] = []
    if record.attempts > 1:
        parts.append(f"{record.attempts} attempts")
    if record.status.value == "recovered":
        parts.append("via fallback")
    if record.status.value == "failed" and record.error:
        parts.append(clip(record.error, 64))
    return " · ".join(parts)


def step_table(workflow: Workflow, steps: Mapping[str, StepRecord]) -> Table:
    table = Table.grid(padding=(0, 2))
    for _ in range(7):
        table.add_column()
    for step in (*workflow.steps, *workflow.fallbacks):
        record = steps.get(step.id)
        if record is None:
            continue
        status = record.status.value
        if workflow.is_fallback(step.id) and status == "pending":
            table.add_row(
                Text("·", MUTED),
                Text(step.id, MUTED),
                Text(step.type, MUTED),
                Text("fallback · not triggered", MUTED),
                Text(""),
                Text(""),
                Text(""),
            )
            continue
        glyph, style = STEP_GLYPHS[status]
        dim = status in ("skipped", "cancelled", "pending")
        duration = (
            "" if status in ("pending", "waiting", "running") else fmt_duration(record.duration)
        )
        table.add_row(
            Text(glyph, style=style),
            Text(step.id, style=MUTED if dim else "bold"),
            Text(step.type, style=MUTED),
            Text(_status_label(status), style="" if status == "succeeded" else style),
            Text(duration, style=MUTED),
            Text(fmt_cost(record.cost_usd), style=MUTED),
            Text(_note(record), style=MUTED),
        )
    return table


def run_view(
    run: RunRecord,
    workflow: Workflow,
    steps: Mapping[str, StepRecord],
    *,
    mode: str,
    stale: bool = False,
) -> Group:
    end = run.ended_at or run.updated_at
    footer = run_status(run.status.value, stale=stale)
    footer.append(f"   {fmt_duration(end - run.created_at)}", style=MUTED)
    if run.cost_usd:
        footer.append(f"   {fmt_cost(run.cost_usd)}", style=MUTED)
    return Group(
        header(f"{workflow.name} v{workflow.version}", f"run {run.run_id} · {mode}"),
        Rule(style=MUTED),
        step_table(workflow, steps),
        Rule(style=MUTED),
        footer,
    )


def output_view(output: Any) -> Group:
    body = json.dumps(output, indent=2, ensure_ascii=False, default=str)
    return Group(
        Text("output", style=f"bold {MUTED}"),
        Syntax(body, "json", theme="ansi_dark", background_color="default", word_wrap=True),
    )


def _table(*columns: str) -> Table:
    table = Table(box=None, header_style=f"bold {MUTED}", pad_edge=False, padding=(0, 2, 0, 0))
    for column in columns:
        table.add_column(column)
    return table


def runs_table(runs: Sequence[RunRecord], *, now: float, stale: set[str]) -> Table:
    table = _table("RUN", "WORKFLOW", "STATUS", "COST", "AGE", "AI")
    for run in runs:
        table.add_row(
            Text(run.run_id, style=ACCENT),
            run.workflow_name,
            run_status(run.status.value, stale=run.run_id in stale),
            Text(fmt_cost(run.cost_usd), style=MUTED),
            Text(fmt_age(run.created_at, now), style=MUTED),
            Text("mock" if run.mock else "claude", style=MUTED),
        )
    if not runs:
        table.add_row(Text("no runs yet", style=MUTED), "", "", "", "", "")
    return table


def approvals_table(approvals: Sequence[ApprovalRecord], *, now: float) -> Table:
    table = _table("APPROVAL", "RUN", "STEP", "TITLE", "STATUS", "REQUESTED", "EXPIRES")
    for approval in approvals:
        style = {"pending": "yellow", "approved": "green", "rejected": "red"}.get(
            approval.status, ""
        )
        decided = f" · {approval.decided_by}" if approval.decided_by else ""
        if approval.expires_at is None or approval.status != "pending":
            expires = ""
        elif approval.expires_at < now:
            expires = "overdue"
        else:
            expires = f"in {fmt_duration(approval.expires_at - now)}"
        table.add_row(
            Text(approval.id, style=MUTED),
            Text(approval.run_id, style=ACCENT),
            approval.step_id,
            clip(approval.title, 48),
            Text(approval.status + decided, style=style),
            Text(fmt_age(approval.requested_at, now), style=MUTED),
            Text(expires, style=MUTED),
        )
    if not approvals:
        table.add_row(Text("no pending approvals", style=MUTED), "", "", "", "", "", "")
    return table


def tasks_table(tasks: Sequence[TaskRecord], *, now: float) -> Table:
    table = _table("TASK", "RUN", "TITLE", "ASSIGNEE", "STATUS", "AGE")
    for task in tasks:
        table.add_row(
            Text(task.id, style=MUTED),
            Text(task.run_id, style=ACCENT),
            clip(task.title, 56),
            task.assignee,
            Text(task.status, style="magenta" if task.status == "open" else "green"),
            Text(fmt_age(task.created_at, now), style=MUTED),
        )
    if not tasks:
        table.add_row(Text("no open tasks", style=MUTED), "", "", "", "", "")
    return table


def connectors_table(results: Iterable[tuple[str, str, HealthStatus]]) -> Table:
    table = _table("", "CONNECTOR", "TYPE", "DETAIL", "LATENCY")
    for name, kind, status in results:
        table.add_row(
            Text("●", style="green") if status.ok else Text("✕", style="red"),
            Text(name, style="bold"),
            Text(kind, style=MUTED),
            clip(status.detail, 80),
            Text(fmt_duration(status.latency_ms / 1000) if status.ok else "", style=MUTED),
        )
    return table


def issues_view(title: str, issues: Sequence[SpecIssue]) -> Group:
    lines = [Text("✕ ", style="red") + Text(title, style="bold")]
    for issue in issues:
        line = Text("  ")
        line.append(issue.path, style=ACCENT)
        line.append(f"  {issue.message}")
        lines.append(line)
    return Group(*lines)


def _step_label(step: Any, index: int) -> Text:
    label = Text()
    label.append(f"{index:>2}  ", style=MUTED)
    label.append(step.id, style="bold")
    label.append(f"  {step.type}", style=MUTED)
    if step.needs:
        label.append(f"  ← {', '.join(step.needs)}", style=MUTED)
    if step.when:
        label.append(f"  when {' '.join(step.when.split())}", style="yellow")
    if step.retry.max:
        label.append(f"  ↻ {step.retry.max}", style=MUTED)
    if step.on_failure:
        label.append(f"  ⤳ {step.on_failure.fallback}", style="magenta")
    return label


def dag_view(workflow: Workflow) -> Tree:
    title = Text(f"{workflow.name} v{workflow.version}", style="bold")
    if workflow.description:
        title.append(f"  {clip(workflow.description, 90)}", style=MUTED)
    tree = Tree(title, guide_style=MUTED)
    for index, step in enumerate(workflow.steps, start=1):
        tree.add(_step_label(step, index))
    if workflow.fallbacks:
        users = {s.on_failure.fallback: s.id for s in workflow.steps if s.on_failure}
        branch = tree.add(Text("fallbacks", style="magenta"))
        for step in workflow.fallbacks:
            label = Text()
            label.append(step.id, style="bold")
            label.append(f"  {step.type}", style=MUTED)
            if step.id in users:
                label.append(f"  ⤳ for {users[step.id]}", style="magenta")
            branch.add(label)
    return tree


def trace_table(spans: Sequence[Span], *, now: float, width: int = 36) -> Table:
    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2, 0, 0))
    table.add_column()
    table.add_column()
    table.add_column(justify="right")
    if not spans:
        table.add_row(Text("no spans recorded", style=MUTED), "", "")
        return table
    t0 = min(span.start for span in spans)
    t1 = max(span.end if span.end is not None else now for span in spans)
    total = max(t1 - t0, 1e-9)
    for span in spans:
        end = span.end if span.end is not None else now
        offset = min(int((span.start - t0) / total * width), width - 1)
        length = min(max(1, round((end - span.start) / total * width)), width - offset)
        bar = Text(
            " " * offset + "━" * length + " " * (width - offset - length),
            style=SPAN_STYLES.get(span.status, ACCENT),
        )
        label = Text(
            ("  └ " if span.parent_id else "") + span.label,
            style=MUTED if span.parent_id else "bold",
        )
        table.add_row(label, bar, Text(fmt_duration(end - span.start), style=MUTED))
    return table


def scenario_line(title: str, run: RunRecord) -> Text:
    glyph, style = RUN_GLYPHS.get(run.status.value, ("·", ""))
    decision = run.output.get("decision") if isinstance(run.output, dict) else None
    text = Text()
    text.append(f" {glyph} ", style=style)
    text.append(f"{title:<37}")  # keeps the longest demo line within 80 columns
    text.append(f"{run.run_id}  ", style=ACCENT)
    text.append(f"{run.status.value.replace('_', ' '):<18}", style=style)
    if decision:
        text.append(str(decision), style="bold")
    return text


def describe_check(check: Mapping[str, Any]) -> str:
    """One line for a failed check: what the case expected and what the run did."""
    if check["kind"] == "assert":
        return f"assert {check['target']} → {check.get('actual') or 'false'}"
    expected = json.dumps(check["expected"], ensure_ascii=False, default=str)
    if check.get("missing"):
        return f"{check['target']}: expected {expected} · missing"
    actual = json.dumps(check["actual"], ensure_ascii=False, default=str)
    return f"{check['target']}: expected {expected} · got {actual}"


def eval_targets(targets: Sequence[Target]) -> Group:
    """What the eval's connectors point at; a yellow line for each real system it will touch."""
    lines: list[Text] = []
    for target in targets:
        line = Text(f"   {target.connector:<12}", style=MUTED)
        line.append(target.where, style=None if target.isolated else "yellow")
        lines.append(line)
    for target in targets:
        if target.warning:
            lines.append(Text("! ", style="yellow") + Text(target.warning, style="yellow"))
    return Group(*lines)


def eval_case_line(result: EvalResultRecord) -> Group:
    glyph, style = ("●", "green") if result.passed else ("✕", "red")
    line = Text()
    line.append(f" {glyph} ", style=style)
    line.append(f"{result.case_id:<44}")
    line.append(f"{result.run_id or '—':<12}", style=ACCENT if result.run_id else MUTED)
    line.append(f"{fmt_duration(result.duration_s):>9}", style=MUTED)
    if result.regression:
        line.append("  ↓ regression", style="bold red")
    lines: list[Text] = [line]
    if result.error:
        lines.append(Text(f"     {result.error}", style="red"))
    for check in result.checks:
        if not check["passed"]:
            lines.append(Text(f"     {describe_check(check)}", style="red"))
    return Group(*lines)


def eval_summary(
    record: EvalRunRecord,
    baseline: EvalRunRecord | None,
    regressed: Sequence[str],
    min_pass: float,
) -> Group:
    rate = record.passed / record.total if record.total else 0.0
    ok = rate + 1e-9 >= min_pass
    head = Text()
    head.append(" ● " if ok else " ✕ ", style="green" if ok else "red")
    head.append(f"{record.passed}/{record.total} passed ({rate:.0%})", style="bold")
    head.append(f"  · minimum {min_pass:.0%}", style=MUTED)
    head.append(f"  · cost {fmt_cost(record.cost_usd) or '$0'}", style=MUTED)
    if record.ai_first_try:
        head.append(
            f"  · AI first try {record.ai_first_ok}/{record.ai_first_try}"
            f" · repairs {record.ai_repairs}",
            style=MUTED,
        )
    lines = [head]
    if baseline is None:
        lines.append(Text("   first run of this suite: nothing to compare with", style=MUTED))
    else:
        compare = Text(
            f"   vs {baseline.id}: {baseline.passed}/{baseline.total} → "
            f"{record.passed}/{record.total}",
            style=MUTED,
        )
        if regressed:
            compare.append(
                f" · {len(regressed)} regression(s): {', '.join(regressed)}", style="red"
            )
        else:
            compare.append(" · no regressions", style=MUTED)
        lines.append(compare)
    lines.append(Text(f"   eval {record.id} · dashboard: cerebellum ui → /evals", style=MUTED))
    return Group(*lines)
