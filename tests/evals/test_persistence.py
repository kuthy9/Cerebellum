import sqlite3

import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.errors import NotFound
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.store import Store

# The eval_runs table as databases created before the heartbeat column have it.
EVAL_RUNS_BEFORE_HEARTBEAT = """
CREATE TABLE eval_runs (
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
INSERT INTO eval_runs(id, suite, suite_path, workflow_name, workflow_digest, status, mock, total,
                      created_at)
VALUES ('ev_00000001', 's', '/x/evals.yaml', 'wf', 'd1', 'running', 1, 2, 1790000000.0);
"""

CHECK = {
    "kind": "expect",
    "target": "status",
    "passed": True,
    "expected": "succeeded",
    "actual": "succeeded",
    "missing": False,
}


def make_eval(store, eval_run_id="ev_00000001", *, suite="s", baseline_id=None, total=2, mock=True):
    return store.create_eval_run(
        eval_run_id,
        suite=suite,
        suite_path="/x/evals.yaml",
        workflow_name="wf",
        workflow_digest="d1",
        mock=mock,
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
    assert store.latest_eval_run("s", mock=True) is None
    make_eval(store, "ev_00000001")
    store.finish_eval_run("ev_00000001", status="completed")
    clock.advance(1)
    make_eval(store, "ev_00000002")
    store.finish_eval_run("ev_00000002", status="errored", error="interrupted")
    clock.advance(1)
    make_eval(store, "ev_00000003", suite="other")
    assert store.latest_eval_run("s", mock=True).id == "ev_00000001"
    assert [r.id for r in store.list_eval_runs()] == ["ev_00000003", "ev_00000002", "ev_00000001"]
    assert [r.id for r in store.list_eval_runs(suite="s")] == ["ev_00000002", "ev_00000001"]
    assert store.get_eval_run("ev_00000002").error == "interrupted"


def test_baseline_is_the_latest_completed_run_with_the_same_ai_mode(store, clock):
    """Review finding: a Claude eval was compared with the latest mock eval (and vice versa)."""
    make_eval(store, "ev_00000001", mock=True)
    store.finish_eval_run("ev_00000001", status="completed")
    clock.advance(1)
    make_eval(store, "ev_00000002", mock=False)
    store.finish_eval_run("ev_00000002", status="completed")
    assert store.latest_eval_run("s", mock=True).id == "ev_00000001"
    assert store.latest_eval_run("s", mock=False).id == "ev_00000002"
    assert store.latest_eval_run("other", mock=False) is None


def test_newest_eval_runs_of_each_suite(store, clock):
    for index in range(3):
        clock.advance(1)
        make_eval(store, f"ev_a000000{index}", suite="a")
    for index in range(5):
        clock.advance(1)
        make_eval(store, f"ev_b000000{index}", suite="b")
    assert [r.id for r in store.list_eval_runs_per_suite(2)] == [
        "ev_b0000004",
        "ev_b0000003",
        "ev_a0000002",
        "ev_a0000001",
    ]
    assert len(store.list_eval_runs_per_suite(10)) == 8


def test_running_eval_without_a_recent_heartbeat_is_stale(store, clock):
    """Review finding: a killed eval stayed "running" forever, with no liveness signal."""
    record = make_eval(store)
    assert record.heartbeat_at == clock.now()
    assert not store.is_eval_stale(record, timeout=30)
    clock.advance(31)
    assert store.is_eval_stale(store.get_eval_run(record.id), timeout=30)
    store.eval_heartbeat(record.id)
    assert store.get_eval_run(record.id).heartbeat_at == clock.now()
    assert not store.is_eval_stale(store.get_eval_run(record.id), timeout=30)
    done = store.finish_eval_run(record.id, status="completed")
    clock.advance(100)
    assert not store.is_eval_stale(done, timeout=30)


def test_stale_running_evals_of_a_suite_are_marked_abandoned(store, clock):
    killed = make_eval(store, "ev_00000001")
    make_eval(store, "ev_00000002", suite="other")
    clock.advance(60)
    make_eval(store, "ev_00000003")  # alive: its heartbeat is recent
    assert store.abandon_stale_eval_runs("s", timeout=30) == ["ev_00000001"]
    abandoned = store.get_eval_run("ev_00000001")
    assert abandoned.status == "errored" and abandoned.error.startswith("abandoned")
    assert abandoned.ended_at == killed.heartbeat_at  # when it was last known to be alive
    assert store.get_eval_run("ev_00000002").status == "running"
    assert store.get_eval_run("ev_00000003").status == "running"
    assert store.abandon_stale_eval_runs("s", timeout=30) == []


def test_databases_from_before_the_heartbeat_column_are_migrated(tmp_path, clock):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(EVAL_RUNS_BEFORE_HEARTBEAT)
    conn.close()
    with Store(path, clock=clock) as store:
        record = store.get_eval_run("ev_00000001")
        assert record.heartbeat_at is None and record.status == "running"
        clock.advance(31)
        assert store.is_eval_stale(record, timeout=30)  # no heartbeat: judged by its start
        store.eval_heartbeat("ev_00000001")
        assert store.get_eval_run("ev_00000001").heartbeat_at == clock.now()
    with Store(path, clock=clock) as store:  # opening a migrated database again is a no-op
        assert store.get_eval_run("ev_00000001").heartbeat_at == clock.now()


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


async def test_workflow_history_leaves_eval_runs_out(store, settings, simple_workflow):
    """Review finding: one eval made the Workflows page count 15 runs and list eval-only copies."""
    engine = Engine(store, settings, MockProvider(latency=(0, 0)))
    make_eval(store)
    await engine.start(simple_workflow, {"order_id": "A1"}, eval_run_id="ev_00000001")
    assert store.list_workflows() == []
    await engine.start(simple_workflow, {"order_id": "A2"})
    [snapshot] = store.list_workflows()
    assert snapshot.runs == 1
