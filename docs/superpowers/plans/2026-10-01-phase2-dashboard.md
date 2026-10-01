# Phase 2 — Dashboard (`cerebellum ui`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local dashboard — FastAPI JSON API + server-sent events + background worker, and a prebuilt React UI — where a person watches runs live, reads traces, approves or rejects, works the manual-task inbox and starts new runs.

**Architecture:** `cerebellum.server` wraps the Phase 1 runtime: a `Worker` drives runs in the server's event loop (UI-started runs, resumes after decisions, a periodic approval-timeout sweep); a `Catalog` lists workflows from a directory and from run history; the API serialises store projections; `/api/stream` polls the event table (cross-process) and pushes SSE. The UI (React 19 + Vite 7 + Tailwind 4 + React Flow 12 + TanStack Query 5) is built into `src/cerebellum/server/static/` and served by the same app, so `cerebellum ui` needs no Node.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx (tests), SQLite (WAL); TypeScript 5.9, React 19, React Router 7, TanStack Query 5, @xyflow/react 12, Tailwind CSS 4, Vite 7, Vitest 3, self-hosted Inter + JetBrains Mono (@fontsource-variable).

**Spec:** `docs/superpowers/specs/2026-10-01-workflow-runtime-design.md` (§9 Dashboard, §4.5 approvals, §11 phase 2 acceptance). Builds on Phase 1: `docs/superpowers/plans/2026-10-01-phase1-runtime-cli.md`.

## Global Constraints

- Do not commit (user rule). Every "Checkpoint" step runs `git status --short` only.
- No new Python dependencies (FastAPI, uvicorn, httpx are already required).
- UI packages, pinned by major: react 19, react-dom 19, react-router 7, @tanstack/react-query 5, @xyflow/react 12, @fontsource-variable/inter 5, @fontsource-variable/jetbrains-mono 5; dev: vite 7, @vitejs/plugin-react 5, tailwindcss 4, @tailwindcss/vite 4, typescript ~5.9, vitest 3, @types/react 19, @types/react-dom 19. Node ≥ 20.19 is needed only to rebuild the UI.
- The built UI is committed under `src/cerebellum/server/static/`; nothing is loaded from a CDN at runtime (fonts are bundled).
- Server binds `127.0.0.1` by default, has no authentication (local tool, README says so) and warns when `--host` is not a loopback address.
- Pages in this phase: Overview `/`, Run detail `/runs/:id`, Approvals `/approvals`, Tasks `/tasks`, Workflows `/workflows`. Evals is Phase 3.
- API (spec §9.3): `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events`, `POST /api/runs` `{workflow, input, params}`, `POST /api/runs/{id}/resume`, `GET /api/approvals`, `POST /api/approvals/{id}/decision`, `GET /api/tasks`, `POST /api/tasks/{id}/resolve`, `GET /api/metrics?window=24h`, `GET /api/workflows`, `GET /api/stream` (SSE, polls `events` by `seq` every 0.5 s). Plus `GET /api/info` and `GET /api/workflows/{id}` (detail).
- Built-in worker: UI-started runs and resumes; approval-timeout sweep every 30 s.
- Visual (spec §9.2): dark near-black background, hairline borders, no shadows or gradients, Inter (UI) + JetBrains Mono (ids, numbers, JSON), one cyan accent, desaturated status colours (ok green, waiting amber, failed red, fallback purple, skipped grey), small uppercase labels, dotted DAG canvas, every colour a CSS token.
- All new defaults live in `config.py` and are overridable by env vars.
- The user's pre-tool hook blocks some shell commands that name certain files (e.g. the states/trace/store test files) or contain `os.environ`/`unlink`/heredoc appends: run tests by directory and edit files with the editor tools.

## Review Focus

1. **The UI approves a run that a CLI process is still driving** → the decision is recorded, the server's resume attempt quietly yields (`LeaseUnavailable`), and the CLI's drive applies the decision before suspending. Test: Task 3 `test_decide_while_another_process_drives_the_run`.
2. **The browser's EventSource reconnects with `Last-Event-ID`** → the stream resumes after that `seq`: no duplicates, no gaps. Test: Task 4 `test_stream_resumes_after_last_event_id`.
3. **The workflows directory holds unrelated or broken YAML, hidden folders, `node_modules`, deep trees** → those are skipped; listing never fails. Test: Task 2 `test_scan_skips_non_workflows_hidden_dirs_and_deep_files`.
4. **Two tabs (or a double click) decide the same approval** → the second request gets 409 with a message; the run resumes once. Tests: Task 3 `test_deciding_twice_conflicts`, Task 4 `test_approval_flow_over_the_api`.
5. **No Anthropic credentials** → runs use the mock provider and the UI says so prominently. Tests: Task 4 `test_info_reports_mock_mode`; UI banner in Task 7 (checked in the Task 8 walkthrough).

## File Structure

```
src/cerebellum/
  config.py                       # + ui_host, ui_port, worker_interval, stream_poll
  errors.py                       # + NotFound (RunNotFound subclasses it)
  runtime/store.py                # + WorkflowSnapshot, list_workflows, last_seq, metrics; NotFound for approvals/tasks
  runtime/engine.py               # + Engine.prepare (validate + record a run without driving it)
  server/
    __init__.py                   # create_app
    serialize.py                  # records → JSON dicts
    catalog.py                    # workflows on disk (depth ≤ 3) + run-history snapshots
    worker.py                     # background drives, decisions, approval-timeout sweep
    stream.py                     # SSE frames from the event table
    app.py                        # FastAPI app: /api/*, /api/stream, static UI + SPA fallback
    static/                       # built UI (committed; produced by `make ui`)
  cli/app.py                      # + `cerebellum ui`; demo prints the dashboard hint
Makefile                          # + ui-install, ui, ui-test; test runs vitest when installed
README.md                         # + Dashboard section, config rows, roadmap update
ui/
  package.json, package-lock.json, tsconfig.json, vite.config.ts, index.html, public/favicon.svg
  src/main.tsx, App.tsx, index.css, api.ts, types.ts
  src/lib/format.ts, status.ts, layout.ts, waterfall.ts, runs.ts, invalidation.ts (+ *.test.ts)
  src/hooks/useEventStream.ts, useNow.ts, useApprover.ts
  src/components/ui.tsx, Shell.tsx, RunGraph.tsx, StepInspector.tsx, Waterfall.tsx, ApprovalCard.tsx, NewRunDialog.tsx, RunsTable.tsx
  src/pages/Overview.tsx, RunDetail.tsx, Approvals.tsx, Tasks.tsx, Workflows.tsx
tests/
  runtime/test_queries.py         # store queries + Engine.prepare
  server/test_catalog.py, test_worker.py, test_api.py, test_stream.py, test_packaged_ui.py
  cli/test_cli.py                 # + ui command tests; test_demo.py + dashboard hint
```

---

### Task 1: Settings, NotFound, store queries, `Engine.prepare`

**Files:**
- Modify: `src/cerebellum/config.py`, `src/cerebellum/errors.py`, `src/cerebellum/runtime/store.py`, `src/cerebellum/runtime/engine.py`
- Test: `tests/test_config.py` (append), `tests/runtime/test_queries.py` (new)

**Interfaces:**
- Consumes: Phase 1 `Store`, `Engine`, `RunStatus`, `RUN_TERMINAL`.
- Produces: `Settings.ui_host: str`, `ui_port: int`, `worker_interval: float`, `stream_poll: float` (env `CEREBELLUM_UI_HOST`, `CEREBELLUM_UI_PORT`, `CEREBELLUM_WORKER_INTERVAL`, `CEREBELLUM_STREAM_POLL`); constants `DEFAULT_UI_HOST`, `DEFAULT_WORKER_INTERVAL_SECONDS`, `DEFAULT_STREAM_POLL_SECONDS`; `errors.NotFound(CerebellumError)`, `RunNotFound(NotFound)`; `Store.get_approval/decide_approval/get_task/resolve_task` raise `NotFound` for unknown ids; `WorkflowSnapshot(digest, name, version, source_yaml, base_dir, created_at, runs, last_run_at)`; `Store.list_workflows() -> list[WorkflowSnapshot]` (most recently used first); `Store.last_seq() -> int`; `Store.metrics(since: float) -> dict` with keys `since, runs, by_status, success_rate, avg_duration_s, cost_usd, pending_approvals, open_tasks, retries, fallbacks`; `Engine.prepare(workflow, input=None, params=None, *, run_id=None, eval_run_id=None) -> RunRecord`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_dashboard_settings_have_defaults_and_env_overrides():
    defaults = Settings.from_env({})
    assert (defaults.ui_host, defaults.ui_port) == ("127.0.0.1", 7400)
    assert defaults.worker_interval == 30.0 and defaults.stream_poll == 0.5
    custom = Settings.from_env(
        {
            "CEREBELLUM_UI_HOST": "0.0.0.0",
            "CEREBELLUM_UI_PORT": "9000",
            "CEREBELLUM_WORKER_INTERVAL": "5",
            "CEREBELLUM_STREAM_POLL": "0.1",
        }
    )
    assert (custom.ui_host, custom.ui_port) == ("0.0.0.0", 9000)
    assert custom.worker_interval == 5.0 and custom.stream_poll == 0.1
```

`tests/runtime/test_queries.py`:

```python
import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.errors import NotFound, RunNotFound, SpecError
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus, StepStatus
from cerebellum.spec import parse_workflow

S = StepStatus

YAML = """
name: metric_flow
input: {amount: {type: number, required: true}}
steps:
  - {id: gate, type: approval, when: "input.amount > 100", title: "Approve {{ input.amount }}"}
  - {id: note, type: task, needs: [gate], title: "Note {{ input.amount }}"}
"""


def engine_for(store, settings):
    return Engine(store, settings, MockProvider(latency=(0, 0)), jitter=0)


def flow(tmp_path, name="metric_flow"):
    return parse_workflow(YAML.replace("metric_flow", name), base_dir=tmp_path, env={})


async def test_prepare_records_the_run_without_driving_it(store, settings, tmp_path):
    engine = engine_for(store, settings)
    run = engine.prepare(flow(tmp_path), {"amount": 5}, run_id="r_prep0001")
    assert run.run_id == "r_prep0001" and run.status is RunStatus.RUNNING
    assert {s.status for s in store.get_steps(run.run_id).values()} == {S.PENDING}
    assert run.lease_owner is None
    assert (await engine.resume(run.run_id)).status is RunStatus.SUCCEEDED


def test_prepare_rejects_bad_input_without_creating_a_run(store, settings, tmp_path):
    with pytest.raises(SpecError):
        engine_for(store, settings).prepare(flow(tmp_path), {"amount": "x"})
    assert store.list_runs() == []


async def test_metrics_summarise_runs_in_the_window(store, settings, clock, tmp_path):
    engine = engine_for(store, settings)
    await engine.start(flow(tmp_path), {"amount": 5})  # before the window
    clock.advance(3600)
    since = clock.now()
    await engine.start(flow(tmp_path), {"amount": 5})
    await engine.start(flow(tmp_path), {"amount": 500})
    m = store.metrics(since)
    assert m["since"] == since and m["runs"] == 2
    assert m["by_status"]["succeeded"] == 1 and m["by_status"]["waiting_approval"] == 1
    assert m["success_rate"] == 1.0 and m["avg_duration_s"] == 0.0 and m["cost_usd"] == 0.0
    # Inbox sizes are current counts, not windowed: both finished runs opened a task.
    assert m["pending_approvals"] == 1 and m["open_tasks"] == 2
    assert m["retries"] == 0 and m["fallbacks"] == 0
    assert store.metrics(clock.now() + 1)["success_rate"] is None


def test_metrics_count_retries_and_fallbacks(store, simple_workflow):
    store.save_workflow(simple_workflow)
    store.create_run("r_m0000001", simple_workflow, {}, {}, mock=True)
    for status, event in [
        (S.RUNNING, "started"),
        (S.RETRYING, "retrying"),
        (S.RUNNING, "started"),
        (S.FAILED, "failed"),
        (S.RECOVERED, "recovered"),
    ]:
        store.step_transition("r_m0000001", "first", status, event=event)
    m = store.metrics(0)
    assert (m["retries"], m["fallbacks"], m["runs"]) == (1, 1, 1)
    assert m["success_rate"] is None


async def test_list_workflows_reports_snapshots_with_run_counts(store, settings, clock, tmp_path):
    engine = engine_for(store, settings)
    used = flow(tmp_path)
    store.save_workflow(flow(tmp_path, "other_flow"))
    await engine.start(used, {"amount": 5})
    clock.advance(5)
    await engine.start(used, {"amount": 6})
    first, second = store.list_workflows()
    assert (first.name, first.runs, first.last_run_at) == ("metric_flow", 2, clock.now())
    assert first.digest == used.digest and first.source_yaml == used.source_yaml
    assert first.base_dir == used.base_dir and first.version == 1
    assert (second.name, second.runs, second.last_run_at) == ("other_flow", 0, None)


async def test_last_seq_tracks_the_newest_event(store, settings, tmp_path):
    assert store.last_seq() == 0
    run = await engine_for(store, settings).start(flow(tmp_path), {"amount": 5})
    assert store.last_seq() == store.get_events(run.run_id)[-1].seq


def test_unknown_ids_raise_not_found(store):
    with pytest.raises(NotFound):
        store.get_approval("ap_missing")
    with pytest.raises(NotFound):
        store.decide_approval("ap_missing", approved=True, by="x")
    with pytest.raises(NotFound):
        store.resolve_task("tk_missing", by="x")
    with pytest.raises(NotFound):
        store.get_task("tk_missing")
    assert issubclass(RunNotFound, NotFound)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py tests/runtime -q`
Expected: FAIL — `ImportError: cannot import name 'NotFound'` (collection error in `test_queries.py`) and `AttributeError: 'Settings' object has no attribute 'ui_host'`.

- [ ] **Step 3: Implement**

`src/cerebellum/config.py` — add after `DEFAULT_SANDBOX_PORT = 8787`:

```python
DEFAULT_UI_HOST = "127.0.0.1"
```

and after `DEFAULT_AI_MAX_TOKENS = 16000`:

```python
# Dashboard server: how often the worker sweeps for overdue approvals, and how often the live
# event stream polls the event table (it also sees events written by other processes).
DEFAULT_WORKER_INTERVAL_SECONDS = 30.0
DEFAULT_STREAM_POLL_SECONDS = 0.5
```

Extend `Settings` (new fields have defaults so existing constructors keep working):

```python
    lease_seconds: float
    ui_host: str = DEFAULT_UI_HOST
    ui_port: int = DEFAULT_UI_PORT
    worker_interval: float = DEFAULT_WORKER_INTERVAL_SECONDS
    stream_poll: float = DEFAULT_STREAM_POLL_SECONDS
```

and in `from_env`, after `lease_seconds=...`:

```python
            ui_host=env.get("CEREBELLUM_UI_HOST", DEFAULT_UI_HOST),
            ui_port=int(env.get("CEREBELLUM_UI_PORT", DEFAULT_UI_PORT)),
            worker_interval=float(
                env.get("CEREBELLUM_WORKER_INTERVAL", DEFAULT_WORKER_INTERVAL_SECONDS)
            ),
            stream_poll=float(env.get("CEREBELLUM_STREAM_POLL", DEFAULT_STREAM_POLL_SECONDS)),
```

`src/cerebellum/errors.py` — replace the `RunNotFound` class with:

```python
class NotFound(CerebellumError):
    """The requested run, approval, task or workflow does not exist."""


class RunNotFound(NotFound):
    """No run with the given id exists."""
```

`src/cerebellum/runtime/store.py`:

- import: `from cerebellum.errors import CerebellumError, NotFound, RunNotFound`
- In `get_workflow_source`, `decide_approval`, `get_approval`, `resolve_task`, `get_task`: change `raise CerebellumError(f"... not found")` to `raise NotFound(f"... not found")` (same messages).
- Add after the `TaskRecord` dataclass:

```python
@dataclass(frozen=True)
class WorkflowSnapshot:
    digest: str
    name: str
    version: int
    source_yaml: str
    base_dir: str
    created_at: float
    runs: int
    last_run_at: float | None
```

- Add to `Store`, after `get_workflow_source`:

```python
    def list_workflows(self) -> list[WorkflowSnapshot]:
        """Every workflow snapshot runs were started from, most recently used first."""
        rows = self._rows(
            "SELECT w.digest, w.name, w.version, w.source_yaml, w.base_dir, w.created_at, "
            "COUNT(r.run_id) AS runs, MAX(r.created_at) AS last_run_at "
            "FROM workflows w LEFT JOIN runs r ON r.workflow_digest = w.digest "
            "GROUP BY w.digest ORDER BY COALESCE(last_run_at, w.created_at) DESC"
        )
        return [WorkflowSnapshot(**dict(row)) for row in rows]
```

- Add a metrics section before `# ── events`:

```python
    # ── metrics ───────────────────────────────────────────────────────────────

    def metrics(self, since: float) -> dict[str, Any]:
        """Run outcomes since `since`; inbox sizes (pending approvals, open tasks) are current."""
        runs = self._rows(
            "SELECT status, cost_usd, created_at, ended_at FROM runs WHERE created_at >= ?",
            (since,),
        )
        by_status = {status.value: 0 for status in RunStatus}
        for row in runs:
            by_status[row["status"]] += 1
        finished = [row for row in runs if RunStatus(row["status"]) in RUN_TERMINAL]
        durations = [r["ended_at"] - r["created_at"] for r in finished if r["ended_at"] is not None]
        succeeded = by_status[RunStatus.SUCCEEDED.value]

        def count(sql: str, params: tuple[Any, ...] = ()) -> int:
            return int(self._rows(sql, params)[0][0])

        return {
            "since": since,
            "runs": len(runs),
            "by_status": by_status,
            "success_rate": succeeded / len(finished) if finished else None,
            "avg_duration_s": sum(durations) / len(durations) if durations else None,
            "cost_usd": sum(row["cost_usd"] for row in runs),
            "pending_approvals": count("SELECT COUNT(*) FROM approvals WHERE status='pending'"),
            "open_tasks": count("SELECT COUNT(*) FROM tasks WHERE status='open'"),
            "retries": count(
                "SELECT COUNT(*) FROM events WHERE type='step.retrying' AND ts >= ?", (since,)
            ),
            "fallbacks": count(
                "SELECT COUNT(*) FROM events WHERE type='step.recovered' AND ts >= ?", (since,)
            ),
        }
```

