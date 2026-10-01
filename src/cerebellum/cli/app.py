"""Cerebellum command-line interface."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import shutil
import sqlite3
import threading
import time
import webbrowser
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Annotated, Any, NoReturn

import click
import typer
import uvicorn
from dotenv import load_dotenv
from rich.console import Console, RenderableType
from rich.live import Live
from rich.rule import Rule
from rich.text import Text
from typer.core import TyperGroup
from uvicorn.config import STARTUP_FAILURE

from cerebellum import __version__
from cerebellum.ai import AnthropicProvider, select_provider
from cerebellum.ai.base import AIProvider
from cerebellum.ai.pricing import Pricing
from cerebellum.authoring import DRAFT_HEADER, DraftError, draft_workflow
from cerebellum.cli import render
from cerebellum.cli.demo import run_scenarios
from cerebellum.config import (
    DEFAULT_EVAL_KEEP,
    DEFAULT_EVAL_MIN_PASS,
    MAX_PORT,
    MIN_PORT,
    Settings,
    has_anthropic_credentials,
)
from cerebellum.connectors import ConnectorEnv, HealthStatus, create_connector
from cerebellum.connectors.postgres import sandbox_db_path
from cerebellum.errors import CerebellumError, ConfigError, LeaseUnavailable, SpecError, StepError
from cerebellum.evals import EvalRunner, LoadedSuite, load_suite
from cerebellum.evals.lock import EvalBusy, EvalLock
from cerebellum.evals.prune import prune_eval_homes
from cerebellum.evals.targets import connector_targets
from cerebellum.runtime.engine import Engine, load_run_workflow
from cerebellum.runtime.states import RunStatus
from cerebellum.runtime.store import EventRecord, RunRecord, Store
from cerebellum.runtime.trace import build_spans
from cerebellum.sandbox.payments import FailMode, PaymentsState, create_payments_app
from cerebellum.sandbox.server import SandboxHandle, sandbox_url, start_sandbox
from cerebellum.server import create_app
from cerebellum.server.app import dashboard_server, loopback_host_names
from cerebellum.spec import load_workflow
from cerebellum.spec.models import Workflow
from cerebellum.templates import template_path


class _CerebellumGroup(TyperGroup):
    """Runs every command. An unusable configured file (ConfigError) or Cerebellum database
    (sqlite3.Error) ends the command with a message and exit code 1 instead of a traceback."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except ConfigError as exc:
            _fail(str(exc))
        except sqlite3.Error as exc:
            _fail(
                f"cannot use the Cerebellum database {Settings.from_env().db_path}: {exc} "
                "(check CEREBELLUM_HOME)"
            )


app = typer.Typer(
    name="cerebellum",
    help="Reliable, observable, recoverable business workflows.",
    no_args_is_help=True,
    add_completion=False,
    cls=_CerebellumGroup,
)
tasks_app = typer.Typer(help="Manual task inbox (fallback hand-offs).")
connectors_app = typer.Typer(help="Connector utilities.", no_args_is_help=True)
evals_app = typer.Typer(help="Eval run housekeeping.", no_args_is_help=True)
app.add_typer(tasks_app, name="tasks")
app.add_typer(connectors_app, name="connectors")
app.add_typer(evals_app, name="evals")

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

EXIT_OK, EXIT_FAILED, EXIT_INVALID, EXIT_WAITING = 0, 1, 2, 3
# The variable the packaged refund template reads for its payments API base URL.
SANDBOX_URL_ENV = "PAYMENTS_URL"
# The refund template's database connector; `cerebellum demo` resets its sandbox database.
DEMO_SANDBOX_CONNECTOR = "orders_db"
DEFAULT_USER = os.environ.get("USER") or os.environ.get("USERNAME") or "cli"
REFUND_TEMPLATE_FILES = (
    "workflow.yaml",
    "seed.sql",
    "evals.yaml",
    "inputs/small.json",
    "inputs/large.json",
    "inputs/flaky.json",
    "inputs/outage.json",
    "inputs/fraud.json",
)
ENV_EXAMPLE = """\
# Cerebellum configuration. Copy to .env and adjust.
# Real Claude calls (otherwise the offline mock AI is used):
# ANTHROPIC_API_KEY=
# CEREBELLUM_MODEL=claude-opus-5-5
# CEREBELLUM_HOME=.cerebellum
# Real PostgreSQL (default: SQLite sandbox seeded from seed.sql):
# ORDERS_DSN=postgresql://cerebellum:cerebellum@localhost:5432/cerebellum
# Payments API (default: the local sandbox started with --sandbox):
# PAYMENTS_URL=http://127.0.0.1:8787
# PAYMENTS_TOKEN=sandbox-token
"""


def main() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    app()


# ── helpers ──────────────────────────────────────────────────────────────────


def _settings() -> Settings:
    settings = Settings.from_env()
    try:
        settings.ensure_home()
    except OSError as exc:
        _fail(f"cannot create CEREBELLUM_HOME {settings.home}: {exc.strerror or exc}")
    return settings


