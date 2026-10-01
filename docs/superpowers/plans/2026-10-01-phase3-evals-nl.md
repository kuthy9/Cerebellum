# Phase 3 — Evals + Natural-Language Drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eval suites that pin a workflow's behaviour case by case (`cerebellum eval`, stored results, comparison with the previous run, an Evals dashboard page) and `cerebellum new`, which drafts a validated workflow YAML from a plain-language description with Claude.

**Architecture:** `cerebellum.evals` loads a suite (`suite.py`), runs one real, `eval_run_id`-tagged run per case through the Phase 1 `Engine` against fresh per-eval sandbox databases, decides approvals as `eval`, checks `expect` paths and `assert` expressions against a view of the finished run (`checks.py`), and records results plus the baseline comparison in two new store tables (`runner.py`). The dashboard reads them through `GET /api/evals` and `GET /api/evals/{id}`. `cerebellum.authoring` prompts Claude with the workflow JSON Schema, a step guide and the refund example, validates the reply with the normal loader and feeds the issues back (≤ 3 attempts).

**Tech Stack:** Python 3.11+, pydantic 2, Typer + Rich, FastAPI, SQLite; React 19 + TanStack Query 5 + Tailwind 4 (inline SVG sparklines, no chart library), Vitest 3.

**Spec:** `docs/superpowers/specs/2026-10-01-workflow-runtime-design.md` (§8 Evals and `cerebellum new`, §9.1 #5 Evals page, §9.3 API, §11 phase 3 acceptance). Builds on `docs/superpowers/plans/2026-10-01-phase1-runtime-cli.md` and `docs/superpowers/plans/2026-10-01-phase2-dashboard.md`.

## Global Constraints

- Do not commit (user rule). Every "Checkpoint" step runs `git status --short` only.
- No new Python or UI dependencies.
- Suite format (spec §8.1): `suite`, `workflow`, `defaults: {approval, sandbox, mock}`, `cases: [{id, input, approval?, sandbox?, expect: {dotted.path: value}, assert: [expr]}]`. Assertable paths: `status`, `output.*`, `steps.<id>.{status,output.*}`, `tasks.count`, `run.cost_usd`, `run.duration_s` — this plan also exposes `error`, `steps.<id>.{attempts,error}`, `tasks.open`, `approvals.count`, `run.id`.
- Each case is one real run tagged with `eval_run_id`; approvals are decided per case (`approved|rejected`) by `eval`; cases run sequentially; the sandbox fail mode is switched per case.
- Results: pass rate; per case expected/actual/failed assertions, cost and duration; AI schema first-try pass rate and repair count; compared with the previous eval run of the same suite, regressions flagged.
- `cerebellum eval <suite> [--mock] [--min-pass 0.9]`: below `--min-pass` exit 1; invalid suite exit 2. The packaged suite has ~15 cases and passes completely in mock mode; a broken approval threshold produces regression flags.
- `cerebellum new "<description>" -o <file>`: context = the workflow JSON Schema exported from the pydantic models, a step-type guide, connector types and the refund example; reply → loader validation → retry with the issues (≤ 3 attempts) → write the file, first line a comment saying it is an AI draft to review. No credentials → error exit, no template fallback.
- Evals page `/evals` (spec §9.1 #5): pass-rate trend, cost/latency trend, case details with regression marks. API `GET /api/evals`, `GET /api/evals/{id}`.
- `cerebellum init` also copies `evals.yaml` (spec §7).
- All new defaults live in `config.py` as named constants.
- Visual rules from Phase 2 apply (dark tokens, hairlines, Inter + JetBrains Mono, one cyan accent, desaturated status colours, every colour a CSS token).
- The user's pre-tool hook blocks shell commands that name some files (e.g. `store.py`, `config.py`, the states/trace test files) or contain `os.environ`/`unlink`/heredocs: run tests by directory or by a test file whose name avoids those words, and edit with the editor tools.

## Review Focus

1. **A suite with a typo in a path or step id, an unknown fail mode, or an input the workflow rejects** → `cerebellum eval` exits 2 listing every issue with its YAML path, before any run starts. Tests: Task 2 `test_suite_reports_every_issue_with_its_path`, Task 4 `test_eval_rejects_an_invalid_suite_with_exit_2`.
2. **The same suite runs twice, or after the demo and manual runs changed the sandbox orders** → identical results, because every eval run seeds its own sandbox databases under `CEREBELLUM_HOME/evals/<id>/`. Test: Task 3 `test_suite_is_repeatable_and_isolated`.
3. **Eval traffic reaches the human surfaces** → manual tasks opened by eval cases are closed by `eval`; Overview metrics, nav badges and the recent-runs list leave eval runs out. Tests: Task 1 `test_metrics_and_run_lists_leave_eval_runs_out`, Task 3 `test_eval_tasks_are_closed`, Task 5 `test_runs_endpoint_leaves_eval_runs_out`.
4. **Comparison edge cases** → the first run of a suite has no baseline and no regressions; an interrupted (errored) run is never a baseline; a case failing in both runs is not a regression. Tests: Task 1 `test_latest_completed_eval_run_is_the_baseline`, Task 3 `test_breaking_the_threshold_is_flagged_as_regression`, Task 3 `test_interrupted_eval_is_marked_errored`.
5. **`cerebellum new` without credentials, over an existing file, or with a model that keeps producing invalid YAML** → exit 1 / exit 2 with a clear message; no file is written or overwritten. Tests: Task 6 `test_new_without_credentials_writes_nothing`, `test_new_refuses_to_overwrite_without_force`, `test_new_reports_issues_when_the_draft_stays_invalid`.

## File Structure

```
src/cerebellum/
  config.py                       # + DEFAULT_EVAL_MIN_PASS, DEFAULT_NEW_ATTEMPTS
  spec/loader.py                  # _loc → issue_path (public; the suite loader reuses it)
  runtime/store.py                # + eval_runs / eval_results tables, EvalRunRecord, EvalResultRecord,
                                  #   create/record/finish/get/list/latest eval methods;
                                  #   metrics and list_runs(include_evals=False) leave eval runs out
  evals/
    __init__.py                   # public exports
    suite.py                      # suite models, load_suite, path/fail-mode/input checks
    checks.py                     # run_view, resolve, same, check_case
    runner.py                     # EvalRunner: runs cases, decides approvals, closes tasks, records
  authoring.py                    # cerebellum new: prompt, draft_workflow, DraftError
  templates/refund/evals.yaml     # the packaged 15-case suite
  cli/app.py                      # + eval, new; init copies evals.yaml; _sandbox yields its handle
  cli/render.py                   # + eval_case_line, describe_check, eval_summary
  server/serialize.py             # + eval_run_json, eval_result_json
  server/app.py                   # + /api/evals, /api/evals/{id}; /api/runs leaves eval runs out
ui/src/
  types.ts, api.ts                # + EvalRun, EvalResult, EvalCheck, EvalDetail; Run.eval_run_id
  lib/evals.ts (+ evals.test.ts)  # groupBySuite, sparkPoints, describeCheck, caseChange
  lib/invalidation.ts (+ test)    # run.* events refresh ["evals"]
  components/Sparkline.tsx
  pages/Evals.tsx, pages/EvalDetail.tsx
  App.tsx, components/Shell.tsx, pages/RunDetail.tsx
tests/
  evals/test_persistence.py, test_suite.py, test_checks.py, test_runner.py
  cli/test_eval_cli.py, cli/test_new_cli.py, cli/test_cli.py (init)
  server/test_api_evals.py
  test_authoring.py
Makefile, README.md
```

---

### Task 1: Eval persistence, eval-free metrics, defaults

**Files:**
- Modify: `src/cerebellum/config.py`, `src/cerebellum/runtime/store.py`
- Test: `tests/evals/test_persistence.py` (new)

**Interfaces:**
- Consumes: Phase 1/2 `Store`, `_tx`, `_rows`, `to_json`, `NotFound`, `Engine.start(..., eval_run_id=)`.
- Produces: `config.DEFAULT_EVAL_MIN_PASS = 0.9`, `config.DEFAULT_NEW_ATTEMPTS = 3`; `EvalRunRecord(id, suite, suite_path, workflow_name, workflow_digest, status, mock, total, passed, failed, regressions, cost_usd, ai_first_try, ai_first_ok, ai_repairs, baseline_id, error, created_at, ended_at)` with `.pass_rate -> float | None`; `EvalResultRecord(eval_run_id, case_id, position, run_id, passed, baseline_passed, checks, error, cost_usd, duration_s)` with `.regression -> bool`; `Store.create_eval_run(eval_run_id, *, suite, suite_path, workflow_name, workflow_digest, mock, total, baseline_id) -> EvalRunRecord`; `Store.record_eval_result(eval_run_id, *, case_id, position, run_id, passed, baseline_passed, checks, error, cost_usd, duration_s) -> EvalResultRecord`; `Store.finish_eval_run(eval_run_id, *, status, error=None, ai_first_try=0, ai_first_ok=0, ai_repairs=0) -> EvalRunRecord`; `Store.get_eval_run(id) -> EvalRunRecord` (raises `NotFound`); `Store.list_eval_runs(*, suite=None, limit=50) -> list[EvalRunRecord]` (newest first); `Store.latest_eval_run(suite) -> EvalRunRecord | None` (most recent *completed*); `Store.get_eval_results(id) -> list[EvalResultRecord]` (by position); `Store.list_runs(..., include_evals: bool = True)`; `Store.metrics` counts no eval runs.

- [ ] **Step 1: Write the failing tests**

`tests/evals/test_persistence.py`:

```python
import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.errors import NotFound
from cerebellum.runtime.engine import Engine

CHECK = {
    "kind": "expect",
    "target": "status",
    "passed": True,
    "expected": "succeeded",
    "actual": "succeeded",
    "missing": False,
}


def make_eval(store, eval_run_id="ev_00000001", *, suite="s", baseline_id=None, total=2):
    return store.create_eval_run(
        eval_run_id,
        suite=suite,
        suite_path="/x/evals.yaml",
        workflow_name="wf",
        workflow_digest="d1",
        mock=True,
        total=total,
        baseline_id=baseline_id,
    )


def test_eval_run_lifecycle(store):
    record = make_eval(store)
    assert record.status == "running" and record.passed == 0 and record.pass_rate is None
    store.record_eval_result(
        "ev_00000001",
        case_id="a",
        position=0,
        run_id="r_00000001",
        passed=True,
        baseline_passed=None,
        checks=[CHECK],
        error=None,
        cost_usd=0.01,
        duration_s=0.5,
    )
    result = store.record_eval_result(
        "ev_00000001",
        case_id="b",
        position=1,
        run_id=None,
        passed=False,
        baseline_passed=True,
        checks=[],
        error="boom",
        cost_usd=0.0,
        duration_s=None,
    )
    assert result.regression is True and result.error == "boom" and result.baseline_passed is True
    done = store.finish_eval_run(
        "ev_00000001", status="completed", ai_first_try=2, ai_first_ok=1, ai_repairs=1
    )
    assert (done.status, done.passed, done.failed, done.regressions) == ("completed", 1, 1, 1)
    assert done.cost_usd == pytest.approx(0.01) and done.pass_rate == 0.5
    assert done.ended_at is not None
    assert (done.ai_first_try, done.ai_first_ok, done.ai_repairs) == (2, 1, 1)
    first, second = store.get_eval_results("ev_00000001")
    assert (first.case_id, second.case_id) == ("a", "b")
    assert first.checks == [CHECK] and first.regression is False and first.passed is True


def test_latest_completed_eval_run_is_the_baseline(store, clock):
    assert store.latest_eval_run("s") is None
    make_eval(store, "ev_00000001")
    store.finish_eval_run("ev_00000001", status="completed")
    clock.advance(1)
    make_eval(store, "ev_00000002")
    store.finish_eval_run("ev_00000002", status="errored", error="interrupted")
    clock.advance(1)
    make_eval(store, "ev_00000003", suite="other")
    assert store.latest_eval_run("s").id == "ev_00000001"
    assert [r.id for r in store.list_eval_runs()] == ["ev_00000003", "ev_00000002", "ev_00000001"]
    assert [r.id for r in store.list_eval_runs(suite="s")] == ["ev_00000002", "ev_00000001"]
    assert store.get_eval_run("ev_00000002").error == "interrupted"


def test_unknown_eval_run_is_not_found(store):
    with pytest.raises(NotFound):
        store.get_eval_run("ev_nope")
    with pytest.raises(NotFound):
        store.finish_eval_run("ev_nope", status="completed")


async def test_metrics_and_run_lists_leave_eval_runs_out(store, settings, simple_workflow):
    engine = Engine(store, settings, MockProvider(latency=(0, 0)))
    make_eval(store)
    await engine.start(simple_workflow, {"order_id": "A1"}, eval_run_id="ev_00000001")
    real = await engine.start(simple_workflow, {"order_id": "A2"})
    metrics = store.metrics(0)
    assert metrics["runs"] == 1 and metrics["open_tasks"] == 1
    assert metrics["by_status"]["succeeded"] == 1
    assert [r.run_id for r in store.list_runs(include_evals=False)] == [real.run_id]
    assert len(store.list_runs()) == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src .venv/bin/pytest tests/evals -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'create_eval_run'` (and `list_runs() got an unexpected keyword argument 'include_evals'`).

- [ ] **Step 3: Add the defaults to `src/cerebellum/config.py`**

Below `DEFAULT_STREAM_POLL_SECONDS = 0.5`:

```python
# `cerebellum eval`: the share of cases that must pass for exit code 0 (spec: --min-pass 0.9).
DEFAULT_EVAL_MIN_PASS = 0.9
# `cerebellum new`: model attempts per draft — the first reply plus repairs from loader issues.
DEFAULT_NEW_ATTEMPTS = 3
```

- [ ] **Step 4: Extend the store schema and records in `src/cerebellum/runtime/store.py`**

Append to `SCHEMA` (inside the string, after the `idx_tasks_status` index):

```sql
CREATE TABLE IF NOT EXISTS eval_runs (
    id              TEXT PRIMARY KEY,
    suite           TEXT NOT NULL,
    suite_path      TEXT NOT NULL,
    workflow_name   TEXT NOT NULL,
    workflow_digest TEXT NOT NULL,
    status          TEXT NOT NULL,
    mock            INTEGER NOT NULL,
    total           INTEGER NOT NULL,
    passed          INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    regressions     INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    ai_first_try    INTEGER NOT NULL DEFAULT 0,
    ai_first_ok     INTEGER NOT NULL DEFAULT 0,
    ai_repairs      INTEGER NOT NULL DEFAULT 0,
    baseline_id     TEXT,
    error           TEXT,
    created_at      REAL NOT NULL,
    ended_at        REAL
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_suite ON eval_runs(suite, created_at);
CREATE TABLE IF NOT EXISTS eval_results (
    eval_run_id     TEXT NOT NULL,
    case_id         TEXT NOT NULL,
    position        INTEGER NOT NULL,
    run_id          TEXT,
    passed          INTEGER NOT NULL,
    baseline_passed INTEGER,
    checks          TEXT NOT NULL,
    error           TEXT,
    cost_usd        REAL NOT NULL DEFAULT 0,
    duration_s      REAL,
    PRIMARY KEY (eval_run_id, case_id)
);
```

After the `WorkflowSnapshot` dataclass:

```python
@dataclass(frozen=True)
class EvalRunRecord:
    id: str
    suite: str
    suite_path: str
    workflow_name: str
    workflow_digest: str
    status: str  # running | completed | errored
    mock: bool
    total: int
    passed: int
    failed: int
    regressions: int
    cost_usd: float
    ai_first_try: int
    ai_first_ok: int
    ai_repairs: int
    baseline_id: str | None
    error: str | None
    created_at: float
    ended_at: float | None

    @property
    def pass_rate(self) -> float | None:
        done = self.passed + self.failed
        return self.passed / done if done else None


@dataclass(frozen=True)
class EvalResultRecord:
    eval_run_id: str
    case_id: str
    position: int
    run_id: str | None
    passed: bool
    baseline_passed: bool | None  # None: the baseline run had no such case (or there is none)
    checks: list[dict[str, Any]]
    error: str | None
    cost_usd: float
    duration_s: float | None

    @property
    def regression(self) -> bool:
        return self.baseline_passed is True and not self.passed
```

After `_task(row)`:

```python
def _eval_run(row: sqlite3.Row) -> EvalRunRecord:
    data = dict(row)
    data["mock"] = bool(data["mock"])
    return EvalRunRecord(**data)


def _eval_result(row: sqlite3.Row) -> EvalResultRecord:
    data = dict(row)
    data["passed"] = bool(data["passed"])
    baseline = data["baseline_passed"]
    data["baseline_passed"] = None if baseline is None else bool(baseline)
    data["checks"] = json.loads(data["checks"])
    return EvalResultRecord(**data)
```

- [ ] **Step 5: Leave eval runs out of `list_runs` (opt-in) and `metrics`**

`list_runs` gains a keyword and a clause:

```python
    def list_runs(
        self,
        *,
        status: RunStatus | None = None,
        limit: int = 50,
        eval_run_id: str | None = None,
        include_evals: bool = True,
    ) -> list[RunRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        if eval_run_id is not None:
            clauses.append("eval_run_id=?")
            params.append(eval_run_id)
        if not include_evals:
            clauses.append("eval_run_id IS NULL")
```

(the rest of the method is unchanged). Replace `metrics` with:

```python
    def metrics(self, since: float) -> dict[str, Any]:
        """Run outcomes since `since`; inbox sizes (pending approvals, open tasks) are current.
        Eval runs are test traffic and are left out."""
        runs = self._rows(
            "SELECT status, cost_usd, created_at, ended_at FROM runs "
            "WHERE created_at >= ? AND eval_run_id IS NULL",
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

        def events(kind: str) -> int:
            return count(
                "SELECT COUNT(*) FROM events e JOIN runs r ON r.run_id = e.run_id "
                "WHERE e.type=? AND e.ts >= ? AND r.eval_run_id IS NULL",
                (kind, since),
            )

        return {
            "since": since,
            "runs": len(runs),
            "by_status": by_status,
            "success_rate": succeeded / len(finished) if finished else None,
            "avg_duration_s": sum(durations) / len(durations) if durations else None,
            "cost_usd": sum(row["cost_usd"] for row in runs),
            "pending_approvals": count(
                "SELECT COUNT(*) FROM approvals a JOIN runs r ON r.run_id = a.run_id "
                "WHERE a.status='pending' AND r.eval_run_id IS NULL"
            ),
            "open_tasks": count(
                "SELECT COUNT(*) FROM tasks t JOIN runs r ON r.run_id = t.run_id "
                "WHERE t.status='open' AND r.eval_run_id IS NULL"
            ),
            "retries": events("step.retrying"),
            "fallbacks": events("step.recovered"),
        }
```

- [ ] **Step 6: Add the eval methods to `Store`**

A new section between `# ── metrics` and `# ── events`:

```python
    # ── evals ─────────────────────────────────────────────────────────────────

    def create_eval_run(
        self,
        eval_run_id: str,
        *,
        suite: str,
        suite_path: str,
        workflow_name: str,
        workflow_digest: str,
        mock: bool,
        total: int,
        baseline_id: str | None,
    ) -> EvalRunRecord:
        with self._tx() as tx:
            tx.execute(
                "INSERT INTO eval_runs(id, suite, suite_path, workflow_name, workflow_digest, "
                "status, mock, total, baseline_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    eval_run_id,
                    suite,
                    suite_path,
                    workflow_name,
                    workflow_digest,
                    "running",
                    int(mock),
                    total,
                    baseline_id,
                    tx.now,
                ),
            )
        return self.get_eval_run(eval_run_id)

    def record_eval_result(
        self,
        eval_run_id: str,
        *,
        case_id: str,
        position: int,
        run_id: str | None,
        passed: bool,
        baseline_passed: bool | None,
        checks: list[dict[str, Any]],
        error: str | None,
        cost_usd: float,
        duration_s: float | None,
    ) -> EvalResultRecord:
        regression = baseline_passed is True and not passed
        with self._tx() as tx:
            tx.execute(
                "INSERT INTO eval_results(eval_run_id, case_id, position, run_id, passed, "
                "baseline_passed, checks, error, cost_usd, duration_s) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    eval_run_id,
                    case_id,
                    position,
                    run_id,
                    int(passed),
                    None if baseline_passed is None else int(baseline_passed),
                    to_json(checks),
                    error,
                    cost_usd,
                    duration_s,
                ),
            )
            tx.execute(
                "UPDATE eval_runs SET passed = passed + ?, failed = failed + ?, "
                "regressions = regressions + ?, cost_usd = cost_usd + ? WHERE id=?",
                (int(passed), int(not passed), int(regression), cost_usd, eval_run_id),
            )
        rows = self._rows(
            "SELECT * FROM eval_results WHERE eval_run_id=? AND case_id=?", (eval_run_id, case_id)
        )
        return _eval_result(rows[0])

    def finish_eval_run(
        self,
        eval_run_id: str,
        *,
        status: str,
        error: str | None = None,
        ai_first_try: int = 0,
        ai_first_ok: int = 0,
        ai_repairs: int = 0,
    ) -> EvalRunRecord:
        with self._tx() as tx:
            cursor = tx.execute(
                "UPDATE eval_runs SET status=?, error=?, ai_first_try=?, ai_first_ok=?, "
                "ai_repairs=?, ended_at=? WHERE id=?",
                (status, error, ai_first_try, ai_first_ok, ai_repairs, tx.now, eval_run_id),
            )
            if cursor.rowcount == 0:
                raise NotFound(f"eval run {eval_run_id!r} not found")
        return self.get_eval_run(eval_run_id)

    def get_eval_run(self, eval_run_id: str) -> EvalRunRecord:
        rows = self._rows("SELECT * FROM eval_runs WHERE id=?", (eval_run_id,))
        if not rows:
            raise NotFound(f"eval run {eval_run_id!r} not found")
        return _eval_run(rows[0])

    def list_eval_runs(self, *, suite: str | None = None, limit: int = 50) -> list[EvalRunRecord]:
        where, params = ("WHERE suite=?", (suite,)) if suite is not None else ("", ())
        rows = self._rows(
            f"SELECT * FROM eval_runs {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (*params, limit),
        )
        return [_eval_run(row) for row in rows]

    def latest_eval_run(self, suite: str) -> EvalRunRecord | None:
        """The most recent completed run of `suite`: the baseline the next run is compared with."""
        rows = self._rows(
            "SELECT * FROM eval_runs WHERE suite=? AND status='completed' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (suite,),
        )
        return _eval_run(rows[0]) if rows else None

    def get_eval_results(self, eval_run_id: str) -> list[EvalResultRecord]:
        rows = self._rows(
            "SELECT * FROM eval_results WHERE eval_run_id=? ORDER BY position", (eval_run_id,)
        )
        return [_eval_result(row) for row in rows]
```

- [ ] **Step 7: Run the new tests, then the whole suite**

Run: `PYTHONPATH=src .venv/bin/pytest tests/evals -q`
Expected: PASS (4 tests).

Run: `PYTHONPATH=src .venv/bin/pytest tests -q`
Expected: all pass (the Phase 2 metrics/API tests start no eval runs, so their numbers do not change).

- [ ] **Step 8: Checkpoint**

Run: `git status --short`
Expected: `tests/evals/` appears as untracked content of `tests/`; no unexpected files.

---

### Task 2: Suite files, checks and the packaged suite

**Files:**
- Create: `src/cerebellum/evals/__init__.py`, `src/cerebellum/evals/suite.py`, `src/cerebellum/evals/checks.py`, `src/cerebellum/templates/refund/evals.yaml`
- Modify: `src/cerebellum/spec/loader.py` (rename `_loc` → `issue_path`)
- Test: `tests/evals/test_suite.py`, `tests/evals/test_checks.py` (new)

**Interfaces:**
- Consumes: `load_workflow(path, *, env)`, `validate_input(workflow, data)`, `check_expression(expr)`, `eval_condition(expr, ctx)`, `FailMode.parse(text)` (raises `ValueError`), `Identifier`, `SpecError/SpecIssue`, `RunRecord/StepRecord/TaskRecord/ApprovalRecord`.
- Produces: `spec.loader.issue_path(loc) -> str`; `evals.suite`: `EvalDefaults(approval="approved", sandbox="never", mock=False)`, `EvalCase(id, description, input, approval, sandbox, expect, asserts)` (YAML key `assert`), `EvalSuite(suite, description, workflow, defaults, cases)`, `LoadedSuite(suite, workflow, path)` with `.decision(case) -> "approved"|"rejected"` and `.fail_mode(case) -> str`, `load_suite(path, *, env=None) -> LoadedSuite` (raises `SpecError`), `path_problem(path, step_ids) -> str | None`; `evals.checks`: `MISSING`, `Check(kind, target, passed, expected=None, actual=None, missing=False)` with `.to_json()`, `run_view(run, steps, tasks, approvals) -> dict`, `resolve(view, path) -> Any | MISSING`, `same(actual, expected) -> bool`, `check_case(expect, asserts, view) -> list[Check]`.

- [ ] **Step 1: Write the failing tests**

`tests/evals/test_suite.py`:

```python
import pytest

from cerebellum.errors import SpecError
from cerebellum.evals import load_suite
from cerebellum.templates import template_path

SUITE = template_path("refund") / "evals.yaml"
WORKFLOW = template_path("refund") / "workflow.yaml"


def write_suite(tmp_path, body):
    path = tmp_path / "evals.yaml"
    path.write_text(body.replace("WORKFLOW", str(WORKFLOW)), encoding="utf-8")
    return path


def issue_map(exc):
    return {issue.path: issue.message for issue in exc.value.issues}


def test_packaged_suite_loads_with_its_workflow():
    loaded = load_suite(SUITE, env={})
    assert loaded.suite.suite == "refund_regression"
    assert loaded.workflow.name == "refund_request"
    assert len(loaded.suite.cases) == 15
    cases = {case.id: case for case in loaded.suite.cases}
    assert loaded.decision(cases["large_refund_rejected_by_a_human"]) == "rejected"
    assert loaded.decision(cases["small_refund_is_automatic"]) == "approved"
    assert loaded.fail_mode(cases["flaky_payments_api_is_retried"]) == "first:2"
    assert loaded.fail_mode(cases["small_refund_is_automatic"]) == "never"
    assert cases["undelivered_order_is_refused"].asserts
    assert loaded.path == SUITE.resolve()


def test_suite_reports_every_issue_with_its_path(tmp_path):
    path = write_suite(
        tmp_path,
        """
suite: broken
workflow: WORKFLOW
defaults: {sandbox: sometimes}
cases:
  - id: one
    input: {order_id: A1001, amount: 10}
    sandbox: "first:x"
    expect:
      steps.nope.status: succeeded
      steps.fetch_order.colour: red
      steps.policy_check.status.code: 1
      tasks.total: 1
      verdict: ok
      status.code: 1
    assert: ["output.decision ==", "status == 'succeeded'"]
  - id: one
    input: {order_id: A1002}
    expect: {status: failed}
  - id: three
    input: {order_id: A1003, amount: 10}
""",
    )
    with pytest.raises(SpecError) as exc:
        load_suite(path, env={})
    issues = issue_map(exc)
    assert set(issues) == {
        "defaults.sandbox",
        "cases[0].sandbox",
        "cases[0].expect.steps.nope.status",
        "cases[0].expect.steps.fetch_order.colour",
        "cases[0].expect.steps.policy_check.status.code",
        "cases[0].expect.tasks.total",
        "cases[0].expect.verdict",
        "cases[0].expect.status.code",
        "cases[0].assert[0]",
        "cases[1].id",
        "cases[1].input.amount",
        "cases[2]",
    }
    assert "unknown step 'nope'" in issues["cases[0].expect.steps.nope.status"]
    assert "duplicate case id" in issues["cases[1].id"]
    assert issues["cases[1].input.amount"] == "is required"


def test_suite_structure_errors_use_yaml_paths(tmp_path):
    path = write_suite(tmp_path, "suite: Bad Name\nworkflow: WORKFLOW\ncases: []\nextra: 1\n")
    with pytest.raises(SpecError) as exc:
        load_suite(path, env={})
    assert {"suite", "cases", "extra"} <= set(issue_map(exc))


def test_suite_reports_workflow_problems(tmp_path):
    path = write_suite(
        tmp_path, "suite: s\nworkflow: missing.yaml\ncases: [{id: a, expect: {status: x}}]\n"
    )
    with pytest.raises(SpecError) as exc:
        load_suite(path, env={})
    [issue] = exc.value.issues
    assert issue.path.startswith("workflow: ") and "cannot read file" in issue.message


def test_unreadable_suite_file(tmp_path):
    with pytest.raises(SpecError) as exc:
        load_suite(tmp_path / "nope.yaml")
    assert "cannot read file" in exc.value.issues[0].message
```

`tests/evals/test_checks.py`:

```python
from cerebellum.ai.mock import MockProvider
from cerebellum.evals.checks import MISSING, check_case, resolve, run_view, same
from cerebellum.runtime.engine import Engine

VIEW = {
    "status": "succeeded",
    "error": None,
    "output": {"decision": "refunded", "items": [{"sku": "x"}], "ok": True, "amount": 89.9},
    "steps": {"issue_refund": {"status": "succeeded", "attempts": 3}},
    "tasks": {"count": 0, "open": 0},
}


def test_resolve_follows_mappings_and_list_indexes():
    assert resolve(VIEW, "output.decision") == "refunded"
    assert resolve(VIEW, "output.items.0.sku") == "x"
    assert resolve(VIEW, "steps.issue_refund.attempts") == 3
    assert resolve(VIEW, "error") is None
    assert resolve(VIEW, "output.items.5") is MISSING
    assert resolve(VIEW, "output.decision.code") is MISSING
    assert resolve(VIEW, "steps.nope.status") is MISSING


def test_same_keeps_booleans_apart_from_numbers():
    assert same(89.9, 89.90) and same(3, 3.0)
    assert not same(True, 1) and not same(0, False)
    assert same(True, True) and same(None, None) and same({"a": [1]}, {"a": [1]})
    assert not same("1", 1)


def test_check_case_reports_expected_and_actual():
    checks = check_case(
        {"output.decision": "manual", "output.ok": True, "output.refund_id": None, "tasks.count": 0},
        ["steps.issue_refund.attempts == 3", "output.amount > 100"],
        VIEW,
    )
    by_target = {check.target: check for check in checks}
    failed = by_target["output.decision"]
    assert not failed.passed and (failed.expected, failed.actual) == ("manual", "refunded")
    assert by_target["output.ok"].passed and by_target["tasks.count"].passed
    missing = by_target["output.refund_id"]
    assert not missing.passed and missing.missing
    assert by_target["steps.issue_refund.attempts == 3"].passed
    assert not by_target["output.amount > 100"].passed
    assert checks[0].to_json()["kind"] == "expect"


def test_assertion_that_cannot_be_evaluated_fails():
    [check] = check_case({}, ["'refunded' in error"], VIEW)  # error is None: `in None` raises
    assert not check.passed and "TypeError" in check.actual


async def test_run_view_exposes_status_steps_tasks_and_timing(store, settings, simple_workflow):
    engine = Engine(store, settings, MockProvider(latency=(0, 0)))
    run = await engine.start(simple_workflow, {"order_id": "A1"})
    view = run_view(
        run,
        store.get_steps(run.run_id),
        store.list_tasks(run_id=run.run_id),
        store.list_approvals(run_id=run.run_id),
    )
    assert view["status"] == "succeeded" and view["error"] is None
    assert view["steps"]["first"]["status"] == "succeeded"
    assert view["steps"]["first"]["attempts"] == 1
    assert view["tasks"] == {"count": 1, "open": 1}
    assert view["approvals"] == {"count": 0}
    assert view["run"]["id"] == run.run_id and view["run"]["duration_s"] == 0.0
    assert view["input"] == {"order_id": "A1"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src .venv/bin/pytest tests/evals -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cerebellum.evals'`.

- [ ] **Step 3: Make the loader's issue-path helper public**

In `src/cerebellum/spec/loader.py` rename `def _loc(loc: tuple[Any, ...]) -> str:` to `def issue_path(loc: tuple[Any, ...]) -> str:` and update its one caller in `parse_workflow`:

```python
        raise SpecError(
            [SpecIssue(issue_path(err["loc"]), err["msg"]) for err in exc.errors()]
        ) from exc
```

- [ ] **Step 4: Write `src/cerebellum/evals/checks.py`**

```python
"""What an eval case can observe about its run, and how its expectations are checked."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from cerebellum.runtime.store import ApprovalRecord, RunRecord, StepRecord, TaskRecord
from cerebellum.spec.expressions import eval_condition

MISSING: Any = object()


@dataclass(frozen=True)
class Check:
    kind: str  # "expect": a dotted path compared with a value; "assert": an expression
    target: str
    passed: bool
    expected: Any = None
    actual: Any = None
    missing: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def run_view(
    run: RunRecord,
    steps: Mapping[str, StepRecord],
    tasks: Sequence[TaskRecord],
    approvals: Sequence[ApprovalRecord],
) -> dict[str, Any]:
    """The values `expect` paths and `assert` expressions can read."""
    duration = None if run.ended_at is None else round(run.ended_at - run.created_at, 6)
    return {
        "status": run.status.value,
        "error": run.error,
        "output": run.output,
        "input": run.input,
        "params": run.params,
        "steps": {
            step_id: {
                "status": record.status.value,
                "output": record.output,
                "attempts": record.attempts,
                "error": record.error,
            }
            for step_id, record in steps.items()
        },
        "tasks": {
            "count": len(tasks),
            "open": sum(1 for task in tasks if task.status == "open"),
        },
        "approvals": {"count": len(approvals)},
        "run": {"id": run.run_id, "cost_usd": run.cost_usd, "duration_s": duration},
    }


def resolve(view: Any, path: str) -> Any:
    """Follow a dotted path through mappings and list indexes; MISSING when it leads nowhere."""
    value = view
    for part in path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return MISSING
    return value


def same(actual: Any, expected: Any) -> bool:
    """Equality for YAML expectations: booleans are not numbers; numbers compare by value."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return isinstance(actual, bool) and isinstance(expected, bool) and actual == expected
    if isinstance(actual, int | float) and isinstance(expected, int | float):
        return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
    return actual == expected


def check_case(
    expect: Mapping[str, Any], asserts: Sequence[str], view: Mapping[str, Any]
) -> list[Check]:
    checks: list[Check] = []
    for path, expected in expect.items():
        actual = resolve(view, path)
        if actual is MISSING:
            checks.append(Check("expect", path, False, expected, None, missing=True))
        else:
            checks.append(Check("expect", path, same(actual, expected), expected, actual))
    for expr in asserts:
        try:
            checks.append(Check("assert", expr, eval_condition(expr, view)))
        except Exception as exc:  # an assertion that cannot be evaluated failed; it must not crash
            checks.append(Check("assert", expr, False, actual=f"{type(exc).__name__}: {exc}"))
    return checks
```

- [ ] **Step 5: Write `src/cerebellum/evals/suite.py`**

```python
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
    root, *rest = path.split(".")
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
```

- [ ] **Step 6: Write `src/cerebellum/evals/__init__.py`**

```python
"""Eval suites: pin a workflow's behaviour case by case and catch regressions."""

from cerebellum.evals.checks import Check, check_case, resolve, run_view
from cerebellum.evals.suite import EvalCase, EvalSuite, LoadedSuite, load_suite

__all__ = [
    "Check",
    "EvalCase",
    "EvalSuite",
    "LoadedSuite",
    "check_case",
    "load_suite",
    "resolve",
    "run_view",
]
```

- [ ] **Step 7: Write the packaged suite `src/cerebellum/templates/refund/evals.yaml`**

```yaml
suite: refund_regression
description: >
  Pins the refund workflow's decisions: automatic refunds, the approval threshold, human
  approvals and rejections, retries, the manual fallback, AI denials and the policy rules.
  Every eval run starts from fresh sandbox data; cases run in order.
workflow: workflow.yaml
defaults:
  approval: approved
  sandbox: never

cases:
  - id: small_refund_is_automatic
    input: {order_id: A1001, amount: 120, reason: The jacket arrived with a torn sleeve.}
    expect:
      status: succeeded
      output.decision: refunded
      steps.manager_approval.status: skipped
      tasks.count: 0
    assert:
      - "output.refund_id"

  - id: large_refund_approved_by_a_human
    input: {order_id: A1002, amount: 899, reason: The laptop screen flickers constantly.}
    approval: approved
    expect:
      status: succeeded
      output.decision: refunded
      steps.manager_approval.status: succeeded
      steps.manager_approval.output.by: eval
      steps.assess_request.output.risk: medium

  - id: large_refund_rejected_by_a_human
    input: {order_id: A1008, amount: 640, reason: Changed my mind about the colour.}
    approval: rejected
    expect:
      status: rejected
      output.decision: rejected
      steps.issue_refund.status: cancelled

  - id: amount_at_threshold_needs_no_approval
    input: {order_id: A1010, amount: 499, reason: Arrived scratched.}
    expect:
      status: succeeded
      steps.manager_approval.status: skipped
      approvals.count: 0

  - id: amount_above_threshold_needs_approval
    input: {order_id: A1011, amount: 501, reason: Arrived scratched.}
    expect:
      status: succeeded
      steps.manager_approval.status: succeeded
      approvals.count: 1

  - id: flaky_payments_api_is_retried
    input: {order_id: A1009, amount: 89.9, reason: Wrong size.}
    sandbox: "first:2"
    expect:
      status: succeeded
      output.decision: refunded
      steps.issue_refund.attempts: 3

  - id: payments_outage_opens_a_manual_case
    input: {order_id: A1005, amount: 75, reason: The package arrived empty.}
    sandbox: always
    expect:
      status: needs_attention
      output.decision: manual
      steps.issue_refund.status: recovered
      steps.open_manual_case.status: succeeded
      tasks.count: 1

  - id: chargeback_threat_is_denied
    input:
      order_id: A1003
      amount: 60
      reason: Tracking says delivered but I never got it. Refund now or I will file a chargeback.
    expect:
      status: succeeded
      output.decision: denied
      steps.assess_request.output.risk: high
      steps.issue_refund.status: skipped

  - id: partial_refund_is_allowed
    input: {order_id: A1004, amount: 20, reason: One of the two items was missing.}
    expect:
      status: succeeded
      output.decision: refunded

  - id: undelivered_order_is_refused
    input: {order_id: A1006, amount: 100, reason: Not here yet.}
    expect:
      status: failed
      steps.policy_check.status: failed
    assert:
      - "'Only delivered orders can be refunded' in error"

  - id: already_refunded_order_is_refused
    input: {order_id: A1007, amount: 50, reason: Please refund again.}
    expect:
      status: failed
    assert:
      - "'already been refunded' in error"

  - id: amount_above_order_total_is_refused
    input: {order_id: A1012, amount: 100, reason: Compensation for the delay.}
    expect:
      status: failed
      steps.assess_request.status: cancelled
    assert:
      - "'must be positive and not exceed' in error"

  - id: zero_amount_is_refused
    input: {order_id: A1012, amount: 0, reason: Testing.}
    expect:
      status: failed
      steps.policy_check.status: failed

  - id: unknown_order_fails_fast
    input: {order_id: Z9999, amount: 10, reason: "Where is my parcel?"}
    expect:
      status: failed
      steps.fetch_order.status: failed
      steps.fetch_order.attempts: 1

  - id: second_refund_of_the_same_order_is_refused
    description: Runs after small_refund_is_automatic refunded A1001 in the same eval run.
    input: {order_id: A1001, amount: 120, reason: The jacket arrived with a torn sleeve.}
    expect:
      status: failed
      steps.issue_refund.status: cancelled
    assert:
      - "'already been refunded' in error"
```

- [ ] **Step 8: Run the tests, then the whole suite**

Run: `PYTHONPATH=src .venv/bin/pytest tests/evals tests/spec -q`
Expected: PASS. (If `FailMode.parse("sometimes")` or `("first:x")` does not raise `ValueError`, read `sandbox/payments.py` and adapt `_fail_mode_issues` to the error it does raise — ledger the ruling.)

Run: `PYTHONPATH=src .venv/bin/pytest tests -q`
Expected: all pass.

- [ ] **Step 9: Checkpoint** — `git status --short`.

---

### Task 3: The eval runner

**Files:**
- Create: `src/cerebellum/evals/runner.py`
- Modify: `src/cerebellum/evals/__init__.py` (export the runner)
- Test: `tests/evals/test_runner.py` (new)

**Interfaces:**
- Consumes: Task 1 store eval methods; Task 2 `LoadedSuite`, `EvalCase`, `run_view`, `check_case`; `Engine(store, settings, ai, *, clock, http_transports, jitter)`, `Engine.start(workflow, input, *, eval_run_id)`, `Engine.decide(run_id, step_id, *, approved, by, comment, resume)`; `Store.list_approvals/list_tasks/resolve_task/get_events`.
- Produces: `EVAL_ACTOR = "eval"`; `new_eval_run_id() -> "ev_" + 8 hex`; `eval_home(settings, eval_run_id) -> Path` (`settings.home / "evals" / id`); `EvalRunner(store, settings, provider, *, set_fail_mode: Callable[[str], Awaitable[None]] | None = None, clock=None, http_transports=None, jitter=0.1)`; `await EvalRunner.run(loaded, *, on_result: Callable[[EvalCase, EvalResultRecord], None] | None = None) -> EvalRunRecord`.

- [ ] **Step 1: Write the failing tests**

`tests/evals/test_runner.py`:

```python
import shutil

import httpx
import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.evals import EvalRunner, eval_home, load_suite
from cerebellum.sandbox.payments import FailMode, PaymentsState, create_payments_app
from cerebellum.templates import template_path

SUITE = template_path("refund") / "evals.yaml"
WORKFLOW = template_path("refund") / "workflow.yaml"
AI_CASES = 9  # cases whose run reaches assess_request (the policy check passes)


@pytest.fixture
def payments():
    return PaymentsState()


@pytest.fixture
def make_runner(store, settings, payments):
    def make(*, sandbox=True):
        async def set_fail_mode(mode):
            payments.set_fail_mode(FailMode.parse(mode))

        return EvalRunner(
            store,
            settings,
            MockProvider(latency=(0, 0)),
            set_fail_mode=set_fail_mode if sandbox else None,
            http_transports={"payments": httpx.ASGITransport(app=create_payments_app(payments))},
            jitter=0,
        )

    return make


def failures(store, record):
    return {
        r.case_id: (r.error, [c for c in r.checks if not c["passed"]])
        for r in store.get_eval_results(record.id)
        if not r.passed
    }


async def test_packaged_suite_passes_in_mock_mode(make_runner, store):
    loaded = load_suite(SUITE, env={})
    seen = []
    record = await make_runner().run(
        loaded, on_result=lambda case, result: seen.append((case.id, result.passed))
    )
    assert failures(store, record) == {}
    assert (record.status, record.total, record.passed, record.failed) == ("completed", 15, 15, 0)
    assert record.baseline_id is None and record.regressions == 0 and record.mock is True
    assert record.pass_rate == 1.0 and record.ended_at is not None
    assert (record.ai_first_try, record.ai_first_ok, record.ai_repairs) == (AI_CASES, AI_CASES, 0)
    assert [case_id for case_id, _ in seen] == [case.id for case in loaded.suite.cases]
    for result in store.get_eval_results(record.id):
        assert store.get_run(result.run_id).eval_run_id == record.id


async def test_suite_is_repeatable_and_isolated(make_runner, store, settings):
    loaded = load_suite(SUITE, env={})
    first = await make_runner().run(loaded)
    second = await make_runner().run(loaded)
    assert failures(store, second) == {}
    assert (second.passed, second.regressions, second.baseline_id) == (15, 0, first.id)
    assert all(r.baseline_passed is True for r in store.get_eval_results(second.id))
    for record in (first, second):
        assert (eval_home(settings, record.id) / "sandbox_orders_db.db").is_file()
    assert not (settings.home / "sandbox_orders_db.db").exists()


async def test_breaking_the_threshold_is_flagged_as_regression(make_runner, store, tmp_path):
    project = tmp_path / "refund"
    shutil.copytree(template_path("refund"), project)
    baseline = await make_runner().run(load_suite(project / "evals.yaml", env={}))
    workflow = project / "workflow.yaml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "approval_threshold: 500", "approval_threshold: 1000"
        ),
        encoding="utf-8",
    )
    record = await make_runner().run(load_suite(project / "evals.yaml", env={}))
    assert record.baseline_id == baseline.id
    assert (record.passed, record.failed, record.regressions) == (12, 3, 3)
    results = {r.case_id: r for r in store.get_eval_results(record.id)}
    assert {case_id for case_id, r in results.items() if r.regression} == {
        "large_refund_approved_by_a_human",
        "large_refund_rejected_by_a_human",
        "amount_above_threshold_needs_approval",
    }
    status = next(
        c for c in results["large_refund_rejected_by_a_human"].checks if c["target"] == "status"
    )
    assert (status["expected"], status["actual"], status["passed"]) == ("rejected", "succeeded", False)
    again = await make_runner().run(load_suite(project / "evals.yaml", env={}))
    assert (again.failed, again.regressions) == (3, 0)  # failing in both runs is not a regression


async def test_eval_tasks_are_closed(make_runner, store):
    await make_runner().run(load_suite(SUITE, env={}))
    assert store.list_tasks(status="open") == []
    [task] = store.list_tasks()
    assert task.resolved_by == "eval" and "payments_outage_opens_a_manual_case" in task.note
    assert store.metrics(0)["runs"] == 0


async def test_case_needing_the_sandbox_fails_with_a_reason_when_it_is_off(
    make_runner, store, tmp_path
):
    path = tmp_path / "evals.yaml"
    path.write_text(
        f"""
suite: needs_sandbox
workflow: {WORKFLOW}
cases:
  - id: outage
    input: {{order_id: A1005, amount: 75}}
    sandbox: always
    expect: {{status: needs_attention}}
  - id: small
    input: {{order_id: A1001, amount: 120}}
    expect: {{status: succeeded}}
""",
        encoding="utf-8",
    )
    record = await make_runner(sandbox=False).run(load_suite(path, env={}))
    outage, small = store.get_eval_results(record.id)
    assert not outage.passed and outage.run_id is None and "not running" in outage.error
    assert small.passed and record.status == "completed"


async def test_interrupted_eval_is_marked_errored(make_runner, store):
    class Boom(Exception):
        pass

    runner = make_runner()
    original = runner.set_fail_mode
    calls = []

    async def flaky_setter(mode):
        calls.append(mode)
        if len(calls) == 2:
            raise Boom("operator pressed Ctrl-C")
        await original(mode)

    runner.set_fail_mode = flaky_setter
    with pytest.raises(Boom):
        await runner.run(load_suite(SUITE, env={}))
    [record] = store.list_eval_runs()
    assert record.status == "errored" and record.error == "operator pressed Ctrl-C"
    assert record.passed == 1 and record.ended_at is not None
    assert store.latest_eval_run("refund_regression") is None
    assert calls[-1] == "never"  # the sandbox is put back to normal
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src .venv/bin/pytest tests/evals -q`
Expected: FAIL — `ImportError: cannot import name 'EvalRunner' from 'cerebellum.evals'`.

- [ ] **Step 3: Write `src/cerebellum/evals/runner.py`**

```python
"""Run an eval suite: one real run per case, decided and checked automatically, then compared
with the previous completed run of the same suite."""

from __future__ import annotations

import contextlib
import dataclasses
import secrets
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

import httpx

from cerebellum.ai.base import AIProvider
from cerebellum.config import Settings
from cerebellum.errors import CerebellumError
from cerebellum.evals.checks import Check, check_case, run_view
from cerebellum.evals.suite import EvalCase, LoadedSuite
from cerebellum.runtime.clock import Clock
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus
from cerebellum.runtime.store import EvalResultRecord, EvalRunRecord, RunRecord, Store

# Recorded as the approver of eval approvals and the resolver of tasks eval cases open.
EVAL_ACTOR = "eval"
FailModeSetter = Callable[[str], Awaitable[None]]


def new_eval_run_id() -> str:
    return "ev_" + secrets.token_hex(4)


def eval_home(settings: Settings, eval_run_id: str) -> Path:
    """Where an eval run's sandbox databases live: fresh per run, so results are repeatable."""
    return settings.home / "evals" / eval_run_id


class EvalRunner:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        provider: AIProvider,
        *,
        set_fail_mode: FailModeSetter | None = None,
        clock: Clock | None = None,
        http_transports: Mapping[str, httpx.AsyncBaseTransport] | None = None,
        jitter: float = 0.1,
    ):
        self.store = store
        self.settings = settings
        self.provider = provider
        self.set_fail_mode = set_fail_mode
        self.clock = clock
        self.http_transports = http_transports
        self.jitter = jitter

    async def run(
        self,
        loaded: LoadedSuite,
        *,
        on_result: Callable[[EvalCase, EvalResultRecord], None] | None = None,
    ) -> EvalRunRecord:
        suite = loaded.suite
        baseline = self.store.latest_eval_run(suite.suite)
        previous = (
            {result.case_id: result.passed for result in self.store.get_eval_results(baseline.id)}
            if baseline
            else {}
        )
        eval_run_id = new_eval_run_id()
        home = eval_home(self.settings, eval_run_id)
        home.mkdir(parents=True, exist_ok=True)
        engine = Engine(
            self.store,
            dataclasses.replace(self.settings, home=home),
            self.provider,
            clock=self.clock,
            http_transports=self.http_transports,
            jitter=self.jitter,
        )
        self.store.create_eval_run(
            eval_run_id,
            suite=suite.suite,
            suite_path=str(loaded.path),
            workflow_name=loaded.workflow.name,
            workflow_digest=loaded.workflow.digest,
            mock=self.provider.mock,
            total=len(suite.cases),
            baseline_id=baseline.id if baseline else None,
        )
        run_ids: list[str] = []
        try:
            for position, case in enumerate(suite.cases):
                result = await self._run_case(
                    engine, loaded, eval_run_id, position, case, previous.get(case.id)
                )
                if result.run_id:
                    run_ids.append(result.run_id)
                if on_result is not None:
                    on_result(case, result)
        except BaseException as exc:  # interrupted or broken: never leave the eval "running"
            self.store.finish_eval_run(
                eval_run_id,
                status="errored",
                error=str(exc) or type(exc).__name__,
                **self._ai_stats(run_ids),
            )
            raise
        finally:
            if self.set_fail_mode is not None:
                with contextlib.suppress(Exception):
                    await self.set_fail_mode("never")
        return self.store.finish_eval_run(
            eval_run_id, status="completed", **self._ai_stats(run_ids)
        )

    async def _run_case(
        self,
        engine: Engine,
        loaded: LoadedSuite,
        eval_run_id: str,
        position: int,
        case: EvalCase,
        baseline_passed: bool | None,
    ) -> EvalResultRecord:
        record: RunRecord | None = None
        error: str | None = None
        mode = loaded.fail_mode(case)
        try:
            if self.set_fail_mode is not None:
                await self.set_fail_mode(mode)
            elif mode != "never":
                raise CerebellumError(
                    f"this case needs the sandbox payments API (fail mode {mode}), "
                    "which is not running"
                )
            record = await engine.start(loaded.workflow, case.input, eval_run_id=eval_run_id)
            record = await self._decide(engine, record, loaded, case)
        except CerebellumError as exc:
            error = str(exc)
        checks: list[Check] = []
        cost = 0.0
        duration: float | None = None
        if record is not None:
            run = self.store.get_run(record.run_id)
            view = run_view(
                run,
                self.store.get_steps(run.run_id),
                self.store.list_tasks(run_id=run.run_id),
                self.store.list_approvals(run_id=run.run_id),
            )
            checks = check_case(case.expect, case.asserts, view)
            cost, duration = run.cost_usd, view["run"]["duration_s"]
            self._close_tasks(run.run_id, case.id)
        return self.store.record_eval_result(
            eval_run_id,
            case_id=case.id,
            position=position,
            run_id=record.run_id if record else None,
            passed=error is None and all(check.passed for check in checks),
            baseline_passed=baseline_passed,
            checks=[check.to_json() for check in checks],
            error=error,
            cost_usd=cost,
            duration_s=duration,
        )

    async def _decide(
        self, engine: Engine, record: RunRecord, loaded: LoadedSuite, case: EvalCase
    ) -> RunRecord:
        """Answer every approval the run waits for with the case's decision, as `eval`."""
        approved = loaded.decision(case) == "approved"
        comment = f"decided by eval case {case.id}"
        # An approval step waits at most once per run, so this ends; the bound is a guard.
        for _ in range(len(loaded.workflow.steps) + 1):
            if record.status is not RunStatus.WAITING_APPROVAL:
                break
            pending = self.store.list_approvals(run_id=record.run_id, status="pending")
            if not pending:
                break
            for approval in pending[:-1]:
                await engine.decide(
                    record.run_id,
                    approval.step_id,
                    approved=approved,
                    by=EVAL_ACTOR,
                    comment=comment,
                    resume=False,
                )
            record = await engine.decide(
                record.run_id, pending[-1].step_id, approved=approved, by=EVAL_ACTOR, comment=comment
            )
        return record

    def _close_tasks(self, run_id: str, case_id: str) -> None:
        """Eval traffic must not land in the people's inbox: close the tasks a case opened."""
        for task in self.store.list_tasks(run_id=run_id, status="open"):
            self.store.resolve_task(
                task.id, by=EVAL_ACTOR, note=f"opened by eval case {case_id}; closed automatically"
            )

    def _ai_stats(self, run_ids: list[str]) -> dict[str, int]:
        """How often an AI step's first reply already matched its schema, and how many repair
        turns were needed (from the llm.call events of the case runs)."""
        first_try = first_ok = repairs = 0
        for run_id in run_ids:
            for event in self.store.get_events(run_id):
                if event.type != "llm.call":
                    continue
                if event.data.get("repair", 0) == 0:
                    first_try += 1
                    first_ok += int(bool(event.data.get("ok")))
                else:
                    repairs += 1
        return {"ai_first_try": first_try, "ai_first_ok": first_ok, "ai_repairs": repairs}
```

- [ ] **Step 4: Export the runner from `src/cerebellum/evals/__init__.py`**

```python
"""Eval suites: pin a workflow's behaviour case by case and catch regressions."""

from cerebellum.evals.checks import Check, check_case, resolve, run_view
from cerebellum.evals.runner import EVAL_ACTOR, EvalRunner, eval_home, new_eval_run_id
from cerebellum.evals.suite import EvalCase, EvalSuite, LoadedSuite, load_suite

__all__ = [
    "EVAL_ACTOR",
    "Check",
    "EvalCase",
    "EvalRunner",
    "EvalSuite",
    "LoadedSuite",
    "check_case",
    "eval_home",
    "load_suite",
    "new_eval_run_id",
    "resolve",
    "run_view",
]
```

- [ ] **Step 5: Run the tests, then the whole suite**

Run: `PYTHONPATH=src .venv/bin/pytest tests/evals -q`
Expected: PASS. If a packaged case fails, `failures(store, record)` in the assertion message names the case and its failed checks: fix the case's expectation only when the engine's behaviour matches the spec (§3.5) — ledger it.

Run: `PYTHONPATH=src .venv/bin/pytest tests -q`
Expected: all pass.

- [ ] **Step 6: Checkpoint** — `git status --short`.

---

### Task 4: `cerebellum eval` and `init` with the suite

**Files:**
- Modify: `src/cerebellum/cli/app.py`, `src/cerebellum/cli/render.py`
- Test: `tests/cli/test_eval_cli.py` (new), `tests/cli/test_cli.py` (init test)

**Interfaces:**
- Consumes: Task 1 `DEFAULT_EVAL_MIN_PASS`, `Store.get_eval_run/get_eval_results`; Task 3 `EvalRunner`, `load_suite`, `LoadedSuite`; `SandboxHandle.set_fail_mode(mode)` (blocking HTTP call); `render.header/fmt_duration/fmt_cost/ACCENT/MUTED/issues_view`.
- Produces: CLI `cerebellum eval <suite> [--mock] [--min-pass 0.9] [--no-sandbox]` (exit 0 / 1 below min-pass / 2 invalid suite); `_sandbox(...)` now yields `SandboxHandle | None`; `render.eval_case_line(result) -> Group`, `render.describe_check(check) -> str`, `render.eval_summary(record, baseline, regressed, min_pass) -> Group`; `init` copies `evals.yaml`.

- [ ] **Step 1: Write the failing tests**

`tests/cli/test_eval_cli.py`:

```python
import re

import pytest
from rich.console import Console
from typer.testing import CliRunner

from cerebellum.cli import app as cli
from cerebellum.templates import template_path

WORKFLOW = template_path("refund") / "workflow.yaml"
EVAL_ID = re.compile(r"ev_[0-9a-f]{8}")
SUITE = """
suite: smoke
workflow: {workflow}
cases:
  - id: small_refund
    input: {{order_id: A1001, amount: 120, reason: Torn sleeve.}}
    expect: {{status: succeeded, output.decision: {small}}}
  - id: chargeback_denied
    input: {{order_id: A1003, amount: 60, reason: I will file a chargeback.}}
    expect: {{output.decision: denied}}
  - id: unknown_order
    input: {{order_id: Z9999, amount: 10}}
    expect: {{status: failed}}
"""


@pytest.fixture
def runner(tmp_path, monkeypatch, free_port):
    monkeypatch.setenv("CEREBELLUM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CEREBELLUM_MOCK", "1")
    monkeypatch.setenv("CEREBELLUM_SANDBOX_PORT", str(free_port))
    monkeypatch.delenv("PAYMENTS_URL", raising=False)
    monkeypatch.delenv("ORDERS_DSN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=200, highlight=False))
    monkeypatch.setattr(cli, "err_console", Console(width=200, highlight=False, stderr=True))
    return CliRunner()


def invoke(runner, *args):
    result = runner.invoke(cli.app, [str(arg) for arg in args])
    result.text = result.stdout + (result.stderr or "")
    return result


def write_suite(tmp_path, small="refunded"):
    path = tmp_path / "smoke.yaml"
    path.write_text(SUITE.format(workflow=WORKFLOW, small=small), encoding="utf-8")
    return path


def test_eval_runs_a_suite_and_compares_with_the_previous_run(runner, tmp_path):
    suite = write_suite(tmp_path)
    first = invoke(runner, "eval", suite)
    assert first.exit_code == 0, first.text
    assert "3/3 passed" in first.text and "first run of this suite" in first.text
    assert "mock AI" in first.text
    second = invoke(runner, "eval", suite)
    assert second.exit_code == 0, second.text
    first_id = EVAL_ID.search(first.text).group(0)
    assert f"vs {first_id}: 3/3 → 3/3" in second.text and "no regressions" in second.text


def test_eval_exit_code_follows_min_pass(runner, tmp_path):
    assert invoke(runner, "eval", write_suite(tmp_path)).exit_code == 0  # the baseline
    broken = write_suite(tmp_path, small="manual")
    result = invoke(runner, "eval", broken)
    assert result.exit_code == 1, result.text  # 2/3 is below the default 90%
    assert 'output.decision: expected "manual" · got "refunded"' in result.text
    assert "↓ regression" in result.text and "1 regression(s): small_refund" in result.text
    lenient = invoke(runner, "eval", broken, "--min-pass", "0.6")
    assert lenient.exit_code == 0, lenient.text


def test_eval_rejects_an_invalid_suite_with_exit_2(runner, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        f"suite: bad\nworkflow: {WORKFLOW}\ncases:\n"
        "  - id: a\n    input: {order_id: A1}\n    expect: {steps.nope.status: x}\n",
        encoding="utf-8",
    )
    result = invoke(runner, "eval", path)
    assert result.exit_code == 2, result.text
    assert "cases[0].expect.steps.nope.status" in result.text
    assert "cases[0].input.amount" in result.text


def test_packaged_suite_passes_from_the_cli(runner):
    result = invoke(runner, "eval", template_path("refund") / "evals.yaml")
    assert result.exit_code == 0, result.text
    assert "15/15 passed" in result.text
```

In `tests/cli/test_cli.py`, extend `test_init_scaffolds_files_once` after the `.env.example` assertion:

```python
    suite = target / "workflows" / "refund" / "evals.yaml"
    assert suite.exists()
    assert load_suite(suite).suite.suite == "refund_regression"
    assert "cerebellum eval" in first.text
```

and add `from cerebellum.evals import load_suite` to its imports.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src .venv/bin/pytest tests/cli -q`
Expected: FAIL — `No such command 'eval'` (exit 2) and the init test's missing `evals.yaml`.

- [ ] **Step 3: Add the eval renderers to `src/cerebellum/cli/render.py`**

Add `EvalResultRecord, EvalRunRecord` to the existing `from cerebellum.runtime.store import ...` line (and `import json` / `Mapping` if not yet imported), then append:

```python
def describe_check(check: Mapping[str, Any]) -> str:
    """One line for a failed check: what the case expected and what the run did."""
    if check["kind"] == "assert":
        return f"assert {check['target']} → {check.get('actual') or 'false'}"
    expected = json.dumps(check["expected"], ensure_ascii=False, default=str)
    if check.get("missing"):
        return f"{check['target']}: expected {expected} · missing"
    actual = json.dumps(check["actual"], ensure_ascii=False, default=str)
    return f"{check['target']}: expected {expected} · got {actual}"


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
```

(`Group`, `Text`, `Sequence`, `Any`, `fmt_duration`, `fmt_cost`, `ACCENT`, `MUTED` already exist in `render.py`; add whichever import is missing.)

- [ ] **Step 4: Wire the command into `src/cerebellum/cli/app.py`**

Imports: change `from cerebellum.config import Settings` to `from cerebellum.config import DEFAULT_EVAL_MIN_PASS, Settings` and add `from cerebellum.evals import EvalRunner, LoadedSuite, load_suite`.

`REFUND_TEMPLATE_FILES` gains `"evals.yaml",` after `"seed.sql",`.

`_sandbox` yields its handle (callers that use `with _sandbox(...):` are unaffected):

```python
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
```

Helpers next to `_load`:

```python
def _load_suite(path: Path) -> LoadedSuite:
    try:
        return load_suite(path)
    except SpecError as exc:
        _invalid(f"{path} is invalid", exc)


def _fail_mode_setter(handle: SandboxHandle | None) -> Callable[[str], Awaitable[None]] | None:
    if handle is None:
        return None

    async def set_fail_mode(mode: str) -> None:
        await asyncio.to_thread(handle.set_fail_mode, mode)

    return set_fail_mode
```

In `init`, after the `cerebellum run ...` hint:

```python
    console.print(
        Text(f"  cerebellum eval {target / 'evals.yaml'} --mock", style=render.ACCENT)
    )
```

The command (before `demo`):

```python
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
    with _sandbox(settings, "never", enabled=not no_sandbox) as handle:
        loaded = _load_suite(suite)  # after the sandbox starts: connector URLs may point at it
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
```

- [ ] **Step 5: Run the CLI tests, then the whole suite**

Run: `PYTHONPATH=src .venv/bin/pytest tests/cli -q`
Expected: PASS (the packaged-suite test takes a few seconds: real retry back-off for the flaky and outage cases).

Run: `PYTHONPATH=src .venv/bin/pytest tests -q && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests`
Expected: all pass, ruff clean (run `.venv/bin/ruff format src tests` first if only formatting differs).

- [ ] **Step 6: Checkpoint** — `git status --short`.

---

### Task 5: Eval API

**Files:**
- Modify: `src/cerebellum/server/serialize.py`, `src/cerebellum/server/app.py`
- Test: `tests/server/test_api_evals.py` (new)

**Interfaces:**
- Consumes: Task 1 store eval methods and `list_runs(include_evals=)`; Task 3 `EvalRunner`.
- Produces: `serialize.eval_run_json(record)` = record fields + `pass_rate`, `duration_s`, `ai_first_pass_rate`; `serialize.eval_result_json(result)` = fields + `regression`; `GET /api/evals?suite=&limit=` → `{"evals": [...]}` newest first; `GET /api/evals/{id}` → `{"eval", "baseline" (or null), "results", "history"}` (history: the suite's last `EVAL_HISTORY = 30` runs, oldest first); `GET /api/runs` no longer lists eval runs. Unknown eval id → 404.

- [ ] **Step 1: Write the failing tests**

`tests/server/test_api_evals.py`:

```python
import httpx
import pytest
from fastapi.testclient import TestClient

from cerebellum.ai.mock import MockProvider
from cerebellum.evals import EvalRunner, load_suite
from cerebellum.sandbox.payments import PaymentsState, create_payments_app
from cerebellum.server.app import create_app
from cerebellum.templates import template_path

WORKFLOW = template_path("refund") / "workflow.yaml"


@pytest.fixture
def transports():
    return {"payments": httpx.ASGITransport(app=create_payments_app(PaymentsState()))}


@pytest.fixture
def client(store, settings, transports):
    app = create_app(
        settings,
        provider=MockProvider(latency=(0, 0)),
        mode="mock AI (requested)",
        workflows_dir=template_path("refund"),
        store=store,
        http_transports=transports,
    )
    with TestClient(app) as test_client:
        yield test_client


def suite(tmp_path, decision):
    path = tmp_path / "evals.yaml"
    path.write_text(
        f"""
suite: api_smoke
workflow: {WORKFLOW}
cases:
  - id: small
    input: {{order_id: A1001, amount: 120}}
    expect: {{output.decision: {decision}}}
  - id: unknown
    input: {{order_id: Z9999, amount: 10}}
    expect: {{status: failed}}
""",
        encoding="utf-8",
    )
    return load_suite(path, env={})


async def run_eval(store, settings, transports, loaded):
    runner = EvalRunner(
        store, settings, MockProvider(latency=(0, 0)), http_transports=transports, jitter=0
    )
    return await runner.run(loaded)


async def test_eval_endpoints_list_runs_and_show_cases_with_regressions(
    client, store, settings, transports, tmp_path
):
    first = await run_eval(store, settings, transports, suite(tmp_path, "refunded"))
    second = await run_eval(store, settings, transports, suite(tmp_path, "manual"))

    listed = client.get("/api/evals").json()["evals"]
    assert [e["id"] for e in listed] == [second.id, first.id]
    assert listed[0]["pass_rate"] == 0.5 and listed[0]["regressions"] == 1
    assert listed[1]["pass_rate"] == 1.0 and listed[1]["duration_s"] is not None
    assert listed[1]["ai_first_pass_rate"] == 1.0

    detail = client.get(f"/api/evals/{second.id}").json()
    assert detail["eval"]["baseline_id"] == first.id and detail["baseline"]["id"] == first.id
    small, unknown = detail["results"]
    assert small["case_id"] == "small" and small["passed"] is False and small["regression"] is True
    assert (small["checks"][0]["expected"], small["checks"][0]["actual"]) == ("manual", "refunded")
    assert unknown["passed"] is True and unknown["regression"] is False
    assert [h["id"] for h in detail["history"]] == [first.id, second.id]
    run = client.get(f"/api/runs/{small['run_id']}").json()["run"]
    assert run["eval_run_id"] == second.id
    assert client.get("/api/evals?suite=nope").json()["evals"] == []
    assert client.get(f"/api/evals/{first.id}").json()["baseline"] is None


def test_unknown_eval_is_404(client):
    response = client.get("/api/evals/ev_nope")
    assert response.status_code == 404 and "ev_nope" in response.json()["detail"]


async def test_runs_endpoint_leaves_eval_runs_out(client, store, settings, transports, tmp_path):
    await run_eval(store, settings, transports, suite(tmp_path, "refunded"))
    assert client.get("/api/runs").json()["runs"] == []
    metrics = client.get("/api/metrics").json()
    assert metrics["runs"] == 0 and metrics["open_tasks"] == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src .venv/bin/pytest tests/server -q`
Expected: FAIL — `/api/evals` answers with the SPA fallback (200 HTML / JSON decode error) or 404, and `/api/runs` still lists the eval runs.

- [ ] **Step 3: Serializers in `src/cerebellum/server/serialize.py`**

Add `EvalResultRecord, EvalRunRecord` to the `cerebellum.runtime.store` import, then:

```python
def eval_run_json(record: EvalRunRecord) -> dict[str, Any]:
    data = dataclasses.asdict(record)
    data["pass_rate"] = record.pass_rate
    data["duration_s"] = None if record.ended_at is None else record.ended_at - record.created_at
    data["ai_first_pass_rate"] = (
        record.ai_first_ok / record.ai_first_try if record.ai_first_try else None
    )
    return data


def eval_result_json(result: EvalResultRecord) -> dict[str, Any]:
    data = dataclasses.asdict(result)
    data["regression"] = result.regression
    return data
```

- [ ] **Step 4: Routes in `src/cerebellum/server/app.py`**

Below `UI_MISSING`:

```python
# How many runs of a suite the eval detail returns for its trend line.
EVAL_HISTORY = 30
```

In `list_runs`, leave eval runs out:

```python
        runs = active.list_runs(status=wanted, limit=min(max(limit, 1), 500), include_evals=False)
```

After the `/api/metrics` route:

```python
    @app.get("/api/evals")
    async def list_evals(request: Request, suite: str | None = None, limit: int = 100):
        active, _, _ = parts(request)
        records = active.list_eval_runs(suite=suite, limit=min(max(limit, 1), 500))
        return {"evals": [js.eval_run_json(record) for record in records]}

    @app.get("/api/evals/{eval_run_id}")
    async def eval_detail(eval_run_id: str, request: Request):
        active, _, _ = parts(request)
        record = active.get_eval_run(eval_run_id)
        baseline = None
        if record.baseline_id:
            with contextlib.suppress(NotFound):
                baseline = active.get_eval_run(record.baseline_id)
        history = active.list_eval_runs(suite=record.suite, limit=EVAL_HISTORY)
        history.reverse()
        return {
            "eval": js.eval_run_json(record),
            "baseline": None if baseline is None else js.eval_run_json(baseline),
            "results": [js.eval_result_json(r) for r in active.get_eval_results(eval_run_id)],
            "history": [js.eval_run_json(r) for r in history],
        }
```

- [ ] **Step 5: Run the server tests, then the whole suite**

Run: `PYTHONPATH=src .venv/bin/pytest tests/server -q`
Expected: PASS.

Run: `PYTHONPATH=src .venv/bin/pytest tests -q`
Expected: all pass.

- [ ] **Step 6: Checkpoint** — `git status --short`.

---

### Task 6: `cerebellum new`

**Files:**
- Create: `src/cerebellum/authoring.py`
- Modify: `src/cerebellum/cli/app.py`
- Test: `tests/test_authoring.py`, `tests/cli/test_new_cli.py` (new)

**Interfaces:**
- Consumes: `AIProvider.generate(request, messages) -> AIResult`, `AIRequest`, `AnthropicProvider(pricing=)`, `Pricing.load(path)`, `has_anthropic_credentials()`, `parse_workflow(text, *, base_dir, env)`, `Workflow.model_json_schema()`, `template_path("refund")`, Task 1 `DEFAULT_NEW_ATTEMPTS`.
- Produces: `authoring.DRAFT_HEADER` (first line of every draft), `DRAFT_SCHEMA`, `workflow_json_schema() -> dict`, `build_prompt(description) -> str`, `Draft(yaml, summary, workflow, attempts, cost_usd)`, `DraftError(attempts, issues)`, `await draft_workflow(provider, description, *, model, base_dir, env=None, attempts=DEFAULT_NEW_ATTEMPTS) -> Draft`; CLI `cerebellum new "<description>" -o <file> [--force]` and `cli._draft_provider(settings) -> AIProvider`.

- [ ] **Step 1: Write the failing tests**

`tests/test_authoring.py`:

```python
import json

import pytest

from cerebellum.ai.base import AIResult, Usage
from cerebellum.authoring import (
    DRAFT_SCHEMA,
    DraftError,
    build_prompt,
    draft_workflow,
    workflow_json_schema,
)

VALID = """\
name: invoice_approval
input:
  amount: {type: number, required: true}
steps:
  - id: check_amount
    type: validate
    rules:
      - {expr: "input.amount > 0", message: Amount must be positive}
  - id: finance_approval
    type: approval
    needs: [check_amount]
    when: "input.amount > 1000"
    title: "Approve invoice of {{ input.amount }}"
"""
INVALID = "name: invoice_approval\nsteps: []\n"


class ScriptedProvider:
    name = "scripted"
    mock = False

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def generate(self, request, messages):
        self.calls.append((request, [dict(m) for m in messages]))
        return AIResult(
            text=self.replies.pop(0),
            model=request.model,
            usage=Usage(),
            cost_usd=0.01,
            mock=False,
            stop_reason="end_turn",
            latency_ms=1.0,
        )


def reply(yaml_text, summary="Approves invoices."):
    return json.dumps({"yaml": yaml_text, "summary": summary})


async def test_valid_draft_is_returned_on_the_first_attempt(tmp_path):
    provider = ScriptedProvider(reply(VALID))
    draft = await draft_workflow(
        provider, "Approve invoices over $1000", model="claude-opus-5-5", base_dir=tmp_path, env={}
    )
    assert draft.workflow.name == "invoice_approval" and draft.attempts == 1
    assert draft.summary == "Approves invoices." and draft.cost_usd == pytest.approx(0.01)
    request, messages = provider.calls[0]
    assert request.schema == DRAFT_SCHEMA and request.model == "claude-opus-5-5"
    assert "Approve invoices over $1000" in messages[0]["content"]


async def test_invalid_draft_is_repaired_from_the_loader_issues(tmp_path):
    provider = ScriptedProvider(reply(INVALID), "not json", reply("```yaml\n" + VALID + "```"))
    draft = await draft_workflow(provider, "Approve invoices", model="m", base_dir=tmp_path, env={})
    assert draft.attempts == 3 and draft.cost_usd == pytest.approx(0.03)
    assert draft.yaml.startswith("name: invoice_approval")
    second = provider.calls[1][1]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert "failed validation" in second[2]["content"] and "steps" in second[2]["content"]
    third = provider.calls[2][1]
    assert "requested JSON object" in third[4]["content"]


async def test_draft_gives_up_after_the_attempt_budget(tmp_path):
    provider = ScriptedProvider(reply(INVALID), reply(INVALID), reply(INVALID))
    with pytest.raises(DraftError) as exc:
        await draft_workflow(provider, "x", model="m", base_dir=tmp_path, env={})
    assert exc.value.attempts == 3 and exc.value.issues[0].path == "steps"
    assert len(provider.calls) == 3


def test_prompt_carries_schema_step_guide_and_example():
    prompt = build_prompt("  Refund customers  ")
    assert "<process>\nRefund customers\n</process>" in prompt
    assert "approval: title, show" in prompt and "name: refund_request" in prompt
    schema = workflow_json_schema()
    assert {"name", "steps", "connectors", "fallbacks"} <= set(schema["properties"])
    assert not {"source_yaml", "base_dir", "digest"} & set(schema["properties"])
    assert json.dumps(schema, separators=(",", ":")) in prompt


@pytest.mark.live
async def test_claude_drafts_a_valid_workflow(tmp_path):
    from cerebellum.ai import AnthropicProvider

    draft = await draft_workflow(
        AnthropicProvider(),
        "When a customer cancels a subscription, look the account up in PostgreSQL, ask a "
        "manager to approve refunds over $200, call the billing API to cancel, and open a "
        "manual task if billing keeps failing.",
        model="claude-opus-5-5",
        base_dir=tmp_path,
    )
    assert draft.workflow.steps
```

`tests/cli/test_new_cli.py`:

```python
import json

import pytest
from rich.console import Console
from typer.testing import CliRunner

from cerebellum.ai.base import AIResult, Usage
from cerebellum.authoring import DRAFT_HEADER
from cerebellum.cli import app as cli

VALID = """\
name: invoice_approval
steps:
  - id: check_amount
    type: validate
    rules:
      - {expr: "input.amount > 0", message: Amount must be positive}
"""


class ScriptedProvider:
    name = "scripted"
    mock = False

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def generate(self, request, messages):
        self.calls.append(messages)
        return AIResult(
            text=self.replies.pop(0),
            model=request.model,
            usage=Usage(),
            cost_usd=0.0,
            mock=False,
            stop_reason="end_turn",
            latency_ms=1.0,
        )


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("CEREBELLUM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CEREBELLUM_MOCK", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=200, highlight=False))
    monkeypatch.setattr(cli, "err_console", Console(width=200, highlight=False, stderr=True))
    return CliRunner()


def invoke(runner, *args):
    result = runner.invoke(cli.app, [str(arg) for arg in args])
    result.text = result.stdout + (result.stderr or "")
    return result


def use_provider(monkeypatch, *replies):
    provider = ScriptedProvider(*replies)
    monkeypatch.setattr(cli, "_draft_provider", lambda settings: provider)
    return provider


def test_new_writes_a_validated_draft_with_a_review_header(runner, monkeypatch, tmp_path):
    use_provider(monkeypatch, json.dumps({"yaml": VALID, "summary": "Checks invoice amounts."}))
    target = tmp_path / "workflows" / "invoice.yaml"
    result = invoke(runner, "new", "Check invoice amounts", "-o", target)
    assert result.exit_code == 0, result.text
    text = target.read_text(encoding="utf-8")
    assert text.startswith(DRAFT_HEADER) and "name: invoice_approval" in text
    assert "review it before running" in result.text and "Checks invoice amounts." in result.text
    assert invoke(runner, "validate", target).exit_code == 0


def test_new_refuses_to_overwrite_without_force(runner, monkeypatch, tmp_path):
    provider = use_provider(monkeypatch, json.dumps({"yaml": VALID, "summary": ""}))
    target = tmp_path / "invoice.yaml"
    target.write_text("keep me\n", encoding="utf-8")
    result = invoke(runner, "new", "x", "-o", target)
    assert result.exit_code == 2 and "--force" in result.text
    assert target.read_text(encoding="utf-8") == "keep me\n" and provider.calls == []
    assert invoke(runner, "new", "x", "-o", target, "--force").exit_code == 0
    assert target.read_text(encoding="utf-8").startswith(DRAFT_HEADER)


def test_new_reports_issues_when_the_draft_stays_invalid(runner, monkeypatch, tmp_path):
    bad = json.dumps({"yaml": "name: x\nsteps: []\n", "summary": ""})
    use_provider(monkeypatch, bad, bad, bad)
    target = tmp_path / "invoice.yaml"
    result = invoke(runner, "new", "x", "-o", target)
    assert result.exit_code == 2, result.text
    assert "after 3 attempt(s)" in result.text and "steps" in result.text
    assert not target.exists()


def test_new_without_credentials_writes_nothing(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "has_anthropic_credentials", lambda: False)
    target = tmp_path / "invoice.yaml"
    result = invoke(runner, "new", "x", "-o", target)
    assert result.exit_code == 1 and "ANTHROPIC_API_KEY" in result.text
    assert not target.exists()


def test_new_refuses_forced_mock_mode(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("CEREBELLUM_MOCK", "1")
    result = invoke(runner, "new", "x", "-o", tmp_path / "w.yaml")
    assert result.exit_code == 1 and "CEREBELLUM_MOCK" in result.text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_authoring.py tests/cli -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cerebellum.authoring'`.

- [ ] **Step 3: Write `src/cerebellum/authoring.py`**

```python
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
Every step: id, type, needs, when, timeout, retry {max, backoff, base, max_delay}, on_failure {fallback}, description.
- query: connector (postgres), sql with :name binds only (never templated), params, expect one|many|none|any
- http: connector (rest), method, path, body, headers, query; an Idempotency-Key is sent automatically
- ai: prompt, system, output_schema (a JSON Schema object), effort low|medium|high, mock [{when, output}] so it also runs offline
- validate: rules [{expr, message}]; any false rule fails the run
- approval: title, show [step ids], timeout, on_timeout approve|reject; pauses the run for a human
- task: title, assignee, payload; opens a manual task (the usual fallback)
Rules:
- Conditions (when, rules.expr) and templates ("{{ ... }}") are Jinja over input, params, steps.<id>.output/status/error/attempts and run; fallbacks also see failure.step and failure.error.
- Durations are strings such as 500ms, 30s, 5m, 24h.
- Connectors are declared once under `connectors`: postgres {dsn, seed} or rest {base_url, headers, timeout}. Only connector values may use ${VAR:-default}; always give a default (dsn "sandbox" is a local SQLite sandbox).
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
```

- [ ] **Step 4: Add the command to `src/cerebellum/cli/app.py`**

Imports: `from cerebellum.ai import AnthropicProvider, select_provider`, `from cerebellum.ai.base import AIProvider`, `from cerebellum.ai.pricing import Pricing`, `from cerebellum.authoring import DRAFT_HEADER, DraftError, draft_workflow`, and `has_anthropic_credentials` added to the `cerebellum.config` import.

Helper next to `_start_sandbox`:

```python
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
```

The command (after `show`):

```python
@app.command()
def new(
    description: Annotated[str, typer.Argument(help="The business process, in plain language.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Where to write the YAML draft.")],
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Draft a workflow from a description with Claude; review the draft before running it."""
    settings = _settings()
    if output.exists() and not force:
        _fail(f"{output} already exists; pass --force to overwrite it", EXIT_INVALID)
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(DRAFT_HEADER + draft.yaml.rstrip() + "\n", encoding="utf-8")
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
```

- [ ] **Step 5: Run the tests, then the whole suite**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_authoring.py tests/cli -q`
Expected: PASS (the `live` test is deselected by default).

Run: `PYTHONPATH=src .venv/bin/pytest tests -q && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests`
Expected: all pass, ruff clean.

- [ ] **Step 6: Checkpoint** — `git status --short`.

---

### Task 7: Evals page

**Files:**
- Create: `ui/src/lib/evals.ts`, `ui/src/lib/evals.test.ts`, `ui/src/components/Sparkline.tsx`, `ui/src/pages/Evals.tsx`, `ui/src/pages/EvalDetail.tsx`
- Modify: `ui/src/types.ts`, `ui/src/api.ts`, `ui/src/lib/invalidation.ts`, `ui/src/lib/invalidation.test.ts`, `ui/src/App.tsx`, `ui/src/components/Shell.tsx`, `ui/src/pages/RunDetail.tsx`
- Build output: `src/cerebellum/server/static/`

**Interfaces:**
- Consumes: Task 5 JSON (`/api/evals`, `/api/evals/{id}`, `run.eval_run_id`); Phase 2 `Panel`, `PageHeader`, `Meta`, `Label`, `Empty`, `ErrorNote`, `fmt*`, `toneColor`, `useNow`, `ApiError`.
- Produces: routes `/evals`, `/evals/:evalId`; nav item "Evals"; `groupBySuite(runs) -> SuiteSummary[]`, `sparkPoints(values, width, height, max?) -> Point[]`, `describeCheck(check) -> string`, `caseChange(result, hasBaseline) -> "regression" | "fixed" | "new" | null`; `keysFor` adds `["evals"]` for `run.*` events.

- [ ] **Step 1: Write the failing tests**

`ui/src/lib/evals.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { EvalCheck, EvalResult, EvalRun } from "../types";
import { caseChange, describeCheck, groupBySuite, sparkPoints } from "./evals";

const run = (id: string, suite: string, extra: Partial<EvalRun> = {}): EvalRun => ({
  id,
  suite,
  suite_path: `/x/${suite}.yaml`,
  workflow_name: `${suite}_wf`,
  workflow_digest: "d",
  status: "completed",
  mock: true,
  total: 2,
  passed: 2,
  failed: 0,
  regressions: 0,
  cost_usd: 0,
  ai_first_try: 0,
  ai_first_ok: 0,
  ai_repairs: 0,
  baseline_id: null,
  error: null,
  created_at: 0,
  ended_at: 1,
  pass_rate: 1,
  duration_s: 1,
  ai_first_pass_rate: null,
  ...extra,
});

const result = (passed: boolean, baseline_passed: boolean | null): EvalResult => ({
  eval_run_id: "ev_1",
  case_id: "c",
  position: 0,
  run_id: "r_1",
  passed,
  baseline_passed,
  checks: [],
  error: null,
  cost_usd: 0,
  duration_s: 0.1,
  regression: baseline_passed === true && !passed,
});

describe("groupBySuite", () => {
  it("groups newest-first runs by suite, latest suite first, runs oldest first", () => {
    const groups = groupBySuite([run("ev_3", "b"), run("ev_2", "a"), run("ev_1", "b")]);
    expect(groups.map((g) => g.suite)).toEqual(["b", "a"]);
    expect(groups[0].latest.id).toBe("ev_3");
    expect(groups[0].runs.map((r) => r.id)).toEqual(["ev_1", "ev_3"]);
    expect(groups[0].workflow).toBe("b_wf");
  });
});

describe("sparkPoints", () => {
  it("scales values into the box with y growing downward", () => {
    expect(sparkPoints([0, 0.5, 1], 100, 20, 1)).toEqual([
      { x: 0, y: 20, index: 0 },
      { x: 50, y: 10, index: 1 },
      { x: 100, y: 0, index: 2 },
    ]);
  });

  it("skips nulls but keeps their slot, centres a single point and survives all-zero data", () => {
    expect(sparkPoints([null, 2], 10, 10)).toEqual([{ x: 10, y: 0, index: 1 }]);
    expect(sparkPoints([3], 10, 10)).toEqual([{ x: 5, y: 0, index: 0 }]);
    expect(sparkPoints([0, 0], 10, 10).map((p) => p.y)).toEqual([10, 10]);
    expect(sparkPoints([null], 10, 10)).toEqual([]);
  });
});

describe("describeCheck", () => {
  const base: EvalCheck = { kind: "expect", target: "output.decision", passed: false, expected: "refunded", actual: "manual", missing: false };
  it("shows expected and actual values", () => {
    expect(describeCheck(base)).toBe("output.decision: expected refunded · got manual");
    expect(describeCheck({ ...base, expected: 3, actual: 1, target: "steps.x.attempts" })).toBe("steps.x.attempts: expected 3 · got 1");
    expect(describeCheck({ ...base, missing: true, actual: null })).toBe("output.decision: expected refunded · missing");
  });
  it("shows why an assertion failed", () => {
    expect(describeCheck({ ...base, kind: "assert", target: "run.cost_usd < 1", actual: null })).toBe("assert run.cost_usd < 1 → false");
    expect(describeCheck({ ...base, kind: "assert", target: "'x' in error", actual: "TypeError: boom" })).toBe("assert 'x' in error → TypeError: boom");
  });
});

describe("caseChange", () => {
  it("compares a case with the baseline run", () => {
    expect(caseChange(result(false, true), true)).toBe("regression");
    expect(caseChange(result(true, false), true)).toBe("fixed");
    expect(caseChange(result(true, null), true)).toBe("new");
    expect(caseChange(result(false, false), true)).toBeNull();
    expect(caseChange(result(false, null), false)).toBeNull();
  });
});
```

In `ui/src/lib/invalidation.test.ts`, add to the second `it`:

```ts
    expect(keysFor({ type: "run.completed", run_id: "r_1" })).toContainEqual(["evals"]);
    expect(keysFor({ type: "step.started", run_id: "r_1" })).not.toContainEqual(["evals"]);
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npm --prefix ui test`
Expected: FAIL — `Failed to resolve import "./evals"` and the `["evals"]` expectation.

- [ ] **Step 3: Types and API client**

`ui/src/types.ts` — add `eval_run_id: string | null;` to `Run` (after `stale`), and append:

```ts
export interface EvalRun {
  id: string;
  suite: string;
  suite_path: string;
  workflow_name: string;
  workflow_digest: string;
  status: "running" | "completed" | "errored";
  mock: boolean;
  total: number;
  passed: number;
  failed: number;
  regressions: number;
  cost_usd: number;
  ai_first_try: number;
  ai_first_ok: number;
  ai_repairs: number;
  baseline_id: string | null;
  error: string | null;
  created_at: number;
  ended_at: number | null;
  pass_rate: number | null;
  duration_s: number | null;
  ai_first_pass_rate: number | null;
}

export interface EvalCheck {
  kind: "expect" | "assert";
  target: string;
  passed: boolean;
  expected: unknown;
  actual: unknown;
  missing: boolean;
}

export interface EvalResult {
  eval_run_id: string;
  case_id: string;
  position: number;
  run_id: string | null;
  passed: boolean;
  baseline_passed: boolean | null;
  checks: EvalCheck[];
  error: string | null;
  cost_usd: number;
  duration_s: number | null;
  regression: boolean;
}

export interface EvalDetail {
  eval: EvalRun;
  baseline: EvalRun | null;
  results: EvalResult[];
  history: EvalRun[];
}
```

`ui/src/api.ts` — add `EvalDetail, EvalRun` to the type import and two entries to `api`:

```ts
  evals: () => request<{ evals: EvalRun[] }>("/evals").then((r) => r.evals),
  evalRun: (id: string) => request<EvalDetail>(`/evals/${encodeURIComponent(id)}`),
```

- [ ] **Step 4: `ui/src/lib/evals.ts`**

```ts
import type { EvalCheck, EvalResult, EvalRun } from "../types";

export interface SuiteSummary {
  suite: string;
  workflow: string;
  latest: EvalRun;
  /** Oldest first, for trend lines. */
  runs: EvalRun[];
}

/** Group eval runs (newest first, as the API lists them) by suite, most recently run suite first. */
export function groupBySuite(runs: EvalRun[]): SuiteSummary[] {
  const groups = new Map<string, EvalRun[]>();
  for (const run of runs) {
    const list = groups.get(run.suite) ?? [];
    list.push(run);
    groups.set(run.suite, list);
  }
  return [...groups.entries()].map(([suite, list]) => ({
    suite,
    workflow: list[0].workflow_name,
    latest: list[0],
    runs: [...list].reverse(),
  }));
}

export interface Point {
  x: number;
  y: number;
  index: number;
}

/** Scale values into a width × height box for a sparkline (SVG y grows downward). Nulls are
 * skipped but keep their slot on the x axis; `max` pins the top of the scale (1 for rates). */
export function sparkPoints(values: (number | null)[], width: number, height: number, max?: number): Point[] {
  const present = values.filter((value): value is number => value != null);
  if (!present.length) return [];
  const top = max ?? Math.max(...present);
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  return values.flatMap((value, index) =>
    value == null
      ? []
      : [
          {
            x: values.length > 1 ? index * step : width / 2,
            y: top > 0 ? height - (value / top) * height : height,
            index,
          },
        ],
  );
}

const show = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value));