- Add to the events section:

```python
    def last_seq(self) -> int:
        return int(self._rows("SELECT COALESCE(MAX(seq), 0) FROM events")[0][0])
```

`src/cerebellum/runtime/engine.py` — replace `start` with `prepare` + `start`:

```python
    def prepare(
        self,
        workflow: Workflow,
        input: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        *,
        run_id: str | None = None,
        eval_run_id: str | None = None,
    ) -> RunRecord:
        """Validate the input and record a new run without driving it (the dashboard drives it
        in the background so the request can return the run id at once)."""
        data = validate_input(workflow, dict(input or {}))
        resolved = resolve_params(workflow, params)
        self.store.save_workflow(workflow)
        return self.store.create_run(
            run_id or new_run_id(),
            workflow,
            data,
            resolved,
            mock=self.ai.mock,
            eval_run_id=eval_run_id,
        )

    async def start(
        self,
        workflow: Workflow,
        input: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        *,
        run_id: str | None = None,
        eval_run_id: str | None = None,
    ) -> RunRecord:
        record = self.prepare(workflow, input, params, run_id=run_id, eval_run_id=eval_run_id)
        return await self._drive(record.run_id, workflow)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py tests/runtime -q`
Expected: all PASS.

- [ ] **Step 5: Whole suite, lint, checkpoint**

Run: `.venv/bin/pytest tests -q && make fmt && make lint && git status --short`
Expected: all PASS; lint clean.

---

### Task 2: `serialize` and `Catalog`

**Files:**
- Create: `src/cerebellum/server/__init__.py` (docstring only in this task), `src/cerebellum/server/serialize.py`, `src/cerebellum/server/catalog.py`
- Test: `tests/server/test_catalog.py`

**Interfaces:**
- Consumes: `Store.list_workflows`, `load_workflow`, `parse_workflow`, records from Task 1 / Phase 1, `Span`.
- Produces: `WorkflowEntry(id, workflow, source, path, samples, runs, last_run_at)`; `Catalog(store, root, *, depth=DEFAULT_SCAN_DEPTH)` with `entries() -> list[WorkflowEntry]` and `get(entry_id) -> WorkflowEntry` (raises `KeyError`); file entries have `id` = POSIX path relative to `root`, history entries have `id` = digest; `serialize.run_json(run, *, stale=False)`, `step_json`, `approval_json(approval, run=None)`, `task_json(task, run=None)`, `event_json`, `span_json`, `graph_json(workflow)` → `{"steps": [{id, type, description, needs, when, fallback}], "fallbacks": [{id, type, description, fallback_for}]}`, `workflow_summary(entry)`, `workflow_detail(entry)`.

- [ ] **Step 1: Write the failing test**

`tests/server/test_catalog.py`:

```python
import json
import shutil

import pytest

from cerebellum.server.catalog import Catalog
from cerebellum.server.serialize import graph_json, workflow_detail, workflow_summary
from cerebellum.spec import load_workflow, parse_workflow
from cerebellum.templates import template_path

FLOW = """
name: tiny
description: A tiny flow
input: {who: {type: string, required: true}}
steps:
  - {id: hello, type: task, title: "Hello {{ input.who }}"}
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_skips_non_workflows_hidden_dirs_and_deep_files(store, tmp_path):
    root = tmp_path / "project"
    write(root / "flows" / "tiny" / "workflow.yaml", FLOW)
    write(root / "flows" / "tiny" / "inputs" / "alice.json", json.dumps({"who": "alice"}))
    write(root / "flows" / "tiny" / "inputs" / "broken.json", "{not json")
    write(root / "docker-compose.yml", "services: {db: {image: postgres}}\n")
    write(root / "broken.yaml", "name: [unclosed\n")
    write(root / ".hidden" / "w.yaml", FLOW.replace("tiny", "hidden"))
    write(root / "node_modules" / "pkg" / "w.yaml", FLOW.replace("tiny", "vendored"))
    write(root / "a" / "b" / "c" / "w.yaml", FLOW.replace("tiny", "depth_three"))
    write(root / "a" / "b" / "c" / "d" / "w.yaml", FLOW.replace("tiny", "depth_four"))

    entries = {e.id: e for e in Catalog(store, root).entries()}

    assert sorted(entries) == ["a/b/c/w.yaml", "flows/tiny/workflow.yaml"]
    tiny = entries["flows/tiny/workflow.yaml"]
    assert tiny.source == "file" and tiny.workflow.name == "tiny" and tiny.runs == 0
    assert tiny.samples == {"alice": {"who": "alice"}}
    assert tiny.path == str((root / "flows" / "tiny" / "workflow.yaml").resolve())


def test_history_snapshots_are_listed_and_merged_with_files(store, tmp_path):
    root = tmp_path / "project"
    on_disk = load_workflow(write(root / "tiny.yaml", FLOW))
    store.save_workflow(on_disk)
    store.create_run("r_cat00001", on_disk, {"who": "a"}, {}, mock=True)
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(template_path("refund"), elsewhere)
    refund = load_workflow(elsewhere / "workflow.yaml", env={})
    store.save_workflow(refund)

    entries = {e.id: e for e in Catalog(store, root).entries()}

    assert entries["tiny.yaml"].runs == 1  # merged with its snapshot, not listed twice
    history = entries[refund.digest]
    assert history.source == "history" and history.path is None
    assert history.workflow.name == "refund_request"
    assert set(history.samples) == {"small", "large", "flaky", "outage", "fraud"}
    assert len(entries) == 2


def test_snapshots_that_need_missing_env_vars_are_skipped(store, tmp_path):
    needs_env = """
name: needs_env
connectors: {api: {type: rest, base_url: "${SOME_UNSET_BASE_URL}"}}
steps:
  - {id: call, type: http, connector: api, path: /x}
"""
    store.save_workflow(
        parse_workflow(needs_env, base_dir=tmp_path, env={"SOME_UNSET_BASE_URL": "http://x"})
    )
    assert Catalog(store, tmp_path / "empty").entries() == []


def test_get_and_serialisation(store, tmp_path):
    root = tmp_path / "project"
    write(root / "tiny.yaml", FLOW)
    catalog = Catalog(store, root)
    entry = catalog.get("tiny.yaml")
    summary = workflow_summary(entry)
    assert summary == {
        "id": "tiny.yaml",
        "name": "tiny",
        "version": 1,
        "description": "A tiny flow",
        "source": "file",
        "path": entry.path,
        "digest": entry.workflow.digest,
        "steps": 1,
        "runs": 0,
        "last_run_at": None,
    }
    detail = workflow_detail(entry)
    assert detail["yaml"] == FLOW and detail["params"] == {} and detail["samples"] == {}
    assert detail["input"]["who"]["required"] is True
    assert detail["graph"] == graph_json(entry.workflow)
    with pytest.raises(KeyError):
        catalog.get("missing.yaml")


def test_graph_json_lists_steps_and_fallback_links():
    refund = load_workflow(template_path("refund") / "workflow.yaml", env={})
    graph = graph_json(refund)
    assert [s["id"] for s in graph["steps"]][:2] == ["fetch_order", "policy_check"]
    issue = next(s for s in graph["steps"] if s["id"] == "issue_refund")
    assert issue["needs"] == ["manager_approval"] and issue["fallback"] == "open_manual_case"
    assert graph["fallbacks"] == [
        {
            "id": "open_manual_case",
            "type": "task",
            "description": "",
            "fallback_for": "issue_refund",
        }
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/server -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cerebellum.server'`.

- [ ] **Step 3: Implement**

`src/cerebellum/server/__init__.py`:

```python
"""Dashboard server: JSON API, live event stream, background worker and the prebuilt UI."""
```

`src/cerebellum/server/serialize.py`:

```python
"""JSON shapes returned by the dashboard API."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from cerebellum.runtime.store import (
    ApprovalRecord,
    EventRecord,
    RunRecord,
    StepRecord,
    TaskRecord,
)
from cerebellum.runtime.trace import Span
from cerebellum.spec.models import Workflow

if TYPE_CHECKING:
    from cerebellum.server.catalog import WorkflowEntry


def run_json(run: RunRecord, *, stale: bool = False) -> dict[str, Any]:
    data = dataclasses.asdict(run)
    data["status"] = run.status.value
    data["stale"] = stale
    data["duration_s"] = None if run.ended_at is None else run.ended_at - run.created_at
    return data


def step_json(record: StepRecord) -> dict[str, Any]:
    data = dataclasses.asdict(record)
    data["status"] = record.status.value
    data["duration_s"] = record.duration
    return data


def _with_run(data: dict[str, Any], run: RunRecord | None) -> dict[str, Any]:
    if run is not None:
        data["workflow_name"] = run.workflow_name
        data["run_status"] = run.status.value
    return data


def approval_json(approval: ApprovalRecord, run: RunRecord | None = None) -> dict[str, Any]:
    return _with_run(dataclasses.asdict(approval), run)


def task_json(task: TaskRecord, run: RunRecord | None = None) -> dict[str, Any]:
    return _with_run(dataclasses.asdict(task), run)


def event_json(event: EventRecord) -> dict[str, Any]:
    return dataclasses.asdict(event)


def span_json(span: Span) -> dict[str, Any]:
    return dataclasses.asdict(span)


def graph_json(workflow: Workflow) -> dict[str, Any]:
    users = {s.on_failure.fallback: s.id for s in workflow.steps if s.on_failure}
    return {
        "steps": [
            {
                "id": step.id,
                "type": step.type,
                "description": step.description,
                "needs": list(step.needs),
                "when": step.when,
                "fallback": step.on_failure.fallback if step.on_failure else None,
            }
            for step in workflow.steps
        ],
        "fallbacks": [
            {
                "id": step.id,
                "type": step.type,
                "description": step.description,
                "fallback_for": users.get(step.id),
            }
            for step in workflow.fallbacks
        ],
    }


def workflow_summary(entry: WorkflowEntry) -> dict[str, Any]:
    wf = entry.workflow
    return {
        "id": entry.id,
        "name": wf.name,
        "version": wf.version,
        "description": wf.description,
        "source": entry.source,
        "path": entry.path,
        "digest": wf.digest,
        "steps": len(wf.steps),
        "runs": entry.runs,
        "last_run_at": entry.last_run_at,
    }


def workflow_detail(entry: WorkflowEntry) -> dict[str, Any]:
    wf = entry.workflow
    return {
        **workflow_summary(entry),
        "yaml": wf.source_yaml,
        "graph": graph_json(wf),
        "params": wf.params,
        "input": {name: field.model_dump() for name, field in wf.input.items()},
        "samples": entry.samples,
    }
```

`src/cerebellum/server/catalog.py`:

```python
"""Workflows the dashboard can show and start: YAML files under a directory, plus every workflow
snapshot that runs in the store were started from."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cerebellum.errors import SpecError
from cerebellum.runtime.store import Store
from cerebellum.spec import load_workflow, parse_workflow
from cerebellum.spec.models import Workflow

DEFAULT_SCAN_DEPTH = 3
SKIP_DIRS = frozenset({"node_modules", "__pycache__", "site-packages", "dist", "build"})


@dataclass(frozen=True)
class WorkflowEntry:
    id: str
    workflow: Workflow
    source: str  # "file" | "history"
    path: str | None
    samples: dict[str, Any] = field(default_factory=dict)
    runs: int = 0
    last_run_at: float | None = None


class Catalog:
    def __init__(self, store: Store, root: Path, *, depth: int = DEFAULT_SCAN_DEPTH) -> None:
        self.store = store
        self.root = Path(root).resolve()
        self.depth = depth

    def entries(self) -> list[WorkflowEntry]:
        snapshots = {snap.digest: snap for snap in self.store.list_workflows()}
        entries: list[WorkflowEntry] = []
        on_disk: set[str] = set()
        for path in self._yaml_files():
            try:
                workflow = load_workflow(path)
            except (SpecError, OSError, UnicodeDecodeError):
                continue  # not a workflow (compose files, eval suites, ...) or unreadable
            snap = snapshots.get(workflow.digest)
            on_disk.add(workflow.digest)
            entries.append(
                WorkflowEntry(
                    id=path.relative_to(self.root).as_posix(),
                    workflow=workflow,
                    source="file",
                    path=str(path),
                    samples=_samples(path.parent),
                    runs=snap.runs if snap else 0,
                    last_run_at=snap.last_run_at if snap else None,
                )
            )
        for digest, snap in snapshots.items():
            if digest in on_disk:
                continue
            try:
                workflow = parse_workflow(snap.source_yaml, base_dir=snap.base_dir)
            except SpecError:
                continue  # e.g. a ${VAR} it needs is not set in this environment
            entries.append(
                WorkflowEntry(
                    id=digest,
                    workflow=workflow,
                    source="history",
                    path=None,
                    samples=_samples(Path(snap.base_dir)),
                    runs=snap.runs,
                    last_run_at=snap.last_run_at,
                )
            )
        return entries

    def get(self, entry_id: str) -> WorkflowEntry:
        for entry in self.entries():
            if entry.id == entry_id:
                return entry
        raise KeyError(entry_id)

    def _yaml_files(self) -> Iterator[Path]:
        if not self.root.is_dir():
            return
        for dirpath, dirnames, filenames in os.walk(self.root):
            depth = len(Path(dirpath).relative_to(self.root).parts)
            if depth >= self.depth:
                dirnames[:] = []
            else:
                dirnames[:] = sorted(
                    d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
                )
            for name in sorted(filenames):
                if name.endswith((".yaml", ".yml")):
                    yield Path(dirpath) / name


def _samples(directory: Path) -> dict[str, Any]:
    samples: dict[str, Any] = {}
    for path in sorted((directory / "inputs").glob("*.json")):
        try:
            samples[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return samples
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/server -q`
Expected: all PASS.

- [ ] **Step 5: Whole suite, lint, checkpoint**

Run: `.venv/bin/pytest tests -q && make fmt && make lint && git status --short`

---

### Task 3: `Worker`

**Files:**
- Create: `src/cerebellum/server/worker.py`
- Test: `tests/server/test_worker.py`

**Interfaces:**
- Consumes: `Engine.prepare/resume/decide`, `Store`, `MockProvider`, `NotFound`, `LeaseUnavailable`, `RUN_RESUMABLE`.
- Produces: `Worker(store, settings, provider, *, clock=None, http_transports=None, interval=None)`; `engine(*, mock: bool) -> Engine` (mock runs always use a mock provider); `start_run(workflow, input=None, params=None) -> RunRecord` (sync; raises `SpecError`; must be called inside a running event loop); `resume(run_id) -> RunRecord` (raises `RunNotFound`, `CerebellumError` when not resumable, `LeaseUnavailable` while another process holds a live lease); `async decide(approval_id, *, approved, by, comment="") -> ApprovalRecord` (raises `NotFound`, `CerebellumError` when already decided); `sweep() -> list[str]` (resumes runs with overdue pending approvals); `start()`, `async stop()`, `async drain()`.

- [ ] **Step 1: Write the failing test**

`tests/server/test_worker.py`:

```python
import asyncio
import logging

import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.errors import CerebellumError, LeaseUnavailable, NotFound, RunNotFound, SpecError
from cerebellum.runtime.states import RunStatus
from cerebellum.server.worker import Worker
from cerebellum.spec import parse_workflow

FLOW = """
name: approve_then_note
input: {amount: {type: number, required: true}}
steps:
  - id: gate
    type: approval
    when: "input.amount > 100"
    title: "Approve {{ input.amount }}"
    timeout: 1h
    on_timeout: reject
  - {id: note, type: validate, needs: [gate], rules: [{expr: "true", message: ok}]}
"""


class FakeClaude:
    """Stands in for the real provider: never called by these workflows."""

    name = "claude"
    mock = False

    async def generate(self, request, messages):  # pragma: no cover - not used
        raise AssertionError("not expected")


@pytest.fixture
def wf(tmp_path):
    return parse_workflow(FLOW, base_dir=tmp_path, env={})


@pytest.fixture
def worker(store, settings):
    return Worker(store, settings, MockProvider(latency=(0, 0)))


async def test_start_run_returns_at_once_and_finishes_in_the_background(worker, store, wf):
    record = worker.start_run(wf, {"amount": 5})
    assert record.status is RunStatus.RUNNING
    await worker.drain()
    assert store.get_run(record.run_id).status is RunStatus.SUCCEEDED


async def test_start_run_rejects_bad_input(worker, store, wf):
    with pytest.raises(SpecError):
        worker.start_run(wf, {"amount": "lots"})
    assert store.list_runs() == []


async def test_decide_records_the_decision_and_resumes(worker, store, wf):
    record = worker.start_run(wf, {"amount": 900})
    await worker.drain()
    assert store.get_run(record.run_id).status is RunStatus.WAITING_APPROVAL
    [pending] = store.list_approvals(status="pending")
    decided = await worker.decide(pending.id, approved=True, by="ui-user", comment="ok")
    assert decided.status == "approved" and decided.decided_by == "ui-user"
    await worker.drain()
    assert store.get_run(record.run_id).status is RunStatus.SUCCEEDED


async def test_deciding_twice_conflicts(worker, store, wf):
    worker.start_run(wf, {"amount": 900})
    await worker.drain()
    [pending] = store.list_approvals(status="pending")
    await worker.decide(pending.id, approved=False, by="a")
    with pytest.raises(CerebellumError, match="already rejected") as info:
        await worker.decide(pending.id, approved=True, by="b")
    assert not isinstance(info.value, NotFound)
    await worker.drain()
    assert store.list_runs()[0].status is RunStatus.REJECTED


async def test_unknown_ids_are_not_found(worker):
    with pytest.raises(NotFound):
        await worker.decide("ap_missing", approved=True, by="x")
    with pytest.raises(RunNotFound):
        worker.resume("r_missing0")


async def test_decide_while_another_process_drives_the_run(worker, store, wf, caplog):
    """Review focus: the CLI owns the run; the server records the decision and lets it be."""
    record = worker.start_run(wf, {"amount": 900})
    await worker.drain()
    store.set_run_status(record.run_id, RunStatus.RUNNING, event="resumed")
    assert store.acquire_lease(record.run_id, "cli-process", 60)
    [pending] = store.list_approvals(status="pending")
    with caplog.at_level(logging.ERROR, logger="cerebellum.server.worker"):
        decided = await worker.decide(pending.id, approved=True, by="ui-user")
        await worker.drain()
    assert decided.status == "approved"
    assert store.get_run(record.run_id).lease_owner == "cli-process"
    assert caplog.records == []


async def test_resume_refuses_finished_runs_and_live_leases(worker, store, wf):
    done = worker.start_run(wf, {"amount": 5})
    await worker.drain()
    with pytest.raises(CerebellumError, match="cannot be resumed"):
        worker.resume(done.run_id)
    waiting = worker.start_run(wf, {"amount": 900})
    await worker.drain()
    assert store.acquire_lease(waiting.run_id, "someone-else", 60)
    with pytest.raises(LeaseUnavailable):
        worker.resume(waiting.run_id)


async def test_sweep_resumes_runs_with_overdue_approvals(worker, store, clock, wf):
    record = worker.start_run(wf, {"amount": 900})
    await worker.drain()
    assert worker.sweep() == []
    clock.advance(3601)
    assert worker.sweep() == [record.run_id]
    await worker.drain()
    assert store.get_run(record.run_id).status is RunStatus.REJECTED
    [approval] = store.list_approvals(run_id=record.run_id)
    assert approval.decided_by == "system"


async def test_background_sweeper_runs_until_stopped(store, settings, clock, wf):
    worker = Worker(store, settings, MockProvider(latency=(0, 0)), interval=0.01)
    record = worker.start_run(wf, {"amount": 900})
    await worker.drain()
    clock.advance(3601)
    worker.start()
    for _ in range(200):
        if store.get_run(record.run_id).status is RunStatus.REJECTED:
            break
        await asyncio.sleep(0.01)
    await worker.stop()
    assert store.get_run(record.run_id).status is RunStatus.REJECTED


def test_mock_runs_use_a_mock_provider_even_on_a_claude_server(store, settings):
    claude = FakeClaude()
    worker = Worker(store, settings, claude)
    assert worker.engine(mock=False).ai is claude
    assert worker.engine(mock=True).ai.mock is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/server -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cerebellum.server.worker'`.