def _fail(message: str, code: int = EXIT_FAILED) -> NoReturn:
    err_console.print(Text("✕ ", style="red") + Text(message))
    raise typer.Exit(code)


def _invalid(title: str, exc: SpecError) -> NoReturn:
    err_console.print(render.issues_view(title, exc.issues))
    raise typer.Exit(EXIT_INVALID)


def _load(path: Path, env: dict[str, str] | None = None) -> Workflow:
    try:
        return load_workflow(path, env=env)
    except SpecError as exc:
        _invalid(f"{path} is invalid", exc)


def _load_suite(path: Path, env: dict[str, str] | None = None) -> LoadedSuite:
    try:
        return load_suite(path, env=env)
    except SpecError as exc:
        _invalid(f"{path} is invalid", exc)


def _fail_mode_setter(handle: SandboxHandle | None) -> Callable[[str], Awaitable[None]] | None:
    if handle is None:
        return None

    async def set_fail_mode(mode: str) -> None:
        await asyncio.to_thread(handle.set_fail_mode, mode)

    return set_fail_mode


def _get_run(store: Store, run_id: str) -> RunRecord:
    try:
        return store.get_run(run_id)
    except CerebellumError as exc:
        _fail(str(exc))


def _run_workflow(store: Store, run: RunRecord, *, display_only: bool = False) -> Workflow:
    """The run's workflow snapshot. Display-only callers open no connector, so they tolerate
    connector variables that are unset in this shell; driving the run needs their values."""
    try:
        return load_run_workflow(store, run, require_env=not display_only)
    except SpecError as exc:
        _invalid("the run's workflow snapshot is invalid in this environment", exc)


def _mode(run: RunRecord) -> str:
    return "mock AI" if run.mock else "Claude API"


def _run_provider(settings: Settings, run: RunRecord) -> AIProvider:
    """The provider that continues `run`: mock runs stay on mock AI, and a run started with the
    Claude API never continues on it."""
    provider = select_provider(settings, force_mock=run.mock).provider
    if provider.mock and not run.mock:
        fix = (
            "unset CEREBELLUM_MOCK"
            if settings.force_mock
            else "set ANTHROPIC_API_KEY or run `ant auth login`"
        )
        _fail(
            f"run {run.run_id} was started with the Claude API and cannot continue on mock AI; "
            f"{fix}, then try again"
        )
    return provider


def _exit_code(status: RunStatus) -> int:
    if status is RunStatus.WAITING_APPROVAL:
        return EXIT_WAITING
    if status in (RunStatus.FAILED, RunStatus.REJECTED):
        return EXIT_FAILED
    return EXIT_OK


def _start_sandbox(settings: Settings, fail: str) -> SandboxHandle:
    try:
        return start_sandbox(settings.sandbox_host, settings.sandbox_port, fail)
    except ValueError as exc:
        _fail(str(exc), EXIT_INVALID)
    except CerebellumError as exc:
        _fail(str(exc))


def _draft_provider(settings: Settings) -> AIProvider:
    """`new` needs Claude: a workflow cannot be drafted offline, so there is no mock fallback."""
    if settings.force_mock:
        _fail("cerebellum new needs the Claude API; unset CEREBELLUM_MOCK")
    if not has_anthropic_credentials():
        _fail(
            "cerebellum new needs Anthropic credentials: "
            "set ANTHROPIC_API_KEY or run `ant auth login`"
        )
    return AnthropicProvider(pricing=Pricing.load(settings.pricing_file))


def _check_output(output: Path, *, force: bool) -> None:
    """Refuse an output path `new` could not write to (a directory, an existing file without
    --force, or a parent it cannot create or write in)."""
    if output.is_dir():
        _fail(
            f"{output} is a directory; pass a file path such as {output / 'workflow.yaml'}",
            EXIT_INVALID,
        )
    if output.exists():
        if not force:
            _fail(f"{output} already exists; pass --force to overwrite it", EXIT_INVALID)
        if not os.access(output, os.W_OK):
            _fail(f"{output} is not writable", EXIT_INVALID)
        return
    existing = output.absolute().parent  # the nearest existing directory is where it is created
    while not existing.exists():
        existing = existing.parent
    if not existing.is_dir():
        _fail(f"cannot create {output}: {existing} is not a directory", EXIT_INVALID)
    if not os.access(existing, os.W_OK | os.X_OK):
        _fail(f"cannot create {output}: {existing} is not writable", EXIT_INVALID)


@contextlib.contextmanager
def _sandbox(settings: Settings, fail: str, *, enabled: bool) -> Iterator[SandboxHandle | None]:
    """Run the sandbox payments API for one command. Unless the user set SANDBOX_URL_ENV,
    workflows that read it target this sandbox, which may be on a non-default port."""
    if not enabled:
        yield None
        return
    handle = _start_sandbox(settings, fail)
    configured = SANDBOX_URL_ENV in os.environ
    if not configured:
        os.environ[SANDBOX_URL_ENV] = handle.url
    try:
        yield handle
    finally:
        if not configured:
            os.environ.pop(SANDBOX_URL_ENV, None)
        handle.stop()