/** One line for a failed check: what the case expected and what the run did. */
export function describeCheck(check: EvalCheck): string {
  if (check.kind === "assert") return `assert ${check.target} → ${check.actual == null ? "false" : show(check.actual)}`;
  if (check.missing) return `${check.target}: expected ${show(check.expected)} · missing`;
  return `${check.target}: expected ${show(check.expected)} · got ${show(check.actual)}`;
}

export type CaseChange = "regression" | "fixed" | "new" | null;

/** How a case moved against the baseline eval run (null without a baseline or without change). */
export function caseChange(result: EvalResult, hasBaseline: boolean): CaseChange {
  if (!hasBaseline) return null;
  if (result.baseline_passed === null) return "new";
  if (result.baseline_passed && !result.passed) return "regression";
  if (!result.baseline_passed && result.passed) return "fixed";
  return null;
}
```

`ui/src/lib/invalidation.ts` — inside `keysFor`, before `return keys;`:

```ts
  if (event.type.startsWith("run.")) keys.push(["evals"]);
```

- [ ] **Step 5: Run the unit tests**

Run: `npm --prefix ui test`
Expected: PASS (17 earlier tests + 7 new).

- [ ] **Step 6: `ui/src/components/Sparkline.tsx`**

```tsx
import { sparkPoints } from "../lib/evals";