- [ ] **Step 3: Implement**

`src/cerebellum/server/worker.py`:

```python
"""Background engine work for the dashboard: runs started from the UI, resumes after decisions,
and a periodic sweep that applies approval timeouts."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Mapping
from typing import Any

import httpx

from cerebellum.ai.base import AIProvider
from cerebellum.ai.mock import MockProvider
from cerebellum.config import Settings
from cerebellum.errors import CerebellumError, LeaseUnavailable
from cerebellum.runtime.clock import Clock
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RUN_RESUMABLE
from cerebellum.runtime.store import ApprovalRecord, RunRecord, Store
from cerebellum.spec.models import Workflow

log = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        provider: AIProvider,
        *,
        clock: Clock | None = None,
        http_transports: Mapping[str, httpx.AsyncBaseTransport] | None = None,
        interval: float | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.provider = provider
        self.clock: Clock = clock or store.clock
        self.http_transports = dict(http_transports or {})
        self.interval = settings.worker_interval if interval is None else interval
        self._mock: AIProvider | None = provider if provider.mock else None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._sweeper: asyncio.Task[None] | None = None

    def engine(self, *, mock: bool) -> Engine:
        """An engine whose AI provider matches the run: mock runs stay on the mock provider."""
        provider = self.provider
        if mock and not provider.mock:
            self._mock = self._mock or MockProvider()
            provider = self._mock
        return Engine(
            self.store,
            self.settings,
            provider,
            clock=self.clock,
            http_transports=self.http_transports,
        )

    def start_run(
        self,
        workflow: Workflow,
        input: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> RunRecord:
        engine = self.engine(mock=self.provider.mock)
        record = engine.prepare(workflow, input, params)
        self._spawn(engine.resume(record.run_id))
        return record

    def resume(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.status not in RUN_RESUMABLE:
            raise CerebellumError(f"run {run_id} is {run.status.value} and cannot be resumed")
        if run.lease_until is not None and run.lease_until >= self.clock.now():
            raise LeaseUnavailable(f"run {run_id} is being executed by another process")
        self._spawn(self.engine(mock=run.mock).resume(run_id))
        return run

    async def decide(
        self, approval_id: str, *, approved: bool, by: str, comment: str = ""
    ) -> ApprovalRecord:
        approval = self.store.get_approval(approval_id)
        if approval.status != "pending":
            raise CerebellumError(f"approval {approval_id} is already {approval.status}")
        run = self.store.get_run(approval.run_id)
        engine = self.engine(mock=run.mock)
        await engine.decide(
            run.run_id, approval.step_id, approved=approved, by=by, comment=comment, resume=False
        )
        # If another process drives the run, this resume yields (LeaseUnavailable) and that
        # process applies the decision before it would suspend.
        self._spawn(engine.resume(run.run_id))
        return self.store.get_approval(approval_id)

    def sweep(self) -> list[str]:
        """Resume runs whose pending approval is overdue; resuming applies `on_timeout`."""
        now = self.clock.now()
        due = sorted(
            {
                approval.run_id
                for approval in self.store.list_approvals(status="pending")
                if approval.expires_at is not None and approval.expires_at <= now
            }
        )
        resumed: list[str] = []
        for run_id in due:
            try:
                self.resume(run_id)
            except CerebellumError:
                continue  # driven elsewhere (its owner applies the timeout) or finished meanwhile
            resumed.append(run_id)
        return resumed

    def start(self) -> None:
        if self.interval > 0 and self._sweeper is None:
            self._sweeper = asyncio.create_task(self._sweep_forever(), name="approval-sweeper")

    async def stop(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
            self._sweeper = None
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def drain(self) -> None:
        """Wait until all background work has finished."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                self.sweep()
            except Exception:  # the sweeper must outlive one bad pass
                log.exception("approval sweep failed")

    def _spawn(self, work: Awaitable[RunRecord]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None and not isinstance(exc, LeaseUnavailable):
            log.error("background run work failed: %s", exc, exc_info=exc)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/server -q`
Expected: all PASS.

- [ ] **Step 5: Whole suite, lint, checkpoint**

Run: `.venv/bin/pytest tests -q && make fmt && make lint && git status --short`

---

### Task 4: Event stream and the FastAPI app

**Files:**
- Create: `src/cerebellum/server/stream.py`, `src/cerebellum/server/app.py`
- Modify: `src/cerebellum/server/__init__.py` (export `create_app`)
- Test: `tests/server/test_stream.py`, `tests/server/test_api.py`

**Interfaces:**
- Consumes: `Worker`, `Catalog`, `serialize`, `Store.metrics/last_seq/events_since`, `load_run_workflow`, `build_spans`, `parse_duration`, `NotFound`, `SpecError`.
- Produces: `event_stream(store, *, after, poll, is_disconnected, keepalive_every=30) -> AsyncIterator[str]` (first frame `"retry: 2000\n\n"`, then `id: <seq>\ndata: <event json>\n\n` per event, `": keep-alive\n\n"` when idle); `create_app(settings, *, provider, mode, workflows_dir, store=None, http_transports=None, static_dir=STATIC_DIR) -> FastAPI`; error mapping: `NotFound` → 404, `SpecError` → 400 `{"detail": {"message", "issues": [{path, message}]}}`, other `CerebellumError` → 409; routes as listed in Global Constraints; unknown `/api/*` → 404; any other GET → a static file, else `index.html`, else a page telling the user to run `make ui`.

- [ ] **Step 1: Write the failing tests**

`tests/server/test_stream.py`:

```python
import json
import threading
import time

import httpx
import pytest
import uvicorn

from cerebellum.ai.mock import MockProvider
from cerebellum.runtime.states import StepStatus
from cerebellum.runtime.store import Store
from cerebellum.server.app import create_app
from cerebellum.server.stream import event_stream


def fields(frame):
    return dict(line.split(": ", 1) for line in frame.strip().split("\n"))


async def test_event_stream_yields_new_events_in_order(store, simple_workflow):
    store.save_workflow(simple_workflow)
    store.create_run("r_s0000001", simple_workflow, {}, {}, mock=True)
    store.step_transition("r_s0000001", "first", StepStatus.RUNNING, event="started")
    checks = iter([False, False, True])

    async def disconnected():
        return next(checks)

    frames = [f async for f in event_stream(store, after=0, poll=0, is_disconnected=disconnected)]
    assert frames[0] == "retry: 2000\n\n"
    parsed = [fields(frame) for frame in frames[1:]]
    assert [json.loads(p["data"])["type"] for p in parsed] == ["run.started", "step.started"]
    assert [int(p["id"]) for p in parsed] == [1, 2]


async def test_event_stream_sends_keepalives_while_idle(store):
    checks = iter([False, False, False, True])

    async def disconnected():
        return next(checks)

    frames = [
        f
        async for f in event_stream(
            store, after=0, poll=0, is_disconnected=disconnected, keepalive_every=2
        )
    ]
    assert frames == ["retry: 2000\n\n", ": keep-alive\n\n"]


@pytest.fixture
def live_server(settings, free_port, tmp_path):
    app = create_app(
        settings,
        provider=MockProvider(latency=(0, 0)),
        mode="mock AI (requested)",
        workflows_dir=tmp_path,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=free_port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{free_port}"
    server.should_exit = True
    thread.join(timeout=10)


def first_event(response):
    for line in response.iter_lines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError("the stream ended without an event")


def test_stream_pushes_events_written_by_another_process(live_server, settings, simple_workflow):
    with Store(settings.db_path) as writer:
        with httpx.stream("GET", f"{live_server}/api/stream", timeout=10) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            writer.save_workflow(simple_workflow)
            writer.create_run("r_sse00001", simple_workflow, {}, {}, mock=True)
            event = first_event(response)
    assert event["type"] == "run.started" and event["run_id"] == "r_sse00001"


def test_stream_resumes_after_last_event_id(live_server, settings, simple_workflow):
    """Review focus: an EventSource reconnect continues exactly after the last event it saw."""
    with Store(settings.db_path) as writer:
        writer.save_workflow(simple_workflow)
        writer.create_run("r_sse00002", simple_workflow, {}, {}, mock=True)
        writer.step_transition("r_sse00002", "first", StepStatus.RUNNING, event="started")
        first, second = writer.get_events("r_sse00002")
        headers = {"Last-Event-ID": str(first.seq)}
        with httpx.stream("GET", f"{live_server}/api/stream", headers=headers, timeout=10) as r:
            event = first_event(r)
    assert event["seq"] == second.seq and event["type"] == "step.started"
```

`tests/server/test_api.py`:

```python
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from cerebellum.ai.mock import MockProvider
from cerebellum.sandbox.payments import FailMode, PaymentsState, create_payments_app
from cerebellum.server.app import create_app
from cerebellum.templates import template_path

WORKFLOW_ID = "workflow.yaml"  # the packaged refund template, scanned from its own directory


def sample(name):
    path = template_path("refund") / "inputs" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def payments():
    return PaymentsState()


@pytest.fixture
def client(store, settings, payments):
    app = create_app(
        settings,
        provider=MockProvider(latency=(0, 0)),
        mode="mock AI (requested)",
        workflows_dir=template_path("refund"),
        store=store,
        http_transports={"payments": httpx.ASGITransport(app=create_payments_app(payments))},
    )
    with TestClient(app) as test_client:
        yield test_client


def eventually(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached in time")


def settled(store, run_id, status):
    def check():
        run = store.get_run(run_id)
        return run.status.value == status and run.lease_owner is None

    eventually(check)
    return store.get_run(run_id)


def start(client, name):
    response = client.post("/api/runs", json={"workflow": WORKFLOW_ID, "input": sample(name)})
    assert response.status_code == 201, response.text
    return response.json()["run"]["run_id"]


def test_info_reports_mock_mode(client):
    info = client.get("/api/info").json()
    assert info["mock"] is True and info["mode"] == "mock AI (requested)"
    assert info["version"] == "0.2.0"


def test_small_refund_runs_in_the_background(client, store):
    run_id = start(client, "small")
    settled(store, run_id, "succeeded")
    [listed] = client.get("/api/runs").json()["runs"]
    assert listed["run_id"] == run_id and listed["status"] == "succeeded"
    assert listed["stale"] is False and listed["duration_s"] is not None
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["output"]["decision"] == "refunded"
    assert [s["step_id"] for s in detail["steps"]][:2] == ["fetch_order", "policy_check"]
    assert len(detail["steps"]) == 7
    assert detail["graph"]["steps"][-1]["id"] == "mark_refunded"
    assert any(span["label"] == "POST /refunds → 201" for span in detail["spans"])
    events = client.get(f"/api/runs/{run_id}/events").json()["events"]
    assert events[0]["type"] == "run.started"
    later = client.get(f"/api/runs/{run_id}/events", params={"after": events[-2]["seq"]})
    assert [e["seq"] for e in later.json()["events"]] == [events[-1]["seq"]]


def test_approval_flow_over_the_api(client, store):
    run_id = start(client, "large")
    settled(store, run_id, "waiting_approval")
    [approval] = client.get("/api/approvals").json()["approvals"]
    assert approval["run_id"] == run_id and approval["workflow_name"] == "refund_request"
    assert approval["context"]["input"]["amount"] == 899
    url = f"/api/approvals/{approval['id']}/decision"
    decided = client.post(url, json={"approved": True, "by": "  dana ", "comment": "fine"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["approval"]["decided_by"] == "dana"
    again = client.post(url, json={"approved": False, "by": "erin"})
    assert again.status_code == 409 and "already approved" in again.json()["detail"]
    settled(store, run_id, "succeeded")
    assert client.get("/api/approvals").json()["approvals"] == []
    [decided_one] = client.get("/api/approvals", params={"status": "all"}).json()["approvals"]
    assert decided_one["status"] == "approved"


def test_decisions_need_a_name_and_an_existing_approval(client, store):
    run_id = start(client, "large")
    settled(store, run_id, "waiting_approval")
    [approval] = client.get("/api/approvals").json()["approvals"]
    url = f"/api/approvals/{approval['id']}/decision"
    assert client.post(url, json={"approved": True, "by": "   "}).status_code == 422
    missing = client.post("/api/approvals/ap_missing/decision", json={"approved": True, "by": "x"})
    assert missing.status_code == 404


def test_outage_opens_a_task_that_can_be_resolved(client, store, payments):
    payments.set_fail_mode(FailMode.parse("always"))
    run_id = start(client, "outage")
    settled(store, run_id, "needs_attention")
    [task] = client.get("/api/tasks").json()["tasks"]
    assert task["assignee"] == "finance-ops" and task["workflow_name"] == "refund_request"
    url = f"/api/tasks/{task['id']}/resolve"
    resolved = client.post(url, json={"by": "ops", "note": "paid by hand"})
    assert resolved.status_code == 200 and resolved.json()["task"]["status"] == "resolved"
    assert client.get("/api/tasks").json()["tasks"] == []
    assert len(client.get("/api/tasks", params={"status": "all"}).json()["tasks"]) == 1
    assert client.post(url, json={"by": "ops"}).status_code == 409
    assert client.post("/api/tasks/tk_missing/resolve", json={"by": "ops"}).status_code == 404


def test_bad_requests_are_explained(client):
    body = {"workflow": WORKFLOW_ID, "input": {"order_id": "A1001", "amount": "120"}}
    bad = client.post("/api/runs", json=body)
    assert bad.status_code == 400
    assert any(issue["path"] == "input.amount" for issue in bad.json()["detail"]["issues"])
    assert client.post("/api/runs", json={"workflow": "nope.yaml"}).status_code == 404
    assert client.get("/api/runs/r_00000000").status_code == 404
    assert client.get("/api/runs", params={"status": "bogus"}).status_code == 400
    assert client.get("/api/metrics", params={"window": "soon"}).status_code == 400
    assert client.get("/api/approvals", params={"status": "maybe"}).status_code == 400
    assert client.get("/api/tasks", params={"status": "maybe"}).status_code == 400
    assert client.get("/api/does-not-exist").status_code == 404


def test_resume_over_the_api(client, store):
    done = start(client, "small")
    settled(store, done, "succeeded")
    assert client.post(f"/api/runs/{done}/resume").status_code == 409
    body = {"workflow": WORKFLOW_ID, "input": {"order_id": "ZZZ", "amount": 10}}
    failed = client.post("/api/runs", json=body).json()["run"]["run_id"]
    settled(store, failed, "failed")
    assert client.post(f"/api/runs/{failed}/resume").status_code == 202
    eventually(lambda: store.get_step(failed, "fetch_order").attempts == 2)
    settled(store, failed, "failed")


def test_metrics_and_workflows(client, store):
    run_id = start(client, "small")
    settled(store, run_id, "succeeded")
    metrics = client.get("/api/metrics").json()
    assert metrics["runs"] == 1 and metrics["success_rate"] == 1.0
    assert metrics["window"] == "24h"
    [workflow] = client.get("/api/workflows").json()["workflows"]
    assert workflow["id"] == WORKFLOW_ID and workflow["source"] == "file"
    assert workflow["runs"] == 1 and workflow["name"] == "refund_request"
    detail = client.get(f"/api/workflows/{WORKFLOW_ID}").json()
    assert set(detail["samples"]) == {"small", "large", "flaky", "outage", "fraud"}
    assert detail["yaml"].startswith("name: refund_request")
    assert detail["params"] == {"approval_threshold": 500}
    assert client.get("/api/workflows/missing.yaml").status_code == 404


def make_static(tmp_path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (static / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    return static


def app_with(store, settings, tmp_path, static_dir):
    return create_app(
        settings,
        provider=MockProvider(latency=(0, 0)),
        mode="mock AI (requested)",
        workflows_dir=tmp_path,
        store=store,
        static_dir=static_dir,
    )


def test_serves_the_ui_with_an_spa_fallback(store, settings, tmp_path):
    with TestClient(app_with(store, settings, tmp_path, make_static(tmp_path))) as c:
        assert "<title>ui</title>" in c.get("/").text
        assert "<title>ui</title>" in c.get("/runs/r_12345678").text
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/favicon.svg").text == "<svg/>"
        assert "secret" not in c.get("/..%2fsecret.txt").text
        assert c.get("/api/nope").status_code == 404


def test_explains_how_to_build_a_missing_ui(store, settings, tmp_path):
    with TestClient(app_with(store, settings, tmp_path, tmp_path / "not-built")) as c:
        page = c.get("/")
        assert page.status_code == 200 and "make ui" in page.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/server -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cerebellum.server.app'` (and `...stream`).