@contextlib.contextmanager
def _eval_lock(settings: Settings) -> Iterator[None]:
    """Hold this home's eval lock for one command, or stop at once if another eval holds it."""
    lock = EvalLock(settings.home)
    try:
        lock.acquire()
    except EvalBusy as exc:
        _fail(str(exc))
    try:
        yield
    finally:
        lock.release()


def _parse_input(raw: str) -> dict[str, Any]:
    text = raw
    if raw.startswith("@"):
        path = Path(raw[1:])
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            _fail(f"cannot read input file {path}: {exc.strerror}", EXIT_INVALID)
        except UnicodeDecodeError as exc:
            _fail(f"cannot read input file {path}: not UTF-8 text (byte {exc.start})", EXIT_INVALID)
    try:
        # NaN, Infinity and numbers out of float range (1e400) are refused, as for --param: a
        # run's input must stay serialisable as JSON.
        data = json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)
    except json.JSONDecodeError as exc:
        _fail(
            f"input is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})",
            EXIT_INVALID,
        )
    except ValueError as exc:  # a rejected constant or float, an over-long integer
        _fail(f"input is not valid JSON: {exc}", EXIT_INVALID)
    if not isinstance(data, dict):
        _fail("input must be a JSON object", EXIT_INVALID)
    return data


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not a JSON number")


def _finite_float(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(f"{text} is out of range")
    return number


def _param_value(text: str) -> Any:
    """A JSON value (number, true/false/null, "quoted string", object, array); anything else
    is kept as the literal string, so `no`, `on` and `010` stay text. So do NaN, Infinity and
    numbers out of float range (1e400): a run's params must stay serialisable as JSON."""
    try:
        return json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)
    except ValueError:  # JSONDecodeError, a rejected constant or float, an over-long integer
        return text