const PAD = 3;

export function Sparkline({
  values,
  max,
  highlight,
  width = 160,
  height = 32,
  color = "var(--color-accent)",
}: {
  values: (number | null)[];
  max?: number;
  highlight?: number;
  width?: number;
  height?: number;
  color?: string;
}) {
  const points = sparkPoints(values, width - PAD * 2, height - PAD * 2, max).map((p) => ({ ...p, x: p.x + PAD, y: p.y + PAD }));
  return (
    <svg width={width} height={height} className="block shrink-0" aria-hidden="true">
      <line x1={PAD} x2={width - PAD} y1={height - PAD} y2={height - PAD} stroke="var(--color-line)" />
      {points.length > 1 && <polyline points={points.map((p) => `${p.x},${p.y}`).join(" ")} fill="none" stroke={color} strokeWidth={1.25} />}
      {points.map((p) => (
        <circle
          key={p.index}
          cx={p.x}
          cy={p.y}
          r={p.index === highlight ? 2.75 : 1.5}
          fill={p.index === highlight ? color : "var(--color-bg)"}
          stroke={color}
        />
      ))}
    </svg>
  );
}
```

- [ ] **Step 7: `ui/src/pages/Evals.tsx`**

```tsx
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { api } from "../api";
import { Sparkline } from "../components/Sparkline";
import { Empty, ErrorNote, Label, PageHeader, Panel } from "../components/ui";
import { useNow } from "../hooks/useNow";
import { type SuiteSummary, groupBySuite } from "../lib/evals";
import { fmtAge, fmtCost, fmtDuration, fmtPercent } from "../lib/format";
import { toneColor } from "../lib/status";
import type { EvalRun } from "../types";