- [ ] **Step 3: Implement**

`src/cerebellum/server/stream.py`:

```python
"""Server-sent events: every new event in the store, in order — including events written by other
processes (the CLI), because the stream polls the shared event table."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from cerebellum.runtime.store import Store
from cerebellum.server.serialize import event_json


async def event_stream(
    store: Store,
    *,
    after: int,
    poll: float,
    is_disconnected: Callable[[], Awaitable[bool]],
    keepalive_every: int = 30,
) -> AsyncIterator[str]:
    seq = after
    idle = 0
    yield "retry: 2000\n\n"
    while not await is_disconnected():
        events = store.events_since(seq)
        for event in events:
            seq = event.seq
            yield f"id: {event.seq}\ndata: {json.dumps(event_json(event), default=str)}\n\n"
        if events:
            idle = 0
            continue
        idle += 1
        if idle >= keepalive_every:
            idle = 0
            yield ": keep-alive\n\n"
        await asyncio.sleep(poll)
```

`src/cerebellum/server/app.py`:

```python
"""The dashboard server: JSON API, live event stream and the prebuilt UI."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cerebellum import __version__
from cerebellum.ai.base import AIProvider
from cerebellum.config import Settings
from cerebellum.errors import CerebellumError, NotFound, SpecError
from cerebellum.runtime.engine import load_run_workflow
from cerebellum.runtime.states import RunStatus
from cerebellum.runtime.store import RunRecord, Store
from cerebellum.runtime.trace import build_spans
from cerebellum.server import serialize as js
from cerebellum.server.catalog import Catalog
from cerebellum.server.stream import event_stream
from cerebellum.server.worker import Worker
from cerebellum.spec.durations import parse_duration

STATIC_DIR = Path(__file__).parent / "static"
UI_MISSING = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Cerebellum</title></head>
<body style="background:#0a0b0d;color:#e3e6ea;font:14px ui-monospace,monospace;padding:48px">
<p>The dashboard UI has not been built.</p>
<p>Run <code>make ui</code> (Node 20.19+), then restart <code>cerebellum ui</code>.
The JSON API is available under <code>/api</code>.</p>
</body></html>
"""


class StartRun(BaseModel):
    workflow: str = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    approved: bool
    by: str = Field(max_length=80, pattern=r"\S")
    comment: str = Field("", max_length=2000)


class Resolution(BaseModel):
    by: str = Field(max_length=80, pattern=r"\S")
    note: str = Field("", max_length=2000)


def create_app(
    settings: Settings,
    *,
    provider: AIProvider,
    mode: str,
    workflows_dir: Path,
    store: Store | None = None,
    http_transports: Mapping[str, httpx.AsyncBaseTransport] | None = None,
    static_dir: Path = STATIC_DIR,
) -> FastAPI:
    """Build the dashboard app. Without `store`, the app opens and closes its own."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active = store if store is not None else Store(settings.db_path)
        worker = Worker(active, settings, provider, http_transports=http_transports)
        app.state.store = active
        app.state.worker = worker
        app.state.catalog = Catalog(active, workflows_dir)
        worker.start()
        try:
            yield
        finally:
            await worker.stop()
            if store is None:
                active.close()

    app = FastAPI(
        title="Cerebellum",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    @app.exception_handler(CerebellumError)
    async def cerebellum_error(request: Request, exc: CerebellumError) -> JSONResponse:
        if isinstance(exc, NotFound):
            return JSONResponse({"detail": str(exc)}, status_code=404)
        if isinstance(exc, SpecError):
            issues = [{"path": issue.path, "message": issue.message} for issue in exc.issues]
            return JSONResponse(
                {"detail": {"message": str(exc), "issues": issues}}, status_code=400
            )
        return JSONResponse({"detail": str(exc)}, status_code=409)

    def parts(request: Request) -> tuple[Store, Worker, Catalog]:
        state = request.app.state
        return state.store, state.worker, state.catalog

    def run_lookup(active: Store) -> Callable[[str], RunRecord]:
        cache: dict[str, RunRecord] = {}

        def get(run_id: str) -> RunRecord:
            if run_id not in cache:
                cache[run_id] = active.get_run(run_id)
            return cache[run_id]

        return get

    @app.get("/api/info")
    async def info() -> dict[str, Any]:
        return {
            "version": __version__,
            "mode": mode,
            "mock": provider.mock,
            "model": settings.model,
        }

    @app.get("/api/runs")
    async def list_runs(request: Request, status: str | None = None, limit: int = 50):
        active, _, _ = parts(request)
        wanted: RunStatus | None = None
        if status:
            try:
                wanted = RunStatus(status)
            except ValueError as exc:
                raise HTTPException(400, f"unknown status {status!r}") from exc
        runs = active.list_runs(status=wanted, limit=min(max(limit, 1), 500))
        return {"runs": [js.run_json(run, stale=active.is_stale(run)) for run in runs]}

    @app.post("/api/runs", status_code=201)
    async def start_run(body: StartRun, request: Request):
        _, worker, catalog = parts(request)
        try:
            entry = catalog.get(body.workflow)
        except KeyError as exc:
            raise HTTPException(404, f"unknown workflow {body.workflow!r}") from exc
        record = worker.start_run(entry.workflow, body.input, body.params)
        return {"run": js.run_json(record)}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, request: Request):
        active, _, _ = parts(request)
        run = active.get_run(run_id)
        try:
            graph = js.graph_json(load_run_workflow(active, run))
        except SpecError:
            graph = None  # the snapshot needs env vars this server does not have
        return {
            "run": js.run_json(run, stale=active.is_stale(run)),
            "steps": [js.step_json(step) for step in active.get_steps(run_id).values()],
            "graph": graph,
            "approvals": [js.approval_json(a, run) for a in active.list_approvals(run_id=run_id)],
            "tasks": [js.task_json(t, run) for t in active.list_tasks(run_id=run_id)],
            "spans": [js.span_json(span) for span in build_spans(active.get_events(run_id))],
        }

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, after: int = 0):
        active, _, _ = parts(request)
        active.get_run(run_id)
        return {"events": [js.event_json(e) for e in active.get_events(run_id, after_seq=after)]}

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    async def resume_run(run_id: str, request: Request):
        _, worker, _ = parts(request)
        return {"run": js.run_json(worker.resume(run_id))}

    @app.get("/api/approvals")
    async def list_approvals(request: Request, status: str = "pending"):
        active, _, _ = parts(request)
        if status not in ("pending", "all"):
            raise HTTPException(400, "status must be 'pending' or 'all'")
        items = active.list_approvals(status="pending" if status == "pending" else None)
        if status == "all":
            items.reverse()  # newest first; the pending queue stays oldest first
        run = run_lookup(active)
        return {"approvals": [js.approval_json(a, run(a.run_id)) for a in items]}

    @app.post("/api/approvals/{approval_id}/decision")
    async def decide(approval_id: str, body: Decision, request: Request):
        active, worker, _ = parts(request)
        approval = await worker.decide(
            approval_id,
            approved=body.approved,
            by=body.by.strip(),
            comment=body.comment.strip(),
        )
        return {"approval": js.approval_json(approval, active.get_run(approval.run_id))}

    @app.get("/api/tasks")
    async def list_tasks(request: Request, status: str = "open"):
        active, _, _ = parts(request)
        if status not in ("open", "all"):
            raise HTTPException(400, "status must be 'open' or 'all'")
        items = active.list_tasks(status="open" if status == "open" else None)
        if status == "all":
            items.reverse()
        run = run_lookup(active)
        return {"tasks": [js.task_json(t, run(t.run_id)) for t in items]}

    @app.post("/api/tasks/{task_id}/resolve")
    async def resolve_task(task_id: str, body: Resolution, request: Request):
        active, _, _ = parts(request)
        task = active.resolve_task(task_id, by=body.by.strip(), note=body.note.strip())
        return {"task": js.task_json(task, active.get_run(task.run_id))}

    @app.get("/api/metrics")
    async def metrics(request: Request, window: str = "24h"):
        active, _, _ = parts(request)
        try:
            seconds = parse_duration(window)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {**active.metrics(active.clock.now() - seconds), "window": window}

    @app.get("/api/workflows")
    async def list_workflows(request: Request):
        _, _, catalog = parts(request)
        return {"workflows": [js.workflow_summary(entry) for entry in catalog.entries()]}

    @app.get("/api/workflows/{workflow_id:path}")
    async def workflow_detail(workflow_id: str, request: Request):
        _, _, catalog = parts(request)
        try:
            return js.workflow_detail(catalog.get(workflow_id))
        except KeyError as exc:
            raise HTTPException(404, f"unknown workflow {workflow_id!r}") from exc

    @app.get("/api/stream")
    async def stream(request: Request, after: int | None = None) -> StreamingResponse:
        active, _, _ = parts(request)
        last = request.headers.get("last-event-id", "")
        if after is None:
            after = int(last) if last.isdigit() else active.last_seq()
        return StreamingResponse(
            event_stream(
                active,
                after=after,
                poll=settings.stream_poll,
                is_disconnected=request.is_disconnected,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def ui(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "not found")
        root = static_dir.resolve()
        candidate = (root / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(UI_MISSING)

    return app
```

`src/cerebellum/server/__init__.py`:

```python
"""Dashboard server: JSON API, live event stream, background worker and the prebuilt UI."""

from cerebellum.server.app import create_app

__all__ = ["create_app"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/server -q`
Expected: all PASS. If the live-server tests hang, the stream is not noticing the client disconnect; check `request.is_disconnected` is polled every loop (it is in `event_stream`) before changing anything else.

- [ ] **Step 5: Whole suite, lint, checkpoint**

Run: `.venv/bin/pytest tests -q && make fmt && make lint && git status --short`

---

### Task 5: `cerebellum ui`, demo hint, Makefile, README

**Files:**
- Modify: `src/cerebellum/cli/app.py`, `Makefile`, `README.md`
- Test: `tests/cli/test_cli.py` (append), `tests/cli/test_demo.py` (one assertion)

**Interfaces:**
- Consumes: `create_app`, `_sandbox`, `select_provider`, `Settings.ui_host/ui_port`.
- Produces: `cerebellum ui [--host] [--port] [--workflows DIR] [--no-sandbox] [--mock] [--open]`; module constant `LOOPBACK_HOSTS`; `demo` prints `cerebellum ui` with the dashboard URL; Make targets `ui-install`, `ui`, `ui-test`; `make test` runs vitest when `ui/node_modules` exists.

- [ ] **Step 1: Write the failing tests**

Append to `tests/cli/test_cli.py` (add `import os`, `from fastapi import FastAPI` and `from cerebellum.sandbox.server import sandbox_running` to the imports):

```python
def test_ui_serves_the_dashboard_on_localhost(runner, monkeypatch):
    calls = {}

    def fake_run(app, **kwargs):
        calls.update(kwargs, app=app)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    result = invoke(runner, "ui", "--port", "7555", "--no-sandbox", "--mock")
    assert result.exit_code == 0, result.text
    assert (calls["host"], calls["port"]) == ("127.0.0.1", 7555)
    assert isinstance(calls["app"], FastAPI)
    assert "http://127.0.0.1:7555" in result.text and "mock AI" in result.text
    assert "no authentication" not in result.text


def test_ui_warns_when_reachable_beyond_this_machine(runner, monkeypatch):
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    result = invoke(runner, "ui", "--host", "0.0.0.0", "--no-sandbox")
    assert result.exit_code == 0, result.text
    assert "no authentication" in result.text


def test_ui_starts_the_sandbox_and_points_payments_at_it(runner, monkeypatch):
    seen = {}

    def fake_run(app, **kwargs):
        seen["url"] = os.environ.get("PAYMENTS_URL")
        seen["healthy"] = sandbox_running(seen["url"])

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    monkeypatch.delenv("PAYMENTS_URL")
    result = invoke(runner, "ui")
    assert result.exit_code == 0, result.text
    assert seen["healthy"] is True and seen["url"].startswith("http://127.0.0.1:")
    assert "PAYMENTS_URL" not in os.environ
```

In `tests/cli/test_demo.py`, after `assert "cerebellum approve" in text` add:

```python
    assert "cerebellum ui" in text and "http://127.0.0.1:7400" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/cli -q`
Expected: FAIL — `No such command 'ui'` (exit code 2) and the missing demo hint.

- [ ] **Step 3: Implement**

`src/cerebellum/cli/app.py` — imports: add `import threading`, `import webbrowser`, `from cerebellum.server import create_app`. After `SANDBOX_URL_ENV = ...` add:

```python
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
```

New command (place it after `sandbox`):

```python
@app.command()
def ui(
    host: Annotated[str | None, typer.Option(help="Bind host (default 127.0.0.1).")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port (default 7400).")] = None,
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
    bind_port = port or settings.ui_port
    if bind_host not in LOOPBACK_HOSTS:
        err_console.print(
            Text("! ", style="yellow")
            + Text(
                f"the dashboard has no authentication; anyone who can reach "
                f"{bind_host}:{bind_port} can approve runs"
            )
        )
    choice = select_provider(settings, force_mock=mock)
    shown = "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host
    url = f"http://[{shown}]:{bind_port}" if ":" in shown else f"http://{shown}:{bind_port}"
    with _sandbox(settings, "never", enabled=not no_sandbox):
        dashboard = create_app(
            settings, provider=choice.provider, mode=choice.reason, workflows_dir=workflows
        )
        console.print(render.header("dashboard", f"{url} · {choice.reason}"))
        console.print(Text("  Ctrl-C to stop", style=render.MUTED))
        if open_browser:
            threading.Timer(1.0, webbrowser.open, args=(url,)).start()
        uvicorn.run(dashboard, host=bind_host, port=bind_port, log_level="warning")
```

In `demo`, after the `cerebellum runs` hint add:

```python
            console.print(
                Text("  cerebellum ui", style=render.ACCENT)
                + Text(
                    f"   dashboard at http://{settings.ui_host}:{settings.ui_port}",
                    style=render.MUTED,
                )
            )
```

`Makefile` — replace the `.PHONY` and `test` lines and add UI targets:

```make
UI := ui

.PHONY: install test lint fmt demo ui ui-install ui-test

test: lint
	$(BIN)/pytest -q
	@if [ -d $(UI)/node_modules ]; then cd $(UI) && npm test --silent; else echo "UI tests skipped: run 'make ui-install' first"; fi

ui-install:
	cd $(UI) && npm ci

ui: ui-install
	cd $(UI) && npm run build

ui-test:
	cd $(UI) && npm test
```

`README.md`:
- In the CLI table, add the row `| \`cerebellum ui [--port 7400] [--workflows dir] [--no-sandbox] [--open]\` | Local dashboard (runs, traces, approvals, tasks, workflows) |` before the `demo` row.
- In the Configuration table add rows `CEREBELLUM_UI_HOST` (`127.0.0.1`, dashboard bind host), `CEREBELLUM_UI_PORT` (`7400`, dashboard port), `CEREBELLUM_WORKER_INTERVAL` (`30`, seconds between approval-timeout sweeps in the dashboard).
- Add after "Reliability semantics":

```markdown
## Dashboard

```bash
cerebellum ui            # http://127.0.0.1:7400 — also starts the sandbox payments API
```

- **Overview** — runs, success rate, latency, cost, pending approvals, open tasks, retries and fallbacks over 24 h; the run list updates live.
- **Run detail** — the workflow DAG coloured by step status (running edges animate, fallbacks are dashed), a step inspector (output, errors, attempts, AI prompts and responses, HTTP and SQL calls) and the trace waterfall; resume failed runs and decide approvals in place.
- **Approvals** — decision cards with the context the step chose to `show`; your name is remembered in the browser.
- **Tasks** — the manual-task inbox that fallbacks fill.
- **Workflows** — YAML under `--workflows` (3 levels deep) plus every workflow runs were started from; YAML and DAG preview; start a run from a sample input.

The dashboard reads the same SQLite store as the CLI, so runs started or approved in a terminal appear live. It binds to `127.0.0.1` and has no authentication — it is a local tool, and `--host` with any other address prints a warning. The UI ships prebuilt inside the Python package; `make ui` rebuilds it (Node 20.19+).
```

- Architecture block: add the lines ` server/    FastAPI API · SSE stream · worker · prebuilt UI` and ` ui/        React + Vite + Tailwind + React Flow source (built into server/static)`.
- Development block: add `make ui          # rebuild the dashboard (npm ci + vite build)`.
- Roadmap: remove the `cerebellum ui` line (keep Evals).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/cli -q`
Expected: all PASS.

- [ ] **Step 5: Whole suite, lint, checkpoint**

Run: `.venv/bin/pytest tests -q && make fmt && make lint && git status --short`

---

### Task 6: UI scaffold, API client and tested data transforms

**Files:**
- Create: `ui/package.json`, `ui/tsconfig.json`, `ui/vite.config.ts`, `ui/index.html`, `ui/public/favicon.svg`, `ui/src/types.ts`, `ui/src/api.ts`, `ui/src/lib/format.ts`, `ui/src/lib/status.ts`, `ui/src/lib/layout.ts`, `ui/src/lib/waterfall.ts`, `ui/src/lib/runs.ts`, `ui/src/lib/invalidation.ts`
- Test: `ui/src/lib/format.test.ts`, `layout.test.ts`, `waterfall.test.ts`, `runs.test.ts`, `invalidation.test.ts`

**Interfaces:**
- Consumes: the JSON shapes from Tasks 2–4.
- Produces: `api.*` client (`info, runs, run, events, startRun, resume, approvals, decide, tasks, resolveTask, metrics, workflows, workflow`) and `ApiError(status, message, issues)`; types `Info, Run, Step, Approval, Task, RunEvent, RunDetail, Metrics, WorkflowSummary, WorkflowDetail`; `fmtDuration, fmtCost, fmtAge, fmtPercent, fmtAmount, fmtJson, prettyJson`; `STEP_LOOK, RUN_LOOK, SPAN_TONE, stepLook, runLook, toneColor`; `layoutGraph(graph) -> {nodes, edges}` with `NODE_W = 200`, `NODE_H = 52`; `layoutWaterfall(spans, now) -> WaterfallRow[]`; `defaultFocus(steps)`, `skeletonInput(fields)`, `isResumable(run)`; `keysFor(event)`.

- [ ] **Step 1: Scaffold the package and install dependencies**

`ui/package.json`:

```json
{
  "name": "cerebellum-ui",
  "private": true,
  "version": "0.2.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "test": "vitest run",
    "typecheck": "tsc --noEmit"
  }
}
```

Run (from `ui/`):

```bash
npm install react@^19 react-dom@^19 react-router@^7 @tanstack/react-query@^5 @xyflow/react@^12 @fontsource-variable/inter@^5 @fontsource-variable/jetbrains-mono@^5
npm install -D vite@^7 @vitejs/plugin-react@^5 tailwindcss@^4 @tailwindcss/vite@^4 typescript@~5.9 vitest@^3 @types/react@^19 @types/react-dom@^19
```

Expected: `package.json` gains `dependencies`/`devDependencies`; `package-lock.json` is created; `node_modules/` stays git-ignored.

`ui/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "isolatedModules": true,
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["vite/client"]
  },
  "include": ["src", "vite.config.ts"]
}
```

`ui/vite.config.ts`:

```ts
/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build lands inside the Python package so `cerebellum ui` needs no Node at runtime.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../src/cerebellum/server/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:7400" } },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
```

`ui/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="dark" />
    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
    <title>Cerebellum</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`ui/public/favicon.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#0a0b0d"/><circle cx="16" cy="16" r="7" fill="none" stroke="#2fd0e8" stroke-width="3"/><circle cx="16" cy="16" r="2.5" fill="#2fd0e8"/></svg>
```

