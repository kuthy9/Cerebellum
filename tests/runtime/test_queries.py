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