const HEADINGS = ["Eval", "Passed", "Regressions", "Cost", "Duration", "AI first try", "AI", "Age"];

export function EvalOutcome({ run }: { run: EvalRun }) {
  if (run.status === "running") return <span style={{ color: toneColor("accent") }}>◐ running · {run.passed + run.failed}/{run.total}</span>;
  if (run.status === "errored") return <span style={{ color: toneColor("fail") }}>✕ errored · {run.passed}/{run.total}</span>;
  const tone = run.failed ? "fail" : "ok";
  return (
    <span className="mono" style={{ color: toneColor(tone) }}>
      {run.passed}/{run.total}
    </span>
  );
}

function Trend({ label, value, values, max, color }: { label: string; value: string; values: (number | null)[]; max?: number; color: string }) {
  return (
    <div className="border-b border-line px-4 py-3 md:border-r md:border-b-0 md:last:border-r-0">
      <Label>{label}</Label>
      <div className="mt-1 flex items-end justify-between gap-3">
        <span className="mono text-[18px] font-medium">{value}</span>
        <Sparkline values={values} max={max} color={color} highlight={values.length - 1} />
      </div>
    </div>
  );
}

function SuitePanel({ summary, now }: { summary: SuiteSummary; now: number }) {
  const navigate = useNavigate();
  const { latest, runs } = summary;
  const recent = [...runs].reverse().slice(0, 10);
  return (
    <Panel
      title={`${summary.suite} · ${summary.workflow}`}
      actions={<span className="mono text-[11px] text-faint">{runs.length} runs</span>}
    >
      <div className="grid grid-cols-1 border-b border-line md:grid-cols-3">
        <Trend
          label="Pass rate"
          value={fmtPercent(latest.pass_rate)}
          values={runs.map((r) => r.pass_rate)}
          max={1}
          color={toneColor(latest.failed ? "fail" : "ok")}
        />
        <Trend label="Cost per eval" value={fmtCost(latest.cost_usd) || "$0"} values={runs.map((r) => r.cost_usd)} color={toneColor("accent")} />
        <Trend label="Duration" value={fmtDuration(latest.duration_s) || "—"} values={runs.map((r) => r.duration_s)} color={toneColor("accent")} />
      </div>
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
          {recent.map((run) => (
            <tr key={run.id} onClick={() => navigate(`/evals/${run.id}`)} className="h-9 cursor-pointer border-b border-line last:border-b-0 hover:bg-raised">
              <td className="px-3">
                <Link to={`/evals/${run.id}`} className="mono text-accent hover:underline" onClick={(e) => e.stopPropagation()}>
                  {run.id}
                </Link>
              </td>
              <td className="px-3">
                <EvalOutcome run={run} />
              </td>
              <td className="mono px-3" style={{ color: run.regressions ? toneColor("fail") : toneColor("faint") }}>
                {run.regressions ? `↓ ${run.regressions}` : "—"}
              </td>
              <td className="mono px-3 text-muted">{fmtCost(run.cost_usd) || "$0"}</td>
              <td className="mono px-3 text-muted">{fmtDuration(run.duration_s ?? now - run.created_at)}</td>
              <td className="mono px-3 text-muted">{fmtPercent(run.ai_first_pass_rate)}</td>
              <td className="px-3 text-faint">{run.mock ? "mock" : "claude"}</td>
              <td className="px-3 text-faint">{fmtAge(run.created_at, now)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

export function Evals() {
  const now = useNow();
  const evals = useQuery({
    queryKey: ["evals"],
    queryFn: api.evals,
    refetchInterval: (query) => (query.state.data?.some((run) => run.status === "running") ? 2000 : false),
  });
  const suites = groupBySuite(evals.data ?? []);
  return (
    <div>
      <PageHeader title="Evals" subtitle="regression suites · cerebellum eval <suite>" />
      <div className="space-y-6 p-6">
        {evals.isLoading ? (
          <Panel>
            <Empty>loading…</Empty>
          </Panel>
        ) : evals.error ? (
          <Panel>
            <Empty>
              <ErrorNote error={evals.error} />
            </Empty>
          </Panel>
        ) : suites.length ? (
          suites.map((summary) => <SuitePanel key={summary.suite} summary={summary} now={now} />)
        ) : (
          <Panel>
            <Empty>
              no eval runs yet — run <span className="mono">cerebellum eval workflows/refund/evals.yaml --mock</span>
            </Empty>
          </Panel>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 8: `ui/src/pages/EvalDetail.tsx`**

```tsx
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { ApiError, api } from "../api";
import { Sparkline } from "../components/Sparkline";
import { Empty, ErrorNote, Meta, PageHeader, Panel } from "../components/ui";
import { useNow } from "../hooks/useNow";
import { type CaseChange, caseChange, describeCheck } from "../lib/evals";
import { fmtCost, fmtDuration, fmtPercent } from "../lib/format";
import { type Tone, toneColor } from "../lib/status";
import type { EvalResult } from "../types";
import { EvalOutcome } from "./Evals";

const CHANGE: Record<Exclude<CaseChange, null>, [string, Tone]> = {
  regression: ["↓ regression", "fail"],
  fixed: ["↑ fixed", "ok"],
  new: ["new", "faint"],
};

function CaseRow({ result, hasBaseline }: { result: EvalResult; hasBaseline: boolean }) {
  const change = caseChange(result, hasBaseline);
  const failed = result.checks.filter((check) => !check.passed);
  return (
    <div className="border-b border-line px-3 py-2.5 last:border-b-0">
      <div className="flex items-center gap-3 text-[12.5px]">
        <span className="mono w-4" style={{ color: toneColor(result.passed ? "ok" : "fail") }}>
          {result.passed ? "●" : "✕"}
        </span>
        <span className="min-w-0 flex-1 truncate">{result.case_id}</span>
        {change && (
          <span className="mono text-[11px]" style={{ color: toneColor(CHANGE[change][1]) }}>
            {CHANGE[change][0]}
          </span>
        )}
        {result.run_id ? (
          <Link to={`/runs/${result.run_id}`} className="mono text-accent hover:underline">
            {result.run_id}
          </Link>
        ) : (
          <span className="mono text-faint">no run</span>
        )}
        <span className="mono w-16 text-right text-muted">{fmtDuration(result.duration_s)}</span>
      </div>
      {(failed.length > 0 || result.error) && (
        <div className="mono mt-1.5 space-y-0.5 pl-7 text-[11.5px] text-fail">
          {result.error && <div>{result.error}</div>}
          {failed.map((check) => (
            <div key={`${check.kind}:${check.target}`}>{describeCheck(check)}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export function EvalDetail() {
  const { evalId = "" } = useParams();
  const now = useNow();
  const detail = useQuery({
    queryKey: ["evals", evalId],
    queryFn: () => api.evalRun(evalId),
    refetchInterval: (query) => (query.state.data?.eval.status === "running" ? 1500 : false),
  });

  if (detail.isLoading) return <Empty>loading…</Empty>;
  if (!detail.data) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div>
        <PageHeader title="Eval" />
        <Empty>{missing ? `eval ${evalId} not found` : <ErrorNote error={detail.error} />}</Empty>
      </div>
    );
  }

  const { eval: run, baseline, results, history } = detail.data;
  const failing = results.filter((result) => !result.passed).length;
  return (
    <div>
      <PageHeader
        title={<span className="mono">{run.id}</span>}
        subtitle={
          <Link to="/evals" className="hover:text-muted">
            {run.suite} · {run.workflow_name}
          </Link>
        }
      />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-6 py-2.5">
        <EvalOutcome run={run} />
        <Meta label="pass rate">{fmtPercent(run.pass_rate)}</Meta>
        <Meta label="vs">
          {baseline ? (
            <>
              <Link to={`/evals/${baseline.id}`} className="text-accent hover:underline">
                {baseline.id}
              </Link>{" "}
              {baseline.passed}/{baseline.total}
            </>
          ) : (
            "first run"
          )}
        </Meta>
        <Meta label="regressions">
          <span style={{ color: run.regressions ? toneColor("fail") : undefined }}>{run.regressions}</span>
        </Meta>
        <Meta label="cost">{fmtCost(run.cost_usd) || "$0"}</Meta>
        <Meta label="duration">{fmtDuration(run.duration_s ?? now - run.created_at)}</Meta>
        <Meta label="ai first try">{run.ai_first_try ? `${run.ai_first_ok}/${run.ai_first_try}` : "—"}</Meta>
        <Meta label="repairs">{run.ai_repairs}</Meta>
        <Meta label="ai">{run.mock ? "mock" : "claude"}</Meta>
      </div>
      {run.error && <div className="mono border-b border-line px-6 py-2 text-[12px] text-fail">{run.error}</div>}
      <div className="grid gap-6 p-6 xl:grid-cols-[minmax(0,3fr)_minmax(260px,1fr)]">
        <Panel title="Cases" actions={<span className="mono text-[11px] text-faint">{failing ? `${failing} failing` : "all passing"}</span>}>
          {results.length ? (
            results.map((result) => <CaseRow key={result.case_id} result={result} hasBaseline={baseline !== null} />)
          ) : (
            <Empty>no case has finished yet</Empty>
          )}
        </Panel>
        <Panel title="Pass rate history">
          <div className="p-3">
            <Sparkline
              values={history.map((h) => h.pass_rate)}
              max={1}
              highlight={history.findIndex((h) => h.id === run.id)}
              width={240}
              height={56}
              color={toneColor(run.failed ? "fail" : "ok")}
            />
            <div className="mt-3 space-y-1">
              {[...history].reverse().map((h) => (
                <div key={h.id} className="flex items-center justify-between text-[12px]">
                  <Link to={`/evals/${h.id}`} className={`mono hover:underline ${h.id === run.id ? "text-text" : "text-accent"}`}>
                    {h.id}
                  </Link>
                  <EvalOutcome run={h} />
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
```

- [ ] **Step 9: Routes, nav, and the run page's eval link**

`ui/src/App.tsx` — import `Evals` and `EvalDetail`, and add after the `tasks` route:

```tsx
      { path: "evals", element: <Evals /> },
      { path: "evals/:evalId", element: <EvalDetail /> },
```

`ui/src/components/Shell.tsx` — `NAV` gains, after Tasks:

```tsx
  { to: "/evals", label: "Evals", end: false, badge: null },
```

`ui/src/pages/RunDetail.tsx` — in the meta strip after the `started` Meta:

```tsx
        {run.eval_run_id && (
          <Meta label="eval">
            <Link to={`/evals/${run.eval_run_id}`} className="text-accent hover:underline">
              {run.eval_run_id}
            </Link>
          </Meta>
        )}
```

- [ ] **Step 10: Type-check, test, build**

Run: `cd ui && npx tsc --noEmit && npm test && npm run build`
Expected: no type errors; 24 tests pass; `✓ built` into `../src/cerebellum/server/static`.

Run: `PYTHONPATH=src .venv/bin/pytest tests/server -q`
Expected: PASS (`test_packaged_ui.py` still finds the built index and assets).

- [ ] **Step 11: Checkpoint** — `git status --short`.

---

### Task 8: Docs, `make eval`, final verification and browser acceptance

**Files:**
- Modify: `README.md`, `Makefile`

**Interfaces:**
- Consumes: everything above.
- Produces: documentation; `make eval`.

- [ ] **Step 1: `Makefile`**

Add `eval` to `.PHONY` and, after `demo`:

```make
eval:
	$(BIN)/cerebellum eval src/cerebellum/templates/refund/evals.yaml --mock
```

- [ ] **Step 2: `README.md`**

1. After the "Dashboard" section, add:

````markdown
## Evals

An eval suite pins what a workflow must do, case by case. Each case is a real run (tagged with the
eval id, so its trace is one click away in the dashboard); approvals are decided by `eval`, the
sandbox payments API is switched to the case's fault mode, and the finished run is checked.

```yaml
suite: refund_regression
workflow: workflow.yaml            # relative to this file
defaults: {approval: approved, sandbox: never}
cases:
  - id: large_refund_rejected_by_a_human
    input: {order_id: A1008, amount: 640}
    approval: rejected
    expect:
      status: rejected
      output.decision: rejected
      steps.issue_refund.status: cancelled
  - id: flaky_payments_api_is_retried
    input: {order_id: A1009, amount: 89.9}
    sandbox: "first:2"
    expect: {status: succeeded, steps.issue_refund.attempts: 3}
    assert: ["run.cost_usd < 0.05"]
```

```bash
cerebellum eval workflows/refund/evals.yaml --mock        # exit 1 below --min-pass (default 0.9)
```

`expect` paths: `status`, `error`, `output.*`, `steps.<id>.{status,output.*,attempts,error}`,
`tasks.{count,open}`, `approvals.count`, `run.{id,cost_usd,duration_s}`; `assert` takes conditions
over the same values. Every eval run gets fresh sandbox databases (`.cerebellum/evals/<id>/`), so
results repeat; it is compared with the previous completed run of the same suite and newly failing
cases are flagged as regressions. Manual tasks opened by eval cases are closed by `eval`, and eval
runs stay out of the Overview metrics. The packaged suite (15 cases) is copied by `cerebellum init`.

## Draft a workflow from a description

```bash
cerebellum new "When an invoice over $10k arrives, check the vendor in PostgreSQL, ask finance to approve, then post it to the ERP API" -o workflows/invoice.yaml
```

Claude gets the workflow JSON Schema, a step guide and the refund example; its YAML goes through
the normal loader, and any issues are sent back for a repair (up to 3 attempts). The file starts
with a comment marking it as an AI draft: review it before running. `new` needs Anthropic
credentials; there is no offline fallback.
````

2. Dashboard bullets — add after **Tasks**:

```markdown
- **Evals** — every suite's pass-rate, cost and duration trends; per eval run, each case's failed expectations (expected vs actual), regression marks and a link to its run.
```

3. CLI table — `init` row becomes `Scaffold the refund example (workflow, seed, inputs, evals) and `.env.example``; add rows before `demo`:

```markdown
| `cerebellum eval <suite> [--mock] [--min-pass 0.9] [--no-sandbox]` | Run an eval suite and compare with the previous run |
| `cerebellum new "<description>" -o <file> [--force]` | Draft a workflow YAML with Claude (validated, repaired) |
```

4. Architecture block — add the lines:

```
 evals/     suites · runner (one run per case) · checks · baseline comparison
 authoring.py  cerebellum new: description → validated YAML draft
```

5. Development block — add `make eval        # the packaged eval suite with the mock AI`.

6. Delete the `## Roadmap` section (every roadmap item now exists).

- [ ] **Step 3: Full verification**

Run: `make test && git status --short`
Expected: ruff clean; pytest all pass (≈ 300+ tests, 1 skipped, live tests deselected); vitest 24 pass.

Run (from the repo root; `PYTHONPATH=src` works around the iCloud-hidden editable install):
`PYTHONPATH=src .venv/bin/python -c "import sys; from cerebellum.cli.app import main; sys.argv[0] = 'cerebellum'; main()" eval src/cerebellum/templates/refund/evals.yaml --mock`
Expected: 15 case lines with `●`, `15/15 passed (100%)`, exit 0.

- [ ] **Step 4: Browser acceptance (spec §11 phase 3)**

1. Start the dashboard: preview config `cerebellum-ui` (`.claude/launch.json`, port 7400).
2. Run the packaged suite again (Step 3 command) so it has a baseline, then break the threshold in a scratch copy: copy `src/cerebellum/templates/refund/` to the session scratchpad, change `approval_threshold: 500` to `1000` in the copy's `workflow.yaml`, and run `eval <copy>/evals.yaml --mock`.
   Expected: exit 1, `12/15 passed`, `3 regression(s): large_refund_approved_by_a_human, large_refund_rejected_by_a_human, amount_above_threshold_needs_approval`.
3. In the browser: `/evals` shows the `refund_regression` panel with three trend lines and the latest run at `12/15` with `↓ 3`; open it: the three cases show `✕`, `↓ regression` and `expected … · got …` lines; the history panel lists the runs; a case's run link opens the run page, whose meta strip links back to the eval.
4. Overview: the KPI strip and recent-runs list do not include the eval runs; Tasks: no open task from the outage case.
5. Console: no errors.

- [ ] **Step 5: Checkpoint** — `git status --short`; stop the preview server.