- [ ] **Step 2: Write the failing tests**

`ui/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { fmtAge, fmtAmount, fmtCost, fmtDuration, fmtJson, fmtPercent, prettyJson } from "./format";

describe("format", () => {
  it("formats durations like the CLI", () => {
    expect(fmtDuration(null)).toBe("");
    expect(fmtDuration(0.0424)).toBe("42ms");
    expect(fmtDuration(15.623)).toBe("15.62s");
    expect(fmtDuration(90)).toBe("1.5m");
    expect(fmtDuration(5400)).toBe("1.5h");
  });

  it("hides zero cost and formats real cost", () => {
    expect(fmtCost(0)).toBe("");
    expect(fmtCost(0.01234)).toBe("$0.0123");
  });

  it("formats ages, percentages and amounts", () => {
    expect(fmtAge(100, 159)).toBe("59s ago");
    expect(fmtAge(0, 7200)).toBe("2h ago");
    expect(fmtPercent(null)).toBe("—");
    expect(fmtPercent(0.875)).toBe("88%");
    expect(fmtAmount(1234.5)).toBe("1,234.5");
    expect(fmtAmount("n/a")).toBe("n/a");
  });

  it("pretty-prints JSON values and JSON text", () => {
    expect(fmtJson({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(fmtJson(undefined)).toBe("");
    expect(prettyJson('{"ok":true}')).toBe('{\n  "ok": true\n}');
    expect(prettyJson("not json")).toBe("not json");
  });
});
```

`ui/src/lib/layout.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { type Graph, layoutGraph, NODE_W } from "./layout";

const graph: Graph = {
  steps: [
    { id: "a", type: "query", description: "", needs: [], when: null, fallback: null },
    { id: "b", type: "http", description: "", needs: ["a"], when: null, fallback: "rescue" },
    { id: "c", type: "task", description: "", needs: ["a"], when: null, fallback: null },
    { id: "d", type: "task", description: "", needs: ["b", "c"], when: null, fallback: null },
  ],
  fallbacks: [{ id: "rescue", type: "task", description: "", fallback_for: "b" }],
};

const byId = () => Object.fromEntries(layoutGraph(graph).nodes.map((n) => [n.id, n]));

describe("layoutGraph", () => {
  it("places each step one level below its deepest dependency", () => {
    const at = byId();
    expect([at.a.level, at.b.level, at.c.level, at.d.level]).toEqual([0, 1, 1, 2]);
    expect(at.b.y).toBe(at.c.y);
    expect(at.d.y).toBeGreaterThan(at.b.y);
  });

  it("centres each level and keeps siblings apart", () => {
    const at = byId();
    expect(at.a.x + NODE_W / 2).toBe(0);
    expect(at.b.x + at.c.x + NODE_W).toBe(0);
    expect(at.c.x - at.b.x).toBeGreaterThan(NODE_W);
  });

  it("puts fallbacks in their own column, level with the step they rescue", () => {
    const { nodes } = layoutGraph(graph);
    const at = byId();
    const right = Math.max(...nodes.filter((n) => !n.fallback).map((n) => n.x + NODE_W));
    expect(at.rescue.fallback).toBe(true);
    expect(at.rescue.y).toBe(at.b.y);
    expect(at.rescue.x).toBeGreaterThan(right);
  });

  it("draws dependency edges and a fallback edge", () => {
    const { edges } = layoutGraph(graph);
    expect(edges.map((e) => e.id)).toEqual(["a->b", "b~>rescue", "a->c", "b->d", "c->d"]);
    expect(edges.filter((e) => e.fallback).map((e) => e.target)).toEqual(["rescue"]);
  });
});
```

`ui/src/lib/waterfall.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { layoutWaterfall, type Span } from "./waterfall";

const spans: Span[] = [
  { span_id: "a#1", parent_id: null, step_id: "a", kind: "step", label: "a #1", start: 10, end: 12, status: "succeeded" },
  { span_id: "b#1", parent_id: null, step_id: "b", kind: "step", label: "b #1", start: 12, end: null, status: "waiting" },
  { span_id: "a#1:connector:x", parent_id: "a#1", step_id: "a", kind: "connector", label: "sql query", start: 11, end: 11.5, status: "succeeded" },
];

describe("layoutWaterfall", () => {
  it("nests calls under their step attempt in start order", () => {
    const rows = layoutWaterfall(spans, 20);
    expect(rows.map((r) => [r.span.span_id, r.depth])).toEqual([
      ["a#1", 0],
      ["a#1:connector:x", 1],
      ["b#1", 0],
    ]);
  });

  it("positions bars as percentages and extends open spans to now", () => {
    const [a, call, b] = layoutWaterfall(spans, 20);
    expect(a.offset).toBe(0);
    expect(a.width).toBeCloseTo(20);
    expect(call.offset).toBeCloseTo(10);
    expect(call.width).toBeCloseTo(5);
    expect(b.offset).toBeCloseTo(20);
    expect(b.width).toBeCloseTo(80);
    expect(b.duration).toBe(8);
  });

  it("keeps zero-length spans visible and handles no spans", () => {
    const [row] = layoutWaterfall([{ ...spans[0], start: 5, end: 5 }], 5);
    expect(row.width).toBeGreaterThan(0);
    expect(layoutWaterfall([], 1)).toEqual([]);
  });
});
```

`ui/src/lib/runs.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { Step } from "../types";
import { defaultFocus, isResumable, skeletonInput } from "./runs";

const step = (step_id: string, status: Step["status"], started_at: number | null = null): Step => ({
  run_id: "r_1",
  step_id,
  status,
  attempts: started_at == null ? 0 : 1,
  output: null,
  error: null,
  started_at,
  ended_at: null,
  cost_usd: 0,
  duration_s: null,
});

describe("runs helpers", () => {
  it("focuses a step that needs attention first", () => {
    expect(defaultFocus([step("a", "succeeded", 1), step("b", "waiting", 2), step("c", "failed", 3)])).toBe("b");
    expect(defaultFocus([step("a", "succeeded", 1), step("b", "failed", 2)])).toBe("b");
  });

  it("otherwise focuses the step that ran last", () => {
    expect(defaultFocus([step("a", "succeeded", 1), step("b", "succeeded", 2), step("c", "pending")])).toBe("b");
    expect(defaultFocus([step("a", "pending")])).toBe("a");
    expect(defaultFocus([])).toBeNull();
  });

  it("builds an input skeleton from the input schema", () => {
    const f = (type: string, choices: unknown[] | null = null) => ({ type, required: true, enum: choices, description: "" });
    expect(skeletonInput({ id: f("string"), n: f("number"), ok: f("boolean"), kind: f("string", ["a", "b"]), tags: f("array") })).toEqual({
      id: "",
      n: 0,
      ok: false,
      kind: "a",
      tags: [],
    });
  });

  it("offers resume for failed or stale runs only", () => {
    expect(isResumable({ status: "failed", stale: false })).toBe(true);
    expect(isResumable({ status: "running", stale: true })).toBe(true);
    expect(isResumable({ status: "succeeded", stale: false })).toBe(false);
  });
});
```

`ui/src/lib/invalidation.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { keysFor } from "./invalidation";

describe("keysFor", () => {
  it("refreshes run lists, metrics and the run itself for every event", () => {
    expect(keysFor({ type: "step.succeeded", run_id: "r_1" })).toEqual([["runs"], ["metrics"], ["run", "r_1"]]);
  });

  it("refreshes approvals, tasks and workflows when they can change", () => {
    expect(keysFor({ type: "approval.requested", run_id: "r_1" })).toContainEqual(["approvals"]);
    expect(keysFor({ type: "run.suspended", run_id: "r_1" })).toContainEqual(["approvals"]);
    expect(keysFor({ type: "task.created", run_id: "r_1" })).toContainEqual(["tasks"]);
    expect(keysFor({ type: "run.started", run_id: "r_1" })).toContainEqual(["workflows"]);
    expect(keysFor({ type: "step.started", run_id: "r_1" })).not.toContainEqual(["tasks"]);
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd ui && npx vitest run`
Expected: FAIL — `Failed to resolve import "./format"` (and the other modules).

- [ ] **Step 4: Implement**

`ui/src/lib/format.ts`:

```ts
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(2)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export function fmtCost(usd: number | null | undefined): string {
  return usd ? `$${usd.toFixed(4)}` : "";
}

export function fmtAge(ts: number, now: number): string {
  const delta = Math.max(now - ts, 0);
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export function fmtPercent(ratio: number | null | undefined): string {
  return ratio == null ? "—" : `${Math.round(ratio * 100)}%`;
}

export function fmtAmount(value: unknown): string {
  return typeof value === "number" ? value.toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(value);
}

export function fmtJson(value: unknown): string {
  return value === undefined ? "" : JSON.stringify(value, null, 2);
}

/** Pretty-print text that holds JSON (e.g. an LLM response); other text is returned as is. */
export function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}
```

`ui/src/lib/status.ts`:

```ts
export type StepStatus =
  | "pending"
  | "running"
  | "retrying"
  | "waiting"
  | "succeeded"
  | "failed"
  | "skipped"
  | "cancelled"
  | "recovered";
export type RunStatus = "running" | "waiting_approval" | "succeeded" | "failed" | "rejected" | "needs_attention";
export type Tone = "accent" | "ok" | "wait" | "fail" | "fallback" | "skip" | "faint";

export interface Look {
  glyph: string;
  tone: Tone;
  label: string;
}

export const STEP_LOOK: Record<StepStatus, Look> = {
  pending: { glyph: "○", tone: "faint", label: "pending" },
  running: { glyph: "◐", tone: "accent", label: "running" },
  retrying: { glyph: "↻", tone: "wait", label: "retrying" },
  waiting: { glyph: "⏸", tone: "wait", label: "awaiting approval" },
  succeeded: { glyph: "●", tone: "ok", label: "succeeded" },
  failed: { glyph: "✕", tone: "fail", label: "failed" },
  skipped: { glyph: "⊘", tone: "skip", label: "skipped" },
  cancelled: { glyph: "⊗", tone: "skip", label: "cancelled" },
  recovered: { glyph: "⤳", tone: "fallback", label: "recovered" },
};

export const RUN_LOOK: Record<RunStatus, Look> = {
  running: { glyph: "◐", tone: "accent", label: "running" },
  waiting_approval: { glyph: "⏸", tone: "wait", label: "waiting approval" },
  succeeded: { glyph: "●", tone: "ok", label: "succeeded" },
  failed: { glyph: "✕", tone: "fail", label: "failed" },
  rejected: { glyph: "✕", tone: "fail", label: "rejected" },
  needs_attention: { glyph: "⤳", tone: "fallback", label: "needs attention" },
};

export const SPAN_TONE: Record<string, Tone> = {
  succeeded: "ok",
  failed: "fail",
  retrying: "wait",
  waiting: "wait",
  running: "accent",
};

const UNKNOWN = (status: string): Look => ({ glyph: "·", tone: "faint", label: status });

export function stepLook(status: string): Look {
  return STEP_LOOK[status as StepStatus] ?? UNKNOWN(status);
}

export function runLook(status: string): Look {
  return RUN_LOOK[status as RunStatus] ?? UNKNOWN(status);
}

/** Every colour is a CSS token defined in index.css. */
export function toneColor(tone: Tone): string {
  return `var(--color-${tone})`;
}
```

`ui/src/lib/layout.ts`:

```ts
export interface GraphStep {
  id: string;
  type: string;
  description: string;
  needs: string[];
  when: string | null;
  fallback: string | null;
}

export interface GraphFallback {
  id: string;
  type: string;
  description: string;
  fallback_for: string | null;
}

export interface Graph {
  steps: GraphStep[];
  fallbacks: GraphFallback[];
}

export interface LaidNode {
  id: string;
  type: string;
  x: number;
  y: number;
  level: number;
  fallback: boolean;
}

export interface LaidEdge {
  id: string;
  source: string;
  target: string;
  fallback: boolean;
}

export const NODE_W = 200;
export const NODE_H = 52;
const GAP_X = 40;
const GAP_Y = 44;

/** Top-to-bottom layered layout: a step sits one level below its deepest dependency, each level is
 * centred on x = 0, and fallbacks get their own column to the right, level with the step they rescue. */
export function layoutGraph(graph: Graph): { nodes: LaidNode[]; edges: LaidEdge[] } {
  const byId = new Map(graph.steps.map((step) => [step.id, step]));
  const levels = new Map<string, number>();
  const depth = (id: string, visiting: Set<string>): number => {
    const known = levels.get(id);
    if (known !== undefined) return known;
    const step = byId.get(id);
    if (!step || visiting.has(id)) return 0; // the loader rejects cycles; never loop here
    visiting.add(id);
    const value = step.needs.length ? 1 + Math.max(...step.needs.map((dep) => depth(dep, visiting))) : 0;
    visiting.delete(id);
    levels.set(id, value);
    return value;
  };

  const rows = new Map<number, GraphStep[]>();
  for (const step of graph.steps) {
    const level = depth(step.id, new Set());
    rows.set(level, [...(rows.get(level) ?? []), step]);
  }

  const nodes: LaidNode[] = [];
  let right = NODE_W / 2;
  for (const [level, steps] of [...rows.entries()].sort((a, b) => a[0] - b[0])) {
    const width = steps.length * NODE_W + (steps.length - 1) * GAP_X;
    steps.forEach((step, index) => {
      const x = -width / 2 + index * (NODE_W + GAP_X);
      right = Math.max(right, x + NODE_W);
      nodes.push({ id: step.id, type: step.type, x, y: level * (NODE_H + GAP_Y), level, fallback: false });
    });
  }

  const taken = new Set<number>();
  for (const fallback of graph.fallbacks) {
    const user = nodes.find((node) => node.id === fallback.fallback_for);
    let y = user ? user.y : 0;
    while (taken.has(y)) y += NODE_H + 12;
    taken.add(y);
    nodes.push({ id: fallback.id, type: fallback.type, x: right + GAP_X * 2, y, level: user?.level ?? 0, fallback: true });
  }

  const edges: LaidEdge[] = [];
  for (const step of graph.steps) {
    for (const dep of step.needs) edges.push({ id: `${dep}->${step.id}`, source: dep, target: step.id, fallback: false });
    if (step.fallback) {
      edges.push({ id: `${step.id}~>${step.fallback}`, source: step.id, target: step.fallback, fallback: true });
    }
  }
  return { nodes, edges };
}
```

`ui/src/lib/waterfall.ts`:

```ts
export interface Span {
  span_id: string;
  parent_id: string | null;
  step_id: string | null;
  kind: string;
  label: string;
  start: number;
  end: number | null;
  status: string;
}

export interface WaterfallRow {
  span: Span;
  depth: number;
  offset: number; // % of the run's time range
  width: number; // % of the run's time range
  duration: number; // seconds
}

/** Waterfall rows: each step attempt followed by its LLM / connector calls, bars as percentages of
 * the run's time range. Open spans (running, waiting) extend to `now`. */
export function layoutWaterfall(spans: Span[], now: number): WaterfallRow[] {
  if (!spans.length) return [];
  const t0 = Math.min(...spans.map((span) => span.start));
  const t1 = Math.max(...spans.map((span) => span.end ?? now));
  const total = Math.max(t1 - t0, 1e-9);
  const ids = new Set(spans.map((span) => span.span_id));
  const children = new Map<string, Span[]>();
  const roots: Span[] = [];
  for (const span of spans) {
    if (span.parent_id && ids.has(span.parent_id)) {
      children.set(span.parent_id, [...(children.get(span.parent_id) ?? []), span]);
    } else {
      roots.push(span);
    }
  }
  const byStart = (a: Span, b: Span) => a.start - b.start;
  const rows: WaterfallRow[] = [];
  const add = (span: Span, depth: number) => {
    const end = span.end ?? now;
    const offset = Math.min(((span.start - t0) / total) * 100, 99.5);
    const width = Math.min(Math.max(((end - span.start) / total) * 100, 0.5), 100 - offset);
    rows.push({ span, depth, offset, width, duration: end - span.start });
    for (const child of [...(children.get(span.span_id) ?? [])].sort(byStart)) add(child, depth + 1);
  };
  for (const root of [...roots].sort(byStart)) add(root, 0);
  return rows;
}
```

`ui/src/lib/runs.ts`:

```ts
import type { Step } from "../types";

const ATTENTION = ["waiting", "failed", "running", "retrying"] as const;

/** The step a person most likely wants to inspect: one that needs attention, else the last that ran. */
export function defaultFocus(steps: Step[]): string | null {
  for (const status of ATTENTION) {
    const step = steps.find((s) => s.status === status);
    if (step) return step.step_id;
  }
  const ran = steps.filter((s) => s.started_at != null);
  if (ran.length) return ran.reduce((a, b) => ((b.started_at ?? 0) >= (a.started_at ?? 0) ? b : a)).step_id;
  return steps[0]?.step_id ?? null;
}

export interface InputField {
  type: string;
  required: boolean;
  enum: unknown[] | null;
  description: string;
}

const BLANK: Record<string, unknown> = { string: "", number: 0, integer: 0, boolean: false, object: {}, array: [] };

/** A starting input for the new-run form when the workflow ships no sample inputs. */
export function skeletonInput(fields: Record<string, InputField>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(fields).map(([name, field]) => [name, field.enum?.length ? field.enum[0] : (BLANK[field.type] ?? "")]),
  );
}

export function isResumable(run: { status: string; stale: boolean }): boolean {
  return run.status === "failed" || run.stale;
}
```