def _parse_params(items: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            _fail(f"invalid --param {item!r}; use key=value", EXIT_INVALID)
        params[key.strip()] = _param_value(value)
    return params


def _drive_live(
    store: Store,
    workflow: Workflow,
    mode: str,
    action: Callable[[], Awaitable[RunRecord]],
    *,
    run_id: str | None = None,
) -> RunRecord:
    """Run `action` while redrawing the step table in place (terminal only)."""
    if not console.is_terminal:
        return asyncio.run(action())
    current: dict[str, str | None] = {"run_id": run_id}

    def on_event(event: EventRecord) -> None:
        if current["run_id"] is None:
            current["run_id"] = event.run_id

    def view() -> RenderableType:
        rid = current["run_id"]
        if rid is None:
            return Text("  starting…", style=render.MUTED)
        return render.run_view(store.get_run(rid), workflow, store.get_steps(rid), mode=mode)

    unsubscribe = store.add_listener(on_event)
    try:
        with Live(get_renderable=view, console=console, refresh_per_second=12, transient=True):
            return asyncio.run(action())
    finally:
        unsubscribe()


def _show_run(store: Store, run: RunRecord, workflow: Workflow, mode: str) -> None:
    steps = store.get_steps(run.run_id)
    console.print(render.run_view(run, workflow, steps, mode=mode, stale=store.is_stale(run)))
    _print_outcome(store, run)


def _print_outcome(store: Store, run: RunRecord) -> None:
    rid = run.run_id
    if run.status is RunStatus.WAITING_APPROVAL:
        for approval in store.list_approvals(run_id=rid, status="pending"):
            console.print(
                Text("⏸ ", style="yellow") + Text(f"awaiting approval · {approval.title}")
            )
            console.print(
                Text(
                    f"  cerebellum approve {rid} {approval.step_id} --by <you>", style=render.ACCENT
                )
            )
            console.print(
                Text(
                    f"  cerebellum reject {rid} {approval.step_id} --by <you> -m <why>",
                    style=render.MUTED,
                )
            )
    elif run.status is RunStatus.FAILED:
        console.print(Text("✕ ", style="red") + Text(run.error or "run failed"))
        console.print(Text(f"  fix the cause, then: cerebellum resume {rid}", style=render.MUTED))
    elif run.status is RunStatus.REJECTED:
        expired = {
            event.data.get("approval_id")
            for event in store.get_events(rid)
            if event.type == "approval.expired"
        }
        rejected = store.list_approvals(run_id=rid, status="rejected")
        if rejected and all(approval.id in expired for approval in rejected):
            steps = ", ".join(approval.step_id for approval in rejected)
            console.print(
                Text(f"✕ approval timed out · {steps} rejected by on_timeout", style="red")
            )
        else:
            console.print(Text("✕ rejected by a human approver", style="red"))
    elif run.status is RunStatus.NEEDS_ATTENTION:
        open_tasks = store.list_tasks(run_id=rid, status="open")
        console.print(
            Text("⤳ ", style="magenta")
            + Text(f"recovered via fallback · {len(open_tasks)} manual task(s) open")
        )
        console.print(Text("  cerebellum tasks", style=render.ACCENT))
    elif run.status is RunStatus.RUNNING and store.is_stale(run):
        console.print(Text("◐ ", style="red") + Text("the process driving this run stopped"))
        console.print(Text(f"  cerebellum resume {rid}", style=render.ACCENT))
    if run.output is not None:
        console.print(render.output_view(run.output))


# ── commands ─────────────────────────────────────────────────────────────────


def _version(value: bool) -> None:
    if value:
        console.print(f"cerebellum {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    """Reliable, observable, recoverable business workflows."""


@app.command()
def init(
    directory: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
) -> None:
    """Scaffold the refund example workflow and a .env.example."""
    source = template_path("refund")
    target = directory / "workflows" / "refund"
    for relative in REFUND_TEMPLATE_FILES:
        destination = target / relative
        if destination.exists():
            console.print(Text(f"  = {destination} (exists, kept)", style=render.MUTED))
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
        console.print(Text(f"  + {destination}", style="green"))
    env_file = directory / ".env.example"
    if env_file.exists():
        console.print(Text(f"  = {env_file} (exists, kept)", style=render.MUTED))
    else:
        env_file.write_text(ENV_EXAMPLE, encoding="utf-8")
        console.print(Text(f"  + {env_file}", style="green"))
    workflow = target / "workflow.yaml"
    console.print()
    console.print(Text("next", style="bold"))
    console.print(Text(f"  cerebellum validate {workflow}", style=render.ACCENT))
    console.print(
        Text(
            f"  cerebellum run {workflow} -i @{target / 'inputs' / 'small.json'} --sandbox",
            style=render.ACCENT,
        )
    )
    console.print(Text(f"  cerebellum eval {target / 'evals.yaml'} --mock", style=render.ACCENT))


@app.command()
def validate(workflow: Annotated[Path, typer.Argument(help="Workflow YAML file.")]) -> None:
    """Validate a workflow definition."""
    wf = _load(workflow)
    line = Text("● ", style="green") + Text(f"{wf.name} v{wf.version} is valid", style="bold")
    line.append(
        f"  {len(wf.steps)} steps · {len(wf.fallbacks)} fallbacks · "
        f"{len(wf.connectors)} connectors",
        style=render.MUTED,
    )
    console.print(line)


@app.command()
def show(workflow: Annotated[Path, typer.Argument(help="Workflow YAML file.")]) -> None:
    """Print a workflow's steps, dependencies, conditions and fallbacks."""
    console.print(render.dag_view(_load(workflow)))


@app.command()
def new(
    description: Annotated[str, typer.Argument(help="The business process, in plain language.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Where to write the YAML draft.")],
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Draft a workflow from a description with Claude; review the draft before running it."""
    settings = _settings()
    _check_output(output, force=force)  # before the provider is created and Claude is paid
    provider = _draft_provider(settings)
    console.print(render.header("new workflow", f"drafting with Claude API ({settings.model})"))
    try:
        draft = asyncio.run(
            draft_workflow(
                provider, description, model=settings.model, base_dir=output.resolve().parent
            )
        )
    except DraftError as exc:
        err_console.print(render.issues_view(f"{exc}; nothing was written", exc.issues))
        raise typer.Exit(EXIT_INVALID) from None
    except CerebellumError as exc:
        _fail(str(exc))
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(DRAFT_HEADER + draft.yaml.rstrip() + "\n", encoding="utf-8")
    except OSError as exc:  # changed since the check above
        _fail(f"cannot write {output}: {exc.strerror or exc}")
    wf = draft.workflow
    line = Text("● ", style="green") + Text(str(output), style="bold")
    line.append(
        f"  {wf.name} · {len(wf.steps)} steps · {len(wf.fallbacks)} fallbacks · "
        f"{draft.attempts} attempt(s) · {render.fmt_cost(draft.cost_usd) or '$0'}",
        style=render.MUTED,
    )
    console.print(line)
    if draft.summary:
        console.print(Text(f"  {draft.summary}", style=render.MUTED))
    console.print(Text("  AI-generated draft: review it before running", style="yellow"))
    console.print(Text(f"  cerebellum show {output}", style=render.ACCENT))


@app.command()
def run(
    workflow: Annotated[Path, typer.Argument(help="Workflow YAML file.")],
    input: Annotated[
        str, typer.Option("--input", "-i", help="Run input: JSON text or @path/to/file.json.")
    ] = "{}",
    param: Annotated[
        list[str] | None,
        typer.Option(
            "--param",
            "-p",
            help="Override a workflow param (key=value; a JSON value, otherwise text).",
        ),
    ] = None,
    mock: Annotated[bool, typer.Option("--mock", help="Use the offline mock AI provider.")] = False,
    sandbox: Annotated[
        bool,
        typer.Option("--sandbox", help="Start the local sandbox payments API for this command."),
    ] = False,
    sandbox_fail: Annotated[
        str,
        typer.Option(
            "--sandbox-fail", help="Sandbox fault injection: never | always | first:N | rate:P."
        ),
    ] = "never",
) -> None:
    """Start a workflow run."""
    settings = _settings()
    data = _parse_input(input)
    params = _parse_params(param or [])
    choice = select_provider(settings, force_mock=mock)
    # Load before starting anything, with the environment the run will have: unless the user
    # set SANDBOX_URL_ENV, the sandbox sets it to its own URL (connectors may read it).
    env = dict(os.environ)
    if sandbox and SANDBOX_URL_ENV not in env:
        env[SANDBOX_URL_ENV] = sandbox_url(settings.sandbox_host, settings.sandbox_port)
    wf = _load(workflow, env)
    with _sandbox(settings, sandbox_fail, enabled=sandbox):
        with Store(settings.db_path) as store:
            engine = Engine(store, settings, choice.provider)
            try:
                record = _drive_live(
                    store, wf, choice.reason, lambda: engine.start(wf, data, params)
                )
            except SpecError as exc:
                _invalid("run input is invalid", exc)
            except CerebellumError as exc:
                _fail(str(exc))
            _show_run(store, record, wf, choice.reason)
    raise typer.Exit(_exit_code(record.status))


@app.command()
def runs(
    status: Annotated[
        str | None, typer.Option("--status", "-s", help="Filter by run status.")
    ] = None,
    limit: Annotated[int, typer.Option(help="Maximum number of runs.")] = 20,
    evals: Annotated[
        bool, typer.Option("--evals", help="Include the runs eval suites made.")
    ] = False,
) -> None:
    """List recent runs (eval runs only with --evals)."""
    wanted: RunStatus | None = None
    if status:
        try:
            wanted = RunStatus(status)
        except ValueError:
            _fail(
                f"unknown status {status!r}; use one of: {', '.join(s.value for s in RunStatus)}",
                EXIT_INVALID,
            )
    settings = _settings()
    with Store(settings.db_path) as store:
        records = store.list_runs(status=wanted, limit=limit, include_evals=evals)
        stale = {r.run_id for r in records if store.is_stale(r)}
        console.print(render.runs_table(records, now=time.time(), stale=stale))


@app.command()
def status(run_id: Annotated[str, typer.Argument(help="Run id.")]) -> None:
    """Show a run: steps, approvals, manual tasks and output."""
    settings = _settings()
    with Store(settings.db_path) as store:
        run = _get_run(store, run_id)
        workflow = _run_workflow(store, run, display_only=True)
        steps = store.get_steps(run_id)
        console.print(
            render.run_view(run, workflow, steps, mode=_mode(run), stale=store.is_stale(run))
        )
        now = time.time()
        approvals = store.list_approvals(run_id=run_id)
        if approvals:
            console.print()
            console.print(render.approvals_table(approvals, now=now))
        tasks = store.list_tasks(run_id=run_id)
        if tasks:
            console.print()
            console.print(render.tasks_table(tasks, now=now))
        _print_outcome(store, run)


@app.command()
def trace(run_id: Annotated[str, typer.Argument(help="Run id.")]) -> None:
    """Show a run's trace as a span waterfall."""
    settings = _settings()
    with Store(settings.db_path) as store:
        run = _get_run(store, run_id)
        console.print(render.header(f"trace · {run.workflow_name}", f"run {run_id}"))
        console.print(render.trace_table(build_spans(store.get_events(run_id)), now=time.time()))


@app.command()
def approvals(
    show_all: Annotated[bool, typer.Option("--all", help="Include decided approvals.")] = False,
) -> None:
    """List pending human approvals."""
    settings = _settings()
    with Store(settings.db_path) as store:
        items = store.list_approvals(status=None if show_all else "pending")
        console.print(render.approvals_table(items, now=time.time()))


def _decide(
    run_id: str,
    step: str | None,
    *,
    approved: bool,
    by: str,
    comment: str,
    resume: bool,
    sandbox: bool,
) -> None:
    settings = _settings()
    with _sandbox(settings, "never", enabled=sandbox and resume):
        with Store(settings.db_path) as store:
            run = _get_run(store, run_id)
            # Recording a decision without resuming opens no connector: the snapshot is only
            # displayed. The engine re-parses it with the real values when it resumes the run.
            workflow = _run_workflow(store, run, display_only=not resume)
            # Recording a decision without resuming calls no AI, so any provider will do.
            provider = (
                _run_provider(settings, run)
                if resume
                else select_provider(settings, force_mock=run.mock).provider
            )
            engine = Engine(store, settings, provider)
            try:
                record = _drive_live(
                    store,
                    workflow,
                    _mode(run),
                    lambda: engine.decide(
                        run_id, step, approved=approved, by=by, comment=comment, resume=resume
                    ),
                    run_id=run_id,
                )
            except CerebellumError as exc:
                _fail(str(exc))
            verb = "approved" if approved else "rejected"
            line = Text("● " if approved else "✕ ", style="green" if approved else "red")
            line.append(f"{verb} by {by}")
            if comment:
                line.append(f" · {comment}", style=render.MUTED)
            console.print(line)
            _show_run(store, record, workflow, _mode(run))
    raise typer.Exit(_exit_code(record.status))


StepArg = Annotated[
    str | None, typer.Argument(help="Approval step id (optional when only one is pending).")
]
ByOption = Annotated[str, typer.Option("--by", help="Who is deciding.")]
CommentOption = Annotated[str, typer.Option("--comment", "-m", help="Decision note.")]
NoResumeOption = Annotated[
    bool, typer.Option("--no-resume", help="Record the decision without continuing the run.")
]
SandboxOption = Annotated[
    bool, typer.Option("--sandbox", help="Start the local sandbox payments API while resuming.")
]


@app.command()
def approve(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    step: StepArg = None,
    by: ByOption = DEFAULT_USER,
    comment: CommentOption = "",
    no_resume: NoResumeOption = False,
    sandbox: SandboxOption = False,
) -> None:
    """Approve a pending human approval and resume the run."""
    _decide(
        run_id, step, approved=True, by=by, comment=comment, resume=not no_resume, sandbox=sandbox
    )


@app.command()
def reject(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    step: StepArg = None,
    by: ByOption = DEFAULT_USER,
    comment: CommentOption = "",
    no_resume: NoResumeOption = False,
    sandbox: SandboxOption = False,
) -> None:
    """Reject a pending human approval and finish the run."""
    _decide(
        run_id, step, approved=False, by=by, comment=comment, resume=not no_resume, sandbox=sandbox
    )


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    sandbox: SandboxOption = False,
    sandbox_fail: Annotated[
        str, typer.Option("--sandbox-fail", help="Sandbox fault injection.")
    ] = "never",
) -> None:
    """Resume a run after an approval, a crash or a failure."""
    settings = _settings()
    with _sandbox(settings, sandbox_fail, enabled=sandbox):
        with Store(settings.db_path) as store:
            run = _get_run(store, run_id)
            workflow = _run_workflow(store, run)
            engine = Engine(store, settings, _run_provider(settings, run))
            try:
                record = _drive_live(
                    store, workflow, _mode(run), lambda: engine.resume(run_id), run_id=run_id
                )
            except LeaseUnavailable as exc:
                _fail(f"{exc}; try again once it finishes")
            except CerebellumError as exc:
                _fail(str(exc))
            _show_run(store, record, workflow, _mode(run))
    raise typer.Exit(_exit_code(record.status))


@tasks_app.callback(invoke_without_command=True)
def tasks_list(
    ctx: typer.Context,
    show_all: Annotated[bool, typer.Option("--all", help="Include resolved tasks.")] = False,
) -> None:
    """List manual tasks (open ones by default)."""
    if ctx.invoked_subcommand is not None:
        return
    settings = _settings()
    with Store(settings.db_path) as store:
        items = store.list_tasks(status=None if show_all else "open")
        console.print(render.tasks_table(items, now=time.time()))


@tasks_app.command("resolve")
def tasks_resolve(
    task_id: Annotated[str, typer.Argument(help="Task id.")],
    by: ByOption = DEFAULT_USER,
    note: Annotated[str, typer.Option("--note", "-m", help="Resolution note.")] = "",
) -> None:
    """Mark a manual task as resolved."""
    settings = _settings()
    with Store(settings.db_path) as store:
        try:
            task = store.resolve_task(task_id, by=by, note=note)
        except CerebellumError as exc:
            _fail(str(exc))
    console.print(Text("● ", style="green") + Text(f"{task.id} resolved by {by}"))


@connectors_app.command("check")
def connectors_check(
    workflow: Annotated[Path, typer.Argument(help="Workflow YAML file.")],
) -> None:
    """Open every connector of a workflow and run its health check."""
    settings = _settings()
    wf = _load(workflow)

    async def check_all() -> list[tuple[str, str, HealthStatus]]:
        env = ConnectorEnv(home=settings.home, base_dir=Path(wf.base_dir))
        results = []
        for name, spec in wf.connectors.items():
            connector = create_connector(name, spec, env)
            try:
                await connector.open()
                health = await connector.health()
            except StepError as exc:
                health = HealthStatus(False, str(exc))
            finally:
                await connector.close()
            results.append((name, spec.type, health))
        return results

    results = asyncio.run(check_all())
    console.print(render.connectors_table(results))
    raise typer.Exit(EXIT_OK if all(health.ok for _, _, health in results) else EXIT_FAILED)


def _serve(serve: Callable[[], None], what: str, host: str, port: int) -> None:
    """Run a uvicorn server in the foreground. When it cannot start (a busy port, a host that
    does not resolve) uvicorn logs why and exits 3, which here means a run awaits approval:
    exit 1 with a line saying what did not start instead."""
    try:
        serve()
    except SystemExit as exc:
        if exc.code == STARTUP_FAILURE:
            _fail(f"the {what} could not start on {host}:{port} (see the error above)")
        raise


def _port_option(help: str) -> Any:
    """A --port flag: the range CEREBELLUM_*_PORT accepts, so 0 ("any free port") and
    out-of-range numbers are invalid options (exit 2) rather than a default or a bind error."""
    return typer.Option(min=MIN_PORT, max=MAX_PORT, help=help)


@app.command()
def sandbox(
    host: Annotated[str | None, typer.Option(help="Bind host.")] = None,
    port: Annotated[int | None, _port_option("Bind port.")] = None,
    fail: Annotated[
        str, typer.Option(help="Fault injection: never | always | first:N | rate:P.")
    ] = "never",
) -> None:
    """Run the sandbox payments API in the foreground (Ctrl-C to stop)."""
    settings = _settings()
    try:
        mode = FailMode.parse(fail)
    except ValueError as exc:
        _fail(str(exc), EXIT_INVALID)
    bind_host = host or settings.sandbox_host
    bind_port = settings.sandbox_port if port is None else port
    console.print(
        render.header("sandbox payments API", f"http://{bind_host}:{bind_port} · fail mode {mode}")
    )
    _serve(
        lambda: uvicorn.run(
            create_payments_app(PaymentsState(mode)),
            host=bind_host,
            port=bind_port,
            log_level="warning",
        ),
        "sandbox payments API",
        bind_host,
        bind_port,
    )


@app.command()
def ui(
    host: Annotated[str | None, typer.Option(help="Bind host (default 127.0.0.1).")] = None,
    port: Annotated[int | None, _port_option("Bind port (default 7400).")] = None,
    workflows: Annotated[
        Path, typer.Option(help="Directory scanned for workflow YAML (3 levels deep).")
    ] = Path("."),
    no_sandbox: Annotated[
        bool, typer.Option("--no-sandbox", help="Do not start the sandbox payments API.")
    ] = False,
    mock: Annotated[bool, typer.Option("--mock", help="Use the offline mock AI provider.")] = False,
    open_browser: Annotated[
        bool, typer.Option("--open", help="Open the dashboard in a browser.")
    ] = False,
) -> None:
    """Serve the dashboard: live runs, traces, approvals, tasks and workflows."""
    settings = _settings()
    bind_host = host or settings.ui_host
    bind_port = settings.ui_port if port is None else port
    # Bound to this machine only (under any spelling of loopback): answer only requests
    # addressed to it, against DNS rebinding. Otherwise anyone who can reach it may use it.
    allowed_hosts = loopback_host_names(bind_host)
    if allowed_hosts is None:
        err_console.print(
            Text("! ", style="yellow")
            + Text(
                f"the dashboard has no authentication; anyone who can reach "
                f"{bind_host}:{bind_port} can approve runs"
            )
        )
    choice = select_provider(settings, force_mock=mock)
    # The server opens the database while it starts, where a failure is only logged: check here.
    Store(settings.db_path).close()
    shown = "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host
    url = f"http://[{shown}]:{bind_port}" if ":" in shown else f"http://{shown}:{bind_port}"
    with _sandbox(settings, "never", enabled=not no_sandbox):
        dashboard = create_app(
            settings,
            provider=choice.provider,
            mode=choice.reason,
            mock_requested=choice.mock_requested,
            workflows_dir=workflows,
            allowed_hosts=allowed_hosts,
        )
        console.print(render.header("dashboard", f"{url} · {choice.reason}"))
        console.print(Text("  Ctrl-C to stop", style=render.MUTED))
        if open_browser:
            threading.Timer(1.0, webbrowser.open, args=(url,)).start()
        _serve(
            dashboard_server(dashboard, host=bind_host, port=bind_port).run,
            "dashboard",
            bind_host,
            bind_port,
        )


@app.command("eval")
def eval_suite(
    suite: Annotated[Path, typer.Argument(help="Eval suite YAML file.")],
    mock: Annotated[bool, typer.Option("--mock", help="Use the offline mock AI provider.")] = False,
    min_pass: Annotated[
        float,
        typer.Option(
            "--min-pass", min=0.0, max=1.0, help="Share of cases that must pass (else exit 1)."
        ),
    ] = DEFAULT_EVAL_MIN_PASS,
    no_sandbox: Annotated[
        bool, typer.Option("--no-sandbox", help="Do not start the sandbox payments API.")
    ] = False,
) -> None:
    """Run an eval suite: one run per case, checked and compared with the previous run."""
    settings = _settings()
    # Validate before starting anything, with the environment the run will have: unless the
    # user set SANDBOX_URL_ENV, the sandbox sets it to its own URL (connectors may read it).
    env = dict(os.environ)
    if not no_sandbox and SANDBOX_URL_ENV not in env:
        env[SANDBOX_URL_ENV] = sandbox_url(settings.sandbox_host, settings.sandbox_port)
    loaded = _load_suite(suite, env)
    with _eval_lock(settings), _sandbox(settings, "never", enabled=not no_sandbox) as handle:
        choice = select_provider(settings, force_mock=mock or loaded.suite.defaults.mock)
        with Store(settings.db_path) as store:
            runner = EvalRunner(
                store, settings, choice.provider, set_fail_mode=_fail_mode_setter(handle)
            )
            console.print(
                render.header(
                    f"eval · {loaded.suite.suite}",
                    f"{loaded.workflow.name} · {len(loaded.suite.cases)} cases · {choice.reason}",
                )
            )
            targets = connector_targets(loaded.workflow, handle.url if handle else None)
            console.print(render.eval_targets(targets))
            if handle is not None and not handle.owned:
                console.print(render.sandbox_reused(handle.url))
            console.print(Rule(style=render.MUTED))
            record = asyncio.run(
                runner.run(
                    loaded,
                    on_result=lambda _case, result: console.print(render.eval_case_line(result)),
                )
            )
            console.print(Rule(style=render.MUTED))
            baseline = store.get_eval_run(record.baseline_id) if record.baseline_id else None
            regressed = [r.case_id for r in store.get_eval_results(record.id) if r.regression]
            console.print(render.eval_summary(record, baseline, regressed, min_pass))
    passed = record.passed / record.total + 1e-9 >= min_pass
    raise typer.Exit(EXIT_OK if passed else EXIT_FAILED)


@evals_app.command("prune")
def evals_prune(
    keep: Annotated[
        int,
        typer.Option("--keep", min=0, help="Newest eval runs per suite that keep their sandbox."),
    ] = DEFAULT_EVAL_KEEP,
) -> None:
    """Remove the sandbox directories of all but the newest eval runs of each suite."""
    settings = _settings()
    with Store(settings.db_path) as store:
        pruned = prune_eval_homes(store, settings, keep=keep)
    console.print(render.pruned_view(pruned, keep))


@app.command()
def demo(
    live: Annotated[
        bool, typer.Option("--live", help="Use the real Claude API instead of the offline mock AI.")
    ] = False,
) -> None:
    """Run five refund scenarios end to end against the local sandbox. Resets the orders_db
    sandbox database in CEREBELLUM_HOME to the template's seed data first."""
    settings = _settings()
    handle = _start_sandbox(settings, "never")
    try:
        with Store(settings.db_path) as store:
            choice = select_provider(settings, force_mock=not live)
            env = {**os.environ, "ORDERS_DSN": "sandbox", SANDBOX_URL_ENV: handle.url}
            workflow = load_workflow(template_path("refund") / "workflow.yaml", env=env)
            engine = Engine(store, settings, choice.provider)
            # The demo always starts from the seed data. The file is the sandbox database of
            # every workflow's orders_db connector in this home (the waiting demo run resumes
            # against it through `cerebellum approve`), so say so when one is replaced. Only
            # now that everything above started: a demo that cannot start leaves it alone.
            orders_db = sandbox_db_path(settings.home, DEMO_SANDBOX_CONNECTOR)
            reset = orders_db.exists()
            orders_db.unlink(missing_ok=True)
            console.print(render.header("demo · refund_request", choice.reason))
            if reset:
                console.print(
                    Text("↺ ", style="yellow") + Text(f"reset the sandbox database {orders_db}"),
                    soft_wrap=True,
                )
                console.print(
                    Text(
                        f"  shared by every workflow's sandbox connector named "
                        f"{DEMO_SANDBOX_CONNECTOR}; it now holds the refund seed data",
                        style=render.MUTED,
                    )
                )
            console.print(Rule(style=render.MUTED))
            results = asyncio.run(
                run_scenarios(
                    engine,
                    workflow,
                    handle,
                    lambda scenario, record: console.print(
                        render.scenario_line(scenario.title, record)
                    ),
                )
            )
            console.print(Rule(style=render.MUTED))
            for _, record in results:
                if record.status is RunStatus.WAITING_APPROVAL:
                    console.print(Text("⏸ ", style="yellow") + Text("a human decision is pending"))
                    console.print(
                        Text(
                            f"  cerebellum approve {record.run_id} manager_approval "
                            "--by <you> --sandbox",
                            style=render.ACCENT,
                        )
                    )
            retried = next((r for s, r in results if s.key == "flaky"), None)
            if retried is not None:
                console.print(
                    Text(f"  cerebellum trace {retried.run_id}", style=render.ACCENT)
                    + Text("   see the retries", style=render.MUTED)
                )
            console.print(
                Text("  cerebellum tasks", style=render.ACCENT)
                + Text("   the manual case opened by the fallback", style=render.MUTED)
            )
            console.print(
                Text("  cerebellum runs", style=render.ACCENT)
                + Text("   everything that just happened", style=render.MUTED)
            )
            console.print(
                Text("  cerebellum ui", style=render.ACCENT)
                + Text(
                    f"   dashboard at http://{settings.ui_host}:{settings.ui_port}",
                    style=render.MUTED,
                )
            )
    finally:
        handle.stop()
