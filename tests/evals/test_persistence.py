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


async def test_workflow_history_leaves_eval_runs_out(store, settings, simple_workflow):
    """Review finding: one eval made the Workflows page count 15 runs and list eval-only copies."""
    engine = Engine(store, settings, MockProvider(latency=(0, 0)))
    make_eval(store)
    await engine.start(simple_workflow, {"order_id": "A1"}, eval_run_id="ev_00000001")
    assert store.list_workflows() == []
    await engine.start(simple_workflow, {"order_id": "A2"})
    [snapshot] = store.list_workflows()
    assert snapshot.runs == 1