`ui/src/lib/invalidation.ts`:

```ts
import type { RunEvent } from "../types";

/** The query keys an incoming event makes stale (prefix match in TanStack Query). */
export function keysFor(event: Pick<RunEvent, "type" | "run_id">): string[][] {
  const keys = [["runs"], ["metrics"], ["run", event.run_id]];
  if (event.type.startsWith("approval.") || event.type.startsWith("run.")) keys.push(["approvals"]);
  if (event.type.startsWith("task.")) keys.push(["tasks"]);
  if (event.type === "run.started") keys.push(["workflows"]);
  return keys;
}
```

`ui/src/types.ts`:

```ts
import type { Graph } from "./lib/layout";
import type { InputField } from "./lib/runs";
import type { RunStatus, StepStatus } from "./lib/status";
import type { Span } from "./lib/waterfall";

export interface Info {
  version: string;
  mode: string;
  mock: boolean;
  model: string;
}

export interface Run {
  run_id: string;
  workflow_digest: string;
  workflow_name: string;
  status: RunStatus;
  input: Record<string, unknown>;
  params: Record<string, unknown>;
  output: unknown;
  error: string | null;
  cost_usd: number;
  mock: boolean;
  created_at: number;
  updated_at: number;
  ended_at: number | null;
  duration_s: number | null;
  stale: boolean;
}

export interface Step {
  run_id: string;
  step_id: string;
  status: StepStatus;
  attempts: number;
  output: unknown;
  error: string | null;
  started_at: number | null;
  ended_at: number | null;
  cost_usd: number;
  duration_s: number | null;
}

export interface Approval {
  id: string;
  run_id: string;
  step_id: string;
  status: "pending" | "approved" | "rejected";
  title: string;
  context: { input?: Record<string, unknown>; steps?: Record<string, unknown> };
  requested_at: number;
  expires_at: number | null;
  on_timeout: string;
  decided_at: number | null;
  decided_by: string | null;
  comment: string | null;
  workflow_name?: string;
  run_status?: RunStatus;
}

export interface Task {
  id: string;
  run_id: string;
  step_id: string;
  title: string;
  assignee: string;
  payload: Record<string, unknown>;
  status: "open" | "resolved";
  created_at: number;
  resolved_at: number | null;
  resolved_by: string | null;
  note: string | null;
  workflow_name?: string;
}

export interface RunEvent {
  seq: number;
  run_id: string;
  step_id: string | null;
  span_id: string | null;
  parent_span_id: string | null;
  type: string;
  ts: number;
  data: Record<string, unknown>;
}

export interface RunDetail {
  run: Run;
  steps: Step[];
  graph: Graph | null;
  approvals: Approval[];
  tasks: Task[];
  spans: Span[];
}

export interface Metrics {
  since: number;
  window: string;
  runs: number;
  by_status: Record<string, number>;
  success_rate: number | null;
  avg_duration_s: number | null;
  cost_usd: number;
  pending_approvals: number;
  open_tasks: number;
  retries: number;
  fallbacks: number;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  version: number;
  description: string;
  source: "file" | "history";
  path: string | null;
  digest: string;
  steps: number;
  runs: number;
  last_run_at: number | null;
}

export interface WorkflowDetail extends WorkflowSummary {
  yaml: string;
  graph: Graph;
  params: Record<string, unknown>;
  input: Record<string, InputField>;
  samples: Record<string, Record<string, unknown>>;
}
```

`ui/src/api.ts`:

```ts
import type { Approval, Info, Metrics, Run, RunDetail, RunEvent, Task, WorkflowDetail, WorkflowSummary } from "./types";

export interface Issue {
  path: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly issues: Issue[];

  constructor(status: number, message: string, issues: Issue[] = []) {
    super(message);
    this.status = status;
    this.issues = issues;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...init, headers: { "Content-Type": "application/json" } });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let issues: Issue[] = [];
    try {
      const { detail } = await response.json();
      if (typeof detail === "string") message = detail;
      else if (Array.isArray(detail)) message = detail.map((d) => `${(d.loc ?? []).join(".")}: ${d.msg}`).join("; ");
      else if (detail && typeof detail === "object") {
        message = detail.message ?? message;
        issues = detail.issues ?? [];
      }
    } catch {
      // not a JSON error body; keep the status line
    }
    throw new ApiError(response.status, message, issues);
  }
  return (await response.json()) as T;
}

const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
const workflowPath = (id: string) => id.split("/").map(encodeURIComponent).join("/");

export const api = {
  info: () => request<Info>("/info"),
  runs: (status?: string) => request<{ runs: Run[] }>(status ? `/runs?status=${status}` : "/runs").then((r) => r.runs),
  run: (id: string) => request<RunDetail>(`/runs/${id}`),
  events: (id: string) => request<{ events: RunEvent[] }>(`/runs/${id}/events`).then((r) => r.events),
  startRun: (workflow: string, input: unknown, params: unknown) =>
    post<{ run: Run }>("/runs", { workflow, input, params }).then((r) => r.run),
  resume: (id: string) => post<{ run: Run }>(`/runs/${id}/resume`, {}).then((r) => r.run),
  approvals: (status: "pending" | "all") =>
    request<{ approvals: Approval[] }>(`/approvals?status=${status}`).then((r) => r.approvals),
  decide: (id: string, approved: boolean, by: string, comment: string) =>
    post<{ approval: Approval }>(`/approvals/${id}/decision`, { approved, by, comment }).then((r) => r.approval),
  tasks: (status: "open" | "all") => request<{ tasks: Task[] }>(`/tasks?status=${status}`).then((r) => r.tasks),
  resolveTask: (id: string, by: string, note: string) =>
    post<{ task: Task }>(`/tasks/${id}/resolve`, { by, note }).then((r) => r.task),
  metrics: (window = "24h") => request<Metrics>(`/metrics?window=${window}`),
  workflows: () => request<{ workflows: WorkflowSummary[] }>("/workflows").then((r) => r.workflows),
  workflow: (id: string) => request<WorkflowDetail>(`/workflows/${workflowPath(id)}`),
};
```

- [ ] **Step 5: Run the tests and the type check**

Run: `cd ui && npx vitest run && npx tsc --noEmit`
Expected: all vitest suites PASS; `tsc` prints nothing.

- [ ] **Step 6: Checkpoint**

Run: `git status --short`

---

### Task 7: The dashboard UI

**Files:**
- Create: `ui/src/main.tsx`, `ui/src/App.tsx`, `ui/src/index.css`, `ui/src/hooks/useEventStream.ts`, `ui/src/hooks/useNow.ts`, `ui/src/hooks/useApprover.ts`, `ui/src/components/ui.tsx`, `ui/src/components/Shell.tsx`, `ui/src/components/RunsTable.tsx`, `ui/src/components/RunGraph.tsx`, `ui/src/components/StepInspector.tsx`, `ui/src/components/Waterfall.tsx`, `ui/src/components/ApprovalCard.tsx`, `ui/src/components/NewRunDialog.tsx`, `ui/src/pages/Overview.tsx`, `ui/src/pages/RunDetail.tsx`, `ui/src/pages/Approvals.tsx`, `ui/src/pages/Tasks.tsx`, `ui/src/pages/Workflows.tsx`
- Generated: `src/cerebellum/server/static/**` (by `npm run build`)
- Test: `tests/server/test_packaged_ui.py`

**Interfaces:**
- Consumes: everything from Task 6; the API from Task 4.
- Produces: the five pages; the built bundle in `src/cerebellum/server/static/` (`index.html`, `assets/*`, `favicon.svg`).

- [ ] **Step 1: Write the failing test**

`tests/server/test_packaged_ui.py`:

```python
from cerebellum.server.app import STATIC_DIR


def test_the_dashboard_ui_is_built_into_the_package():
    index = STATIC_DIR / "index.html"
    assert index.is_file(), "run `make ui` to build the dashboard"
    html = index.read_text(encoding="utf-8")
    assert "<title>Cerebellum</title>" in html and "/assets/" in html
    assert any(STATIC_DIR.joinpath("assets").glob("*.js"))
    assert any(STATIC_DIR.joinpath("assets").glob("*.css"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/server -q`
Expected: FAIL — `run \`make ui\` to build the dashboard`.

- [ ] **Step 3: Implement the app shell, styles and hooks**

`ui/src/index.css`:

```css
@import "tailwindcss";

@theme static {
  --color-bg: #0a0b0d;
  --color-panel: #0e1013;
  --color-raised: #14171b;
  --color-line: #1e2227;
  --color-line-strong: #2c3138;
  --color-text: #e3e6ea;
  --color-muted: #8b929b;
  --color-faint: #5d646d;
  --color-accent: #2fd0e8;
  --color-ok: #5cb98a;
  --color-wait: #d2a14f;
  --color-fail: #dc6a6a;
  --color-fallback: #a189e4;
  --color-skip: #6b717a;
  --font-sans: "Inter Variable", ui-sans-serif, system-ui, sans-serif;
  --font-mono: "JetBrains Mono Variable", ui-monospace, SFMono-Regular, Menlo, monospace;
}

html,
body,
#root {
  height: 100%;
}

body {
  margin: 0;
  background: var(--color-bg);
  color: var(--color-text);
  font-family: var(--font-sans);
  font-size: 13px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
}

::selection {
  background: color-mix(in oklab, var(--color-accent) 28%, transparent);
}

@layer components {
  .label {
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--color-faint);
  }

  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  .input,
  .input-area {
    width: 100%;
    border: 1px solid var(--color-line);
    background: var(--color-bg);
    color: var(--color-text);
    font-size: 12px;
    outline: none;
  }

  .input {
    height: 2rem;
    padding: 0 0.6rem;
  }

  .input-area {
    padding: 0.5rem 0.6rem;
    resize: vertical;
  }

  .input:focus,
  .input-area:focus {
    border-color: color-mix(in oklab, var(--color-accent) 70%, transparent);
  }
}

/* React Flow: dark canvas, hairline edges, dotted grid, no shadows. */
.react-flow {
  background: var(--color-bg);
}

.react-flow__background circle {
  fill: var(--color-line-strong);
}

.react-flow__node {
  box-shadow: none !important;
}

.react-flow__controls {
  box-shadow: none;
  border: 1px solid var(--color-line);
}

.react-flow__controls-button {
  background: var(--color-panel);
  border-bottom: 1px solid var(--color-line);
  fill: var(--color-muted);
}

.react-flow__controls-button:hover {
  background: var(--color-raised);
}

.react-flow__attribution {
  background: transparent;
}

.react-flow__attribution a {
  color: var(--color-faint);
}
```

`ui/src/main.tsx`:

```tsx
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "@xyflow/react/dist/style.css";
import "./index.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

`ui/src/App.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { Shell } from "./components/Shell";
import { Empty, PageHeader } from "./components/ui";
import { Approvals } from "./pages/Approvals";
import { Overview } from "./pages/Overview";
import { RunDetail } from "./pages/RunDetail";
import { Tasks } from "./pages/Tasks";
import { Workflows } from "./pages/Workflows";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 2_000, refetchOnWindowFocus: false, retry: 1 } },
});

function NotFound() {
  return (
    <div>
      <PageHeader title="Not found" />
      <Empty>there is no page here</Empty>
    </div>
  );
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Overview /> },
      { path: "runs/:runId", element: <RunDetail /> },
      { path: "approvals", element: <Approvals /> },
      { path: "tasks", element: <Tasks /> },
      { path: "workflows", element: <Workflows /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
```

`ui/src/hooks/useNow.ts`:

```ts
import { useEffect, useState } from "react";

/** Wall-clock seconds (like the server's timestamps), refreshed so ages and running durations tick. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}
```

`ui/src/hooks/useApprover.ts`:

```ts
import { useState } from "react";

const KEY = "cerebellum.approver";

/** The name recorded with decisions and task resolutions, remembered in this browser. */
export function useApprover(): [string, (name: string) => void] {
  const [name, setName] = useState(() => {
    try {
      return window.localStorage.getItem(KEY) ?? "";
    } catch {
      return "";
    }
  });
  const update = (value: string) => {
    setName(value);
    try {
      window.localStorage.setItem(KEY, value);
    } catch {
      // storage unavailable (private mode); the name just isn't remembered
    }
  };
  return [name, update];
}
```

`ui/src/hooks/useEventStream.ts`:

```ts
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { keysFor } from "../lib/invalidation";
import type { RunEvent } from "../types";

/** Subscribe to /api/stream and refresh the affected queries (batched every 250 ms). */
export function useEventStream(): boolean {
  const client = useQueryClient();
  const [live, setLive] = useState(false);
  useEffect(() => {
    const source = new EventSource("/api/stream");
    const pending = new Set<string>();
    let timer: number | undefined;
    const flush = () => {
      timer = undefined;
      for (const key of pending) void client.invalidateQueries({ queryKey: JSON.parse(key) });
      pending.clear();
    };
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false);
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as RunEvent;
      for (const key of keysFor(event)) pending.add(JSON.stringify(key));
      timer ??= window.setTimeout(flush, 250);
    };
    return () => {
      source.close();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [client]);
  return live;
}
```

- [ ] **Step 4: Implement shared components**

`ui/src/components/ui.tsx`:

```tsx
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { fmtJson } from "../lib/format";
import { runLook, stepLook, toneColor } from "../lib/status";

export function StatusChip({ status, kind = "run" }: { status: string; kind?: "run" | "step" }) {
  const look = kind === "run" ? runLook(status) : stepLook(status);
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" style={{ color: toneColor(look.tone) }}>
      <span className="mono text-[11px]">{look.glyph}</span>
      <span>{look.label}</span>
    </span>
  );
}

export function Label({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`label ${className}`}>{children}</div>;
}

export function Panel({
  title,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`border border-line bg-panel ${className}`}>
      {title !== undefined && (
        <header className="flex h-9 items-center justify-between border-b border-line px-3">
          <Label>{title}</Label>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex h-14 items-center justify-between gap-4 border-b border-line px-6">
      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="truncate text-[15px] font-medium text-text">{title}</h1>
        {subtitle && <span className="truncate text-[12px] text-faint">{subtitle}</span>}
      </div>
      {actions}
    </header>
  );
}

export function JsonBlock({ value, className = "" }: { value: unknown; className?: string }) {
  return (
    <pre className={`mono overflow-auto whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-muted ${className}`}>
      {fmtJson(value)}
    </pre>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-4 py-10 text-center text-[12px] text-faint">{children}</div>;
}

type ButtonTone = "default" | "accent" | "ok" | "fail";

const BUTTON_TONES: Record<ButtonTone, string> = {
  default: "border-line-strong text-text hover:border-muted",
  accent: "border-accent/60 text-accent hover:bg-accent/10",
  ok: "border-ok/60 text-ok hover:bg-ok/10",
  fail: "border-fail/60 text-fail hover:bg-fail/10",
};

export function Button({
  tone = "default",
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: ButtonTone }) {
  return (
    <button
      type={type}
      {...props}
      className={`h-8 border px-3 text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${BUTTON_TONES[tone]} ${className}`}
    />
  );
}

export function Tabs<T extends string>({ value, options, onChange }: { value: T; options: readonly T[]; onChange: (value: T) => void }) {
  return (
    <div className="flex border border-line">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          className={`h-7 px-3 text-[11.5px] ${option === value ? "bg-raised text-text" : "text-faint hover:text-muted"}`}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

export function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="label">{label}</span>
      <span className="mono text-[12px] text-muted">{children}</span>
    </span>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="text-[12px] text-fail">{error instanceof Error ? error.message : String(error)}</div>;
}
```

`ui/src/components/Shell.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet } from "react-router";
import { api } from "../api";
import { useEventStream } from "../hooks/useEventStream";

const NAV = [
  { to: "/", label: "Overview", end: true, badge: null },
  { to: "/workflows", label: "Workflows", end: false, badge: null },
  { to: "/approvals", label: "Approvals", end: false, badge: "pending_approvals" },
  { to: "/tasks", label: "Tasks", end: false, badge: "open_tasks" },
] as const;

export function Shell() {
  const live = useEventStream();
  const info = useQuery({ queryKey: ["info"], queryFn: api.info, staleTime: Infinity });
  const metrics = useQuery({ queryKey: ["metrics", "24h"], queryFn: () => api.metrics("24h") });
  return (
    <div className="flex h-full">
      <aside className="flex w-52 shrink-0 flex-col border-r border-line">
        <div className="flex h-14 items-center gap-2.5 border-b border-line px-4">
          <span className="h-2 w-2 rounded-full bg-accent" />
          <span className="text-[12px] font-semibold tracking-[0.2em]">CEREBELLUM</span>
        </div>
        <nav className="flex flex-col py-2">
          {NAV.map((item) => {
            const count = item.badge && metrics.data ? metrics.data[item.badge] : 0;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `flex h-8 items-center justify-between border-l-2 px-4 text-[12.5px] ${
                    isActive ? "border-accent bg-raised text-text" : "border-transparent text-muted hover:text-text"
                  }`
                }
              >
                <span>{item.label}</span>
                {count > 0 && <span className="mono text-[11px] text-wait">{count}</span>}
              </NavLink>
            );
          })}
        </nav>
        <div className="mt-auto space-y-1.5 border-t border-line px-4 py-3 text-[11px]">
          <div className="flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-accent" : "bg-faint"}`} />
            <span className="text-muted">{live ? "live" : "reconnecting…"}</span>
          </div>
          {info.data && <div className={info.data.mock ? "text-wait" : "text-muted"}>{info.data.mode}</div>}
          {info.data && <div className="mono text-faint">v{info.data.version}</div>}
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-auto">
        {info.data?.mock && (
          <div className="border-b border-line px-6 py-1.5 text-[11.5px] text-wait">
            Mock AI — {info.data.mode}. AI steps return the outputs from each step's mock rules; set ANTHROPIC_API_KEY to use Claude.
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
```

`ui/src/components/RunsTable.tsx`:

```tsx
import { Link, useNavigate } from "react-router";
import { fmtAge, fmtCost, fmtDuration } from "../lib/format";
import type { Run } from "../types";
import { Empty, StatusChip } from "./ui";

const HEADINGS = ["Run", "Workflow", "Status", "Duration", "Cost", "Age", "AI"];

export function RunsTable({ runs, now, loading }: { runs: Run[]; now: number; loading?: boolean }) {
  const navigate = useNavigate();
  if (loading) return <Empty>loading…</Empty>;
  if (!runs.length) {
    return (
      <Empty>
        no runs yet — start one from Workflows or run <span className="mono">cerebellum demo</span>
      </Empty>
    );
  }
  return (
    <table className="w-full text-left text-[12.5px]">
      <thead>
        <tr className="border-b border-line">
          {HEADINGS.map((heading) => (
            <th key={heading} className="label h-8 px-3 font-medium">
              {heading}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr
            key={run.run_id}
            onClick={() => navigate(`/runs/${run.run_id}`)}
            className="h-9 cursor-pointer border-b border-line last:border-b-0 hover:bg-raised"
          >
            <td className="px-3">
              <Link to={`/runs/${run.run_id}`} className="mono text-accent hover:underline" onClick={(e) => e.stopPropagation()}>
                {run.run_id}
              </Link>
            </td>
            <td className="px-3">{run.workflow_name}</td>
            <td className="px-3">
              <StatusChip status={run.status} />
              {run.stale && <span className="ml-2 text-[11px] text-fail">stale</span>}
            </td>
            <td className="mono px-3 text-muted">{fmtDuration(run.duration_s ?? now - run.created_at)}</td>
            <td className="mono px-3 text-muted">{fmtCost(run.cost_usd)}</td>
            <td className="px-3 text-faint">{fmtAge(run.created_at, now)}</td>
            <td className="px-3 text-faint">{run.mock ? "mock" : "claude"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

`ui/src/components/RunGraph.tsx`:

```tsx
import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react";
import { type CSSProperties, useMemo } from "react";
import { type Graph, layoutGraph, NODE_H, NODE_W } from "../lib/layout";
import { stepLook, toneColor } from "../lib/status";
import type { Step } from "../types";

type StepNodeData = { label: string; type: string; status: string; attempts: number; fallback: boolean; selected: boolean };
type StepNodeType = Node<StepNodeData, "step">;

const HANDLE: CSSProperties = {
  width: 6,
  height: 6,
  minWidth: 0,
  minHeight: 0,
  border: 0,
  background: "var(--color-line-strong)",
};

function StepNode({ data }: NodeProps<StepNodeType>) {
  const look = stepLook(data.status);
  const dormant = data.fallback && data.status === "pending";
  const busy = data.status === "running" || data.status === "retrying";
  const meta = [data.type, data.fallback ? "fallback" : null, data.attempts > 1 ? `${data.attempts} attempts` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <div
      className={`flex items-center gap-2.5 border bg-panel px-3 ${data.selected ? "border-accent" : "border-line-strong"} ${
        dormant ? "border-dashed opacity-60" : ""
      }`}
      style={{ width: NODE_W, height: NODE_H }}
    >
      <Handle id="in" type="target" position={Position.Top} style={HANDLE} isConnectable={false} />
      {data.fallback && <Handle id="rescue" type="target" position={Position.Left} style={HANDLE} isConnectable={false} />}
      <span className={`mono text-[14px] ${busy ? "animate-pulse" : ""}`} style={{ color: toneColor(look.tone) }}>
        {look.glyph}
      </span>
      <div className="min-w-0 flex-1">
        <div className="mono truncate text-[12px] text-text">{data.label}</div>
        <div className="truncate text-[10px] uppercase tracking-wider text-faint">{meta}</div>
      </div>
      <Handle id="out" type="source" position={Position.Bottom} style={HANDLE} isConnectable={false} />
      {!data.fallback && <Handle id="fallback" type="source" position={Position.Right} style={HANDLE} isConnectable={false} />}
    </div>
  );
}

const NODE_TYPES = { step: StepNode };

export function RunGraph({
  graph,
  steps,
  selected,
  onSelect,
}: {
  graph: Graph;
  steps?: Record<string, Step>;
  selected?: string | null;
  onSelect?: (stepId: string) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const laid = layoutGraph(graph);
    const nodes: StepNodeType[] = laid.nodes.map((node) => ({
      id: node.id,
      type: "step",
      position: { x: node.x, y: node.y },
      draggable: false,
      connectable: false,
      data: {
        label: node.id,
        type: node.type,
        status: steps?.[node.id]?.status ?? "pending",
        attempts: steps?.[node.id]?.attempts ?? 0,
        fallback: node.fallback,
        selected: node.id === selected,
      },
    }));
    const edges: Edge[] = laid.edges.map((edge) => {
      const target = steps?.[edge.target]?.status;
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: "smoothstep",
        sourceHandle: edge.fallback ? "fallback" : "out",
        targetHandle: edge.fallback ? "rescue" : "in",
        animated: target === "running" || target === "retrying",
        style: edge.fallback
          ? { stroke: "var(--color-fallback)", strokeDasharray: "4 4" }
          : { stroke: "var(--color-line-strong)" },
      };
    });
    return { nodes, edges };
  }, [graph, steps, selected]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      colorMode="dark"
      fitView
      fitViewOptions={{ padding: 0.25 }}
      minZoom={0.3}
      maxZoom={1.4}
      nodesDraggable={false}
      nodesConnectable={false}
      zoomOnScroll={false}
      preventScrolling={false}
      onNodeClick={(_, node) => onSelect?.(node.id)}
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
```

`ui/src/components/Waterfall.tsx`:

```tsx
import { fmtDuration } from "../lib/format";
import { SPAN_TONE, toneColor } from "../lib/status";
import { layoutWaterfall, type Span } from "../lib/waterfall";
import { Empty, Panel } from "./ui";

export function Waterfall({
  spans,
  now,
  selected,
  onSelect,
}: {
  spans: Span[];
  now: number;
  selected?: string | null;
  onSelect?: (stepId: string) => void;
}) {
  const rows = layoutWaterfall(spans, now);
  const total = rows.length ? Math.max(...rows.map((r) => r.span.end ?? now)) - Math.min(...rows.map((r) => r.span.start)) : 0;
  return (
    <Panel title="Trace" actions={<span className="mono text-[11px] text-faint">{rows.length} spans · {fmtDuration(total)}</span>}>
      {rows.length === 0 ? (
        <Empty>no spans recorded</Empty>
      ) : (
        <div className="py-1">
          {rows.map((row) => {
            const stepId = row.span.step_id;
            const active = row.depth === 0 && stepId === selected;
            return (
              <button
                key={row.span.span_id}
                type="button"
                onClick={() => stepId && onSelect?.(stepId)}
                className={`grid h-6 w-full grid-cols-[240px_minmax(0,1fr)_64px] items-center gap-3 px-3 text-left hover:bg-raised ${
                  active ? "bg-raised" : ""
                }`}
              >
                <span className={`mono truncate text-[11.5px] ${row.depth ? "pl-4 text-muted" : "text-text"}`}>
                  {row.depth ? "└ " : ""}
                  {row.span.label}
                </span>
                <span className="relative h-2">
                  <span
                    className="absolute top-0 h-full"
                    style={{
                      left: `${row.offset}%`,
                      width: `${row.width}%`,
                      background: toneColor(SPAN_TONE[row.span.status] ?? "accent"),
                      opacity: row.depth ? 0.55 : 0.9,
                    }}
                  />
                </span>
                <span className="mono text-right text-[11px] text-faint">{fmtDuration(row.duration)}</span>
              </button>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
```

`ui/src/components/StepInspector.tsx`:

```tsx
import type { ReactNode } from "react";
import { fmtCost, fmtDuration, fmtJson, prettyJson } from "../lib/format";
import type { GraphFallback, GraphStep } from "../lib/layout";
import type { RunEvent, Step } from "../types";
import { Empty, JsonBlock, Label, StatusChip } from "./ui";

type Data = Record<string, unknown>;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="px-4 py-3">
      <Label>{title}</Label>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="mono mt-1 text-[12px] text-muted">{value}</div>
    </div>
  );
}

function Block({ label, text, fail = false }: { label: string; text: string; fail?: boolean }) {
  return (
    <div className="mt-2">
      <div className="text-[10px] uppercase tracking-wider text-faint">{label}</div>
      <pre
        className={`mono mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words border border-line bg-bg p-2 text-[11.5px] leading-relaxed ${
          fail ? "text-fail" : "text-muted"
        }`}
      >
        {text}
      </pre>
    </div>
  );
}

function CallView({ event }: { event: RunEvent }) {
  const d: Data = event.data;
  const llm = event.type === "llm.call";
  const usage = (d.usage ?? {}) as Data;
  const title = llm
    ? `LLM · ${String(d.model ?? "")}${d.mock ? " (mock)" : ""}${d.repair ? ` · repair ${String(d.repair)}` : ""}`
    : d.method
      ? `HTTP · ${String(d.method)} ${String(d.path ?? "")} → ${String(d.status ?? "error")}`
      : `SQL · ${String(d.operation ?? "query")}`;
  return (
    <Section title={title}>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-faint">
        <span className={d.ok === false ? "text-fail" : "text-ok"}>{d.ok === false ? "failed" : "ok"}</span>
        {d.duration_ms != null && <span className="mono">{fmtDuration(Number(d.duration_ms) / 1000)}</span>}
        {llm && usage.input_tokens != null && (
          <span className="mono">
            {String(usage.input_tokens)} in · {String(usage.output_tokens)} out
          </span>
        )}
        {Number(d.cost_usd) > 0 && <span className="mono">{fmtCost(Number(d.cost_usd))}</span>}
        {d.rows != null && <span className="mono">{String(d.rows)} rows</span>}
        {d.rowcount != null && <span className="mono">{String(d.rowcount)} rows changed</span>}
      </div>
      {llm ? (
        <>
          {d.system ? <Block label="system" text={String(d.system)} /> : null}
          <Block label="prompt" text={String(d.prompt ?? "")} />
          {d.response != null && <Block label="response" text={prettyJson(String(d.response))} />}
          {Array.isArray(d.errors) && d.errors.length > 0 && <Block label="schema errors" text={d.errors.join("\n")} fail />}
        </>
      ) : (
        <>
          {d.sql ? <Block label="sql" text={String(d.sql).trim()} /> : null}
          {d.params ? <Block label="params" text={fmtJson(d.params)} /> : null}
          {d.request ? <Block label="request" text={fmtJson(d.request)} /> : null}
          {d.response ? <Block label="response" text={fmtJson(d.response)} /> : null}
        </>
      )}
      {d.error ? <Block label="error" text={String(d.error)} fail /> : null}
    </Section>
  );
}

export function StepInspector({
  step,
  definition,
  events,
}: {
  step?: Step;
  definition?: GraphStep | GraphFallback;
  events: RunEvent[];
}) {
  if (!step) return <Empty>select a step in the graph or the trace</Empty>;
  const calls = events.filter((e) => e.type === "llm.call" || e.type === "connector.call");
  const failures = events.filter((e) => e.type === "step.retrying" || e.type === "step.failed");
  const when = definition && "when" in definition ? definition.when : null;
  return (
    <div className="divide-y divide-line">
      <div className="px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <span className="mono truncate text-[14px] text-text">{step.step_id}</span>
          <StatusChip status={step.status} kind="step" />
        </div>
        {definition?.description && <p className="mt-1 text-[12px] text-muted">{definition.description}</p>}
        <div className="mt-3 grid grid-cols-4 gap-3">
          <Fact label="type" value={definition?.type ?? "—"} />
          <Fact label="attempts" value={step.attempts} />
          <Fact label="duration" value={fmtDuration(step.duration_s) || "—"} />
          <Fact label="cost" value={fmtCost(step.cost_usd) || "—"} />
        </div>
        {when && <div className="mono mt-3 text-[11px] text-wait">when {when}</div>}
      </div>
      {step.error && (
        <Section title="Error">
          <pre className="mono whitespace-pre-wrap break-words text-[12px] text-fail">{step.error}</pre>
        </Section>
      )}
      {failures.length > 0 && (
        <Section title="Failed attempts">
          <ul className="space-y-1.5">
            {failures.map((e) => (
              <li key={e.seq} className="mono text-[11.5px] text-muted">
                <span className="text-faint">#{String(e.data.attempt ?? "?")}</span> {String(e.data.kind ?? "error")} ·{" "}
                {String(e.data.error ?? "")}
                {e.type === "step.retrying" && e.data.delay_s != null && (
                  <span className="text-wait"> · retried after {fmtDuration(Number(e.data.delay_s))}</span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Section title="Output">
        {step.output == null ? <span className="text-faint">—</span> : <JsonBlock value={step.output} className="max-h-64" />}
      </Section>
      {calls.map((call) => (
        <CallView key={call.seq} event={call} />
      ))}
    </div>
  );
}
```

`ui/src/components/ApprovalCard.tsx`:

```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { useApprover } from "../hooks/useApprover";
import { useNow } from "../hooks/useNow";
import { fmtAge, fmtAmount, fmtDuration } from "../lib/format";
import type { Approval } from "../types";
import { Button, ErrorNote, JsonBlock, Label } from "./ui";

export function ApprovalCard({ approval, compact = false }: { approval: Approval; compact?: boolean }) {
  const [approver, setApprover] = useApprover();
  const [comment, setComment] = useState("");
  const now = useNow();
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: (approved: boolean) => api.decide(approval.id, approved, approver.trim(), comment),
    onSuccess: () => {
      for (const key of [["approvals"], ["run", approval.run_id], ["runs"], ["metrics"]]) {
        void client.invalidateQueries({ queryKey: key });
      }
    },
  });
  const input = approval.context.input ?? {};
  const shown = approval.context.steps ?? {};
  const facts = Object.entries(input).filter(([key]) => key !== "amount");
  const pending = approval.status === "pending";
  const overdue = approval.expires_at != null && approval.expires_at < now;
  return (
    <article className="border-b border-line px-4 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[14px] text-text">{approval.title}</div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-faint">
            <Link to={`/runs/${approval.run_id}`} className="mono text-accent hover:underline">
              {approval.run_id}
            </Link>
            {approval.workflow_name && <span>{approval.workflow_name}</span>}
            <span className="mono">{approval.step_id}</span>
            <span>requested {fmtAge(approval.requested_at, now)}</span>
            {pending && approval.expires_at != null && (
              <span className={overdue ? "text-fail" : ""}>
                {overdue ? "overdue" : `expires in ${fmtDuration(approval.expires_at - now)}`} · on timeout {approval.on_timeout}
              </span>
            )}
          </div>
        </div>
        {"amount" in input && (
          <div className="shrink-0 text-right">
            <Label>amount</Label>
            <div className="mono text-[24px] leading-tight text-accent">{fmtAmount(input.amount)}</div>
          </div>
        )}
      </div>
      {!compact && facts.length > 0 && (
        <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-[12px]">
          {facts.map(([key, value]) => (
            <Fragment key={key}>
              <dt className="text-faint">{key}</dt>
              <dd className="mono text-muted">{typeof value === "string" ? value : JSON.stringify(value)}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      {Object.entries(shown).map(([stepId, output]) => (
        <details key={stepId} open={!compact} className="mt-3">
          <summary className="label cursor-pointer select-none">{stepId}</summary>
          <JsonBlock value={output} className="mt-1 max-h-56 border border-line bg-bg p-2" />
        </details>
      ))}
      {pending ? (
        <form className="mt-4 flex flex-wrap items-center gap-2" onSubmit={(e) => e.preventDefault()}>
          <input
            value={approver}
            onChange={(e) => setApprover(e.target.value)}
            placeholder="your name"
            aria-label="your name"
            className="input w-36"
          />
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="comment (optional)"
            aria-label="comment"
            className="input min-w-0 flex-1"
          />
          <Button tone="ok" disabled={!approver.trim() || decide.isPending} onClick={() => decide.mutate(true)}>
            Approve
          </Button>
          <Button tone="fail" disabled={!approver.trim() || decide.isPending} onClick={() => decide.mutate(false)}>
            Reject
          </Button>
        </form>
      ) : (
        <div className={`mt-3 text-[12px] ${approval.status === "approved" ? "text-ok" : "text-fail"}`}>
          {approval.status} by {approval.decided_by}
          {approval.comment ? ` · ${approval.comment}` : ""}
        </div>
      )}
      <div className="mt-2">
        <ErrorNote error={decide.error} />
      </div>
    </article>
  );
}
```

`ui/src/components/NewRunDialog.tsx`:

```tsx
import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api, ApiError } from "../api";
import { fmtJson } from "../lib/format";
import { skeletonInput } from "../lib/runs";
import type { WorkflowDetail } from "../types";
import { Button, ErrorNote, Label } from "./ui";

export function NewRunDialog({ workflow, onClose }: { workflow: WorkflowDetail; onClose: () => void }) {
  const navigate = useNavigate();
  const names = Object.keys(workflow.samples);
  const [sample, setSample] = useState(names[0] ?? "");
  const [input, setInput] = useState(() => fmtJson(names.length ? workflow.samples[names[0]] : skeletonInput(workflow.input)));
  const [params, setParams] = useState(() => fmtJson(workflow.params));
  const [parseError, setParseError] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: (body: { input: unknown; params: unknown }) => api.startRun(workflow.id, body.input, body.params),
    onSuccess: (run) => navigate(`/runs/${run.run_id}`),
  });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = () => {
    try {
      const body = { input: JSON.parse(input), params: params.trim() ? JSON.parse(params) : {} };
      setParseError(null);
      start.mutate(body);
    } catch (error) {
      setParseError(`invalid JSON: ${(error as Error).message}`);
    }
  };
  const issues = start.error instanceof ApiError ? start.error.issues : [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        role="dialog"
        aria-label={`New run of ${workflow.name}`}
        className="w-[560px] max-w-[calc(100vw-32px)] border border-line-strong bg-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex h-10 items-center justify-between border-b border-line px-4">
          <Label>New run · {workflow.name}</Label>
          <button type="button" onClick={onClose} className="text-faint hover:text-text" aria-label="close">
            ✕
          </button>
        </header>
        <div className="space-y-4 p-4">
          {names.length > 0 && (
            <div>
              <Label>Sample input</Label>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {names.map((name) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => {
                      setSample(name);
                      setInput(fmtJson(workflow.samples[name]));
                    }}
                    className={`mono h-7 border px-2.5 text-[11.5px] ${
                      name === sample ? "border-accent text-accent" : "border-line text-muted hover:text-text"
                    }`}
                  >
                    {name}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div>
            <Label>Input · JSON</Label>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              rows={8}
              spellCheck={false}
              aria-label="run input"
              className="input-area mono mt-1.5"
            />
          </div>
          <div>
            <Label>Params · JSON</Label>
            <textarea
              value={params}
              onChange={(e) => setParams(e.target.value)}
              rows={3}
              spellCheck={false}
              aria-label="run params"
              className="input-area mono mt-1.5"
            />
          </div>
          {parseError ? <ErrorNote error={parseError} /> : <ErrorNote error={start.error} />}
          {issues.length > 0 && (
            <ul className="mono space-y-0.5 text-[11.5px] text-fail">
              {issues.map((issue) => (
                <li key={`${issue.path}:${issue.message}`}>
                  {issue.path} — {issue.message}
                </li>
              ))}
            </ul>
          )}
        </div>
        <footer className="flex justify-end gap-2 border-t border-line px-4 py-3">
          <Button onClick={onClose}>Cancel</Button>
          <Button tone="accent" disabled={start.isPending} onClick={submit}>
            {start.isPending ? "Starting…" : "Start run"}
          </Button>
        </footer>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Implement the pages**

`ui/src/pages/Overview.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { RunsTable } from "../components/RunsTable";
import { Label, PageHeader, Panel } from "../components/ui";
import { useNow } from "../hooks/useNow";
import { fmtCost, fmtDuration, fmtPercent } from "../lib/format";
import { type Tone, toneColor } from "../lib/status";

export function Overview() {
  const now = useNow();
  const metrics = useQuery({ queryKey: ["metrics", "24h"], queryFn: () => api.metrics("24h") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.runs() });
  const m = metrics.data;
  const kpis: [string, string, Tone | null][] = [
    ["Runs · 24h", m ? String(m.runs) : "—", null],
    ["Success rate", fmtPercent(m?.success_rate), null],
    ["Avg duration", m?.avg_duration_s == null ? "—" : fmtDuration(m.avg_duration_s), null],
    ["Cost · 24h", m ? fmtCost(m.cost_usd) || "$0" : "—", null],
    ["Pending approvals", m ? String(m.pending_approvals) : "—", m && m.pending_approvals > 0 ? "wait" : null],
    ["Open tasks", m ? String(m.open_tasks) : "—", m && m.open_tasks > 0 ? "fallback" : null],
    ["Retries / fallbacks", m ? `${m.retries} / ${m.fallbacks}` : "—", null],
  ];
  return (
    <div>
      <PageHeader title="Overview" subtitle="last 24 hours · updates live" />
      <div className="grid grid-cols-2 border-b border-line md:grid-cols-4 xl:grid-cols-7">
        {kpis.map(([label, value, tone]) => (
          <div key={label} className="border-r border-b border-line px-5 py-4 xl:border-b-0">
            <Label>{label}</Label>
            <div className="mono mt-2 text-[22px] font-medium" style={tone ? { color: toneColor(tone) } : undefined}>
              {value}
            </div>
          </div>
        ))}
      </div>
      <div className="p-6">
        <Panel title="Recent runs">
          <RunsTable runs={runs.data ?? []} now={now} loading={runs.isLoading} />
        </Panel>
      </div>
    </div>
  );
}
```

`ui/src/pages/RunDetail.tsx`:

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError, api } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { RunGraph } from "../components/RunGraph";
import { StepInspector } from "../components/StepInspector";
import { Button, Empty, ErrorNote, JsonBlock, Label, Meta, PageHeader, Panel, StatusChip } from "../components/ui";
import { Waterfall } from "../components/Waterfall";
import { useNow } from "../hooks/useNow";
import { fmtCost, fmtDuration } from "../lib/format";
import { defaultFocus, isResumable } from "../lib/runs";

export function RunDetail() {
  const { runId = "" } = useParams();
  const now = useNow();
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId) });
  const events = useQuery({ queryKey: ["run", runId, "events"], queryFn: () => api.events(runId) });
  const resume = useMutation({
    mutationFn: () => api.resume(runId),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["run", runId] }),
  });

  if (detail.isLoading) return <Empty>loading…</Empty>;
  if (!detail.data) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div>
        <PageHeader title="Run" />
        <Empty>{missing ? `run ${runId} not found` : <ErrorNote error={detail.error} />}</Empty>
      </div>
    );
  }

  const { run, steps, graph, approvals, spans } = detail.data;
  const stepMap = Object.fromEntries(steps.map((s) => [s.step_id, s]));
  const focus = selected ?? defaultFocus(steps);
  const definitions = graph ? [...graph.steps, ...graph.fallbacks] : [];
  const pending = approvals.filter((a) => a.status === "pending");

  return (
    <div>
      <PageHeader
        title={<span className="mono">{run.run_id}</span>}
        subtitle={
          <Link to="/workflows" className="hover:text-muted">
            {run.workflow_name}
          </Link>
        }
        actions={
          isResumable(run) ? (
            <Button tone="accent" disabled={resume.isPending} onClick={() => resume.mutate()}>
              {resume.isPending ? "Resuming…" : "Resume"}
            </Button>
          ) : undefined
        }
      />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-6 py-2.5">
        <StatusChip status={run.status} />
        {run.stale && <span className="text-[12px] text-fail">stale — the process driving it stopped</span>}
        <Meta label="duration">{fmtDuration(run.duration_s ?? now - run.created_at)}</Meta>
        <Meta label="cost">{fmtCost(run.cost_usd) || "$0"}</Meta>
        <Meta label="ai">{run.mock ? "mock" : "claude"}</Meta>
        <Meta label="started">{new Date(run.created_at * 1000).toLocaleString()}</Meta>
      </div>
      {run.error && <div className="mono border-b border-line px-6 py-2 text-[12px] text-fail">{run.error}</div>}
      {resume.error && (
        <div className="border-b border-line px-6 py-2">
          <ErrorNote error={resume.error} />
        </div>
      )}
      <div className="grid h-[480px] grid-cols-[minmax(0,3fr)_minmax(340px,2fr)] border-b border-line">
        <div className="min-w-0 border-r border-line">
          {graph ? (
            <RunGraph graph={graph} steps={stepMap} selected={focus} onSelect={setSelected} />
          ) : (
            <Empty>workflow graph unavailable for this run</Empty>
          )}
        </div>
        <div className="min-h-0 overflow-auto">
          {pending.map((approval) => (
            <ApprovalCard key={approval.id} approval={approval} compact />
          ))}
          <StepInspector
            step={focus ? stepMap[focus] : undefined}
            definition={definitions.find((d) => d.id === focus)}
            events={(events.data ?? []).filter((e) => e.step_id === focus)}
          />
        </div>
      </div>
      <div className="grid gap-6 p-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Waterfall spans={spans} now={now} selected={focus} onSelect={setSelected} />
        <Panel title="Run output">
          {run.output == null ? (
            <Empty>{run.status === "waiting_approval" ? "waiting for a human decision" : "no output yet"}</Empty>
          ) : (
            <JsonBlock value={run.output} className="p-3" />
          )}
          <div className="border-t border-line p-3">
            <Label>input</Label>
            <JsonBlock value={run.input} className="mt-1" />
          </div>
        </Panel>
      </div>
    </div>
  );
}
```

`ui/src/pages/Approvals.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { Empty, ErrorNote, PageHeader, Panel, Tabs } from "../components/ui";

const FILTERS = ["pending", "all"] as const;

export function Approvals() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("pending");
  const approvals = useQuery({ queryKey: ["approvals", filter], queryFn: () => api.approvals(filter) });
  return (
    <div>
      <PageHeader title="Approvals" subtitle="human-in-the-loop decisions" actions={<Tabs value={filter} options={FILTERS} onChange={setFilter} />} />
      <div className="p-6">
        <Panel>
          {approvals.isLoading ? (
            <Empty>loading…</Empty>
          ) : approvals.error ? (
            <Empty>
              <ErrorNote error={approvals.error} />
            </Empty>
          ) : approvals.data?.length ? (
            approvals.data.map((approval) => <ApprovalCard key={approval.id} approval={approval} />)
          ) : (
            <Empty>{filter === "pending" ? "nothing is waiting for a decision" : "no approvals yet"}</Empty>
          )}
        </Panel>
      </div>
    </div>
  );
}
```

`ui/src/pages/Tasks.tsx`:

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { Button, Empty, ErrorNote, JsonBlock, PageHeader, Panel, Tabs } from "../components/ui";
import { useApprover } from "../hooks/useApprover";
import { useNow } from "../hooks/useNow";
import { fmtAge } from "../lib/format";
import type { Task } from "../types";

const FILTERS = ["open", "all"] as const;

function TaskRow({ task, now }: { task: Task; now: number }) {
  const [name, setName] = useApprover();
  const [note, setNote] = useState("");
  const client = useQueryClient();
  const resolve = useMutation({
    mutationFn: () => api.resolveTask(task.id, name.trim(), note),
    onSuccess: () => {
      for (const key of [["tasks"], ["metrics"], ["run", task.run_id]]) void client.invalidateQueries({ queryKey: key });
    },
  });
  return (
    <article className="border-b border-line px-4 py-4 last:border-b-0">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[14px] text-text">{task.title}</div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-faint">
            <span className="mono">{task.id}</span>
            <Link to={`/runs/${task.run_id}`} className="mono text-accent hover:underline">
              {task.run_id}
            </Link>
            {task.workflow_name && <span>{task.workflow_name}</span>}
            <span>assignee {task.assignee}</span>
            <span>opened {fmtAge(task.created_at, now)}</span>
          </div>
        </div>
        <span className={`text-[12px] ${task.status === "open" ? "text-fallback" : "text-ok"}`}>{task.status}</span>
      </div>
      <JsonBlock value={task.payload} className="mt-3 max-h-48 border border-line bg-bg p-2" />
      {task.status === "open" ? (
        <form className="mt-3 flex flex-wrap items-center gap-2" onSubmit={(e) => e.preventDefault()}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="your name" aria-label="your name" className="input w-36" />
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="what was done" aria-label="resolution note" className="input min-w-0 flex-1" />
          <Button tone="ok" disabled={!name.trim() || resolve.isPending} onClick={() => resolve.mutate()}>
            Mark resolved
          </Button>
        </form>
      ) : (
        <div className="mt-3 text-[12px] text-ok">
          resolved by {task.resolved_by}
          {task.note ? ` · ${task.note}` : ""}
        </div>
      )}
      <div className="mt-2">
        <ErrorNote error={resolve.error} />
      </div>
    </article>
  );
}

export function Tasks() {
  const now = useNow();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("open");
  const tasks = useQuery({ queryKey: ["tasks", filter], queryFn: () => api.tasks(filter) });
  return (
    <div>
      <PageHeader title="Tasks" subtitle="manual work handed over by fallbacks" actions={<Tabs value={filter} options={FILTERS} onChange={setFilter} />} />
      <div className="p-6">
        <Panel>
          {tasks.isLoading ? (
            <Empty>loading…</Empty>
          ) : tasks.data?.length ? (
            tasks.data.map((task) => <TaskRow key={task.id} task={task} now={now} />)
          ) : (
            <Empty>{filter === "open" ? "no open tasks" : "no tasks yet"}</Empty>
          )}
        </Panel>
      </div>
    </div>
  );
}
```

`ui/src/pages/Workflows.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api";
import { NewRunDialog } from "../components/NewRunDialog";
import { RunGraph } from "../components/RunGraph";
import { Button, Empty, PageHeader, Tabs } from "../components/ui";

const VIEWS = ["graph", "yaml"] as const;

export function Workflows() {
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<(typeof VIEWS)[number]>("graph");
  const [dialog, setDialog] = useState(false);
  const list = useQuery({ queryKey: ["workflows"], queryFn: api.workflows });
  const selectedId = params.get("id") ?? list.data?.[0]?.id ?? null;
  const detail = useQuery({
    queryKey: ["workflows", selectedId],
    queryFn: () => api.workflow(selectedId!),
    enabled: selectedId != null,
  });
  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Workflows" subtitle="definitions on disk and from run history" />
      <div className="grid min-h-0 flex-1 grid-cols-[300px_minmax(0,1fr)]">
        <div className="overflow-auto border-r border-line">
          {list.data?.map((workflow) => (
            <button
              key={workflow.id}
              type="button"
              onClick={() => setParams({ id: workflow.id })}
              className={`block w-full border-b border-line px-4 py-3 text-left ${workflow.id === selectedId ? "bg-raised" : "hover:bg-raised/60"}`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="mono truncate text-[12.5px] text-text">{workflow.name}</span>
                <span className="mono text-[11px] text-faint">v{workflow.version}</span>
              </div>
              <div className="mt-1 truncate text-[11px] text-faint">{workflow.source === "file" ? workflow.id : `run history · ${workflow.digest}`}</div>
              <div className="mt-1 text-[11px] text-muted">
                {workflow.steps} steps · {workflow.runs} runs
              </div>
            </button>
          ))}
          {list.data && !list.data.length && (
            <Empty>
              no workflows found — run <span className="mono">cerebellum init</span> here or start a run
            </Empty>
          )}
        </div>
        <div className="flex min-h-[520px] min-w-0 flex-col">
          {detail.data ? (
            <>
              <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-3">
                <div className="min-w-0">
                  <div className="text-[15px] text-text">
                    {detail.data.name} <span className="mono text-[12px] text-faint">v{detail.data.version}</span>
                  </div>
                  {detail.data.description && <p className="mt-1 max-w-3xl text-[12px] text-muted">{detail.data.description}</p>}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Tabs value={view} options={VIEWS} onChange={setView} />
                  <Button tone="accent" onClick={() => setDialog(true)}>
                    New run
                  </Button>
                </div>
              </div>
              <div className="min-h-0 flex-1">
                {view === "graph" ? (
                  <RunGraph key={detail.data.id} graph={detail.data.graph} />
                ) : (
                  <pre className="mono h-full overflow-auto p-6 text-[12px] leading-relaxed text-muted">{detail.data.yaml}</pre>
                )}
              </div>
              {dialog && <NewRunDialog workflow={detail.data} onClose={() => setDialog(false)} />}
            </>
          ) : (
            <Empty>{list.isLoading || detail.isLoading ? "loading…" : "select a workflow"}</Empty>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Type check, unit tests and build**

Run: `cd ui && npx tsc --noEmit && npx vitest run && npm run build`
Expected: no type errors; vitest PASS; Vite writes `../src/cerebellum/server/static/index.html` and `assets/*.js|css`.

- [ ] **Step 7: Run the packaged-UI test and the whole suite**

Run: `.venv/bin/pytest tests -q && make fmt && make lint && git status --short`
Expected: all PASS (including `test_the_dashboard_ui_is_built_into_the_package`).

---

### Task 8: Browser acceptance (spec §11, phase 2)

**Files:**
- Create: `.claude/launch.json` (preview configuration for the in-app browser)
- Modify: only files a defect found here points to (each change gets a test first and a ledger ruling)

**Interfaces:**
- Consumes: everything above.
- Produces: verified behaviour in a real browser.

- [ ] **Step 1: Seed data and start the dashboard**

Run the demo into the default home (`./.cerebellum`, git-ignored): `PYTHONPATH=src .venv/bin/cerebellum demo`.

`.claude/launch.json` (uses `src` on `sys.path` because this checkout's editable-install `.pth` is hidden by iCloud sync):

```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "cerebellum-ui",
      "runtimeExecutable": ".venv/bin/python",
      "runtimeArgs": [
        "-c",
        "import sys; sys.path.insert(0, 'src'); from cerebellum.cli.app import main; sys.argv = ['cerebellum', 'ui', '--port', '7400']; main()"
      ],
      "port": 7400
    }
  ]
}
```

Start it with the browser preview tool (`preview_start` name `cerebellum-ui`).

- [ ] **Step 2: Walk the acceptance path and record evidence**

1. Overview: KPI strip and the five demo runs are listed; the mock-AI banner is visible.
2. Open the waiting "large" run: DAG shows `manager_approval` awaiting approval; the trace waterfall renders.
3. Approve it in place (name + Approve): the run turns `succeeded` without a reload; `issue_refund` and `mark_refunded` succeed.
4. Open the flaky run: the waterfall shows `issue_refund #1..#3` with `POST /refunds → 503` children before `→ 201`; clicking `issue_refund` shows the HTTP calls in the inspector.
5. Tasks: the outage run's manual task is listed; resolve it; it leaves the open list.
6. Workflows: select `refund_request`, preview graph and YAML, New run with sample `small`; the app navigates to the new run, which ends `succeeded`.
7. Read the browser console: no errors.

Expected: every item holds. Any defect: reproduce with a failing test (pytest or vitest), fix, rebuild with `npm run build`, re-check in the browser, and ledger it.

- [ ] **Step 3: Final verification**

Run: `make test && git status --short`
Expected: pytest, lint and vitest all pass.

---

## Self-Review Notes

- Spec coverage (phase 2): §9.1 pages 1–4 and 6 → Tasks 6–7 (Evals page is phase 3); §9.2 visual tokens → Task 7 `index.css` + components; §9.3 API + SSE + worker → Tasks 1–4; §9.4 localhost default, warning, prebuilt static → Tasks 4, 5, 7; §4.5 approval timeout sweep → Task 3; §7 `ui` command and demo dashboard link → Task 5; §10 server tests (`TestClient`, SSE) and vitest data transforms → Tasks 2–4, 6; §11 browser acceptance → Task 8.
- Type consistency: `Worker.start_run/resume/decide/sweep` (Task 3) match the routes (Task 4); JSON shapes from `serialize` (Task 2) match `ui/src/types.ts` (Task 6): run fields + `stale`/`duration_s`, step fields + `duration_s`, approvals/tasks + `workflow_name`/`run_status`, graph `steps[].fallback` / `fallbacks[].fallback_for`, spans from `build_spans`; query keys used by pages (`["runs"]`, `["run", id]`, `["run", id, "events"]`, `["approvals", filter]`, `["tasks", filter]`, `["metrics", "24h"]`, `["workflows"]`, `["workflows", id]`) are prefix-matched by `keysFor`.
- Spans are computed on the server by the Phase 1 `build_spans` (one implementation, tested in Python); the UI's tested transform is the waterfall geometry (`layoutWaterfall`) and the DAG layout (`layoutGraph`).
