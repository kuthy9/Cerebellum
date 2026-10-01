import threading
from datetime import date
from decimal import Decimal

import pytest

from cerebellum.errors import CerebellumError, InvalidTransition, RunNotFound
from cerebellum.runtime.states import RunStatus, StepStatus
from cerebellum.runtime.store import Store


def start(store, wf, run_id="r_test0001"):
    store.save_workflow(wf)
    return store.create_run(run_id, wf, {"order_id": "A1"}, {"k": 1}, mock=True)


def test_create_run_initialises_projection_and_event(store, simple_workflow):
    run = start(store, simple_workflow)
    assert run.status is RunStatus.RUNNING
    assert run.input == {"order_id": "A1"} and run.params == {"k": 1} and run.mock is True
    steps = store.get_steps(run.run_id)
    assert list(steps) == ["first", "second", "rescue"]
    assert all(step.status is StepStatus.PENDING for step in steps.values())
    events = store.get_events(run.run_id)
    assert [e.type for e in events] == ["run.started"]
    assert events[0].data["workflow"] == "simple"


def test_get_run_unknown_raises(store):
    with pytest.raises(RunNotFound):
        store.get_run("r_missing")


def test_step_transition_updates_projection_and_appends_event(store, simple_workflow, clock):
    run = start(store, simple_workflow)
    store.step_transition(
        run.run_id,
        "first",
        StepStatus.RUNNING,
        event="started",
        span_id="first#1",
        attempts=1,
        started_at=clock.now(),
    )
    clock.advance(2)
    record = store.step_transition(
        run.run_id,
        "first",
        StepStatus.SUCCEEDED,
        event="succeeded",
        span_id="first#1",
        output={"ok": True},
        ended_at=clock.now(),
    )
    assert record.status is StepStatus.SUCCEEDED
    assert record.output == {"ok": True}
    assert record.attempts == 1
    assert record.duration == 2
    events = store.get_events(run.run_id)
    assert [e.type for e in events] == ["run.started", "step.started", "step.succeeded"]
    assert events[-1].span_id == "first#1"
    assert events[-1].data == {"status": "succeeded", "output": {"ok": True}}


def test_invalid_step_transition_raises_and_writes_nothing(store, simple_workflow):
    run = start(store, simple_workflow)
    with pytest.raises(InvalidTransition):
        store.step_transition(run.run_id, "first", StepStatus.SUCCEEDED, event="succeeded")
    assert [e.type for e in store.get_events(run.run_id)] == ["run.started"]
    assert store.get_step(run.run_id, "first").status is StepStatus.PENDING


def test_unknown_step_raises(store, simple_workflow):
    run = start(store, simple_workflow)
    with pytest.raises(CerebellumError, match="unknown step"):
        store.step_transition(run.run_id, "ghost", StepStatus.RUNNING, event="started")


def test_record_call_accumulates_cost(store, simple_workflow):
    run = start(store, simple_workflow)
    for cost in (0.25, 0.5):
        store.record_call(
            run.run_id,
            "first",
            "llm",
            span_id=f"c{cost}",
            parent_span_id="first#1",
            data={"model": "m"},
            cost_usd=cost,
        )
    assert store.get_run(run.run_id).cost_usd == pytest.approx(0.75)
    assert store.get_step(run.run_id, "first").cost_usd == pytest.approx(0.75)
    event = store.get_events(run.run_id)[-1]
    assert event.type == "llm.call"
    assert event.parent_span_id == "first#1"
    assert event.data == {"model": "m", "cost_usd": 0.5}


def test_run_status_transitions(store, simple_workflow):
    run = start(store, simple_workflow)
    waiting = store.set_run_status(run.run_id, RunStatus.WAITING_APPROVAL, event="suspended")
    assert waiting.ended_at is None
    store.set_run_status(run.run_id, RunStatus.RUNNING, event="resumed")
    done = store.set_run_status(
        run.run_id, RunStatus.SUCCEEDED, event="completed", output={"decision": "ok"}
    )
    assert done.output == {"decision": "ok"}
    assert done.ended_at is not None
    with pytest.raises(InvalidTransition):
        store.set_run_status(run.run_id, RunStatus.RUNNING, event="resumed")
    types = [e.type for e in store.get_events(run.run_id)]
    assert types[-3:] == ["run.suspended", "run.resumed", "run.completed"]


def test_list_runs_filters(store, simple_workflow):
    start(store, simple_workflow, "r_a")
    start(store, simple_workflow, "r_b")
    store.set_run_status("r_b", RunStatus.FAILED, event="completed", error="boom")
    assert {r.run_id for r in store.list_runs()} == {"r_a", "r_b"}
    assert [r.run_id for r in store.list_runs(status=RunStatus.FAILED)] == ["r_b"]
    assert store.get_run("r_b").error == "boom"


def test_approval_lifecycle(store, simple_workflow, clock):
    run = start(store, simple_workflow)
    store.step_transition(run.run_id, "second", StepStatus.RUNNING, event="started", attempts=1)
    approval = store.request_approval(
        run.run_id,
        "second",
        title="Approve?",
        context={"x": 1},
        expires_at=clock.now() + 60,
        on_timeout="reject",
        span_id="second#1",
    )
    assert approval.status == "pending"
    assert approval.context == {"x": 1}
    assert approval.id.startswith("ap_")
    assert store.get_step(run.run_id, "second").status is StepStatus.WAITING
    assert [a.id for a in store.list_approvals(status="pending")] == [approval.id]

    decided = store.decide_approval(approval.id, approved=True, by="alice", comment="ok")
    assert decided.status == "approved" and decided.decided_by == "alice"
    with pytest.raises(CerebellumError, match="already approved"):
        store.decide_approval(approval.id, approved=False, by="bob")
    assert store.get_approval_for_step(run.run_id, "second").id == approval.id
    assert store.get_approval_for_step(run.run_id, "first") is None
    types = [e.type for e in store.get_events(run.run_id)]
    assert "approval.requested" in types and "approval.decided" in types


def test_expired_decision_uses_its_own_event(store, simple_workflow):
    run = start(store, simple_workflow)
    store.step_transition(run.run_id, "second", StepStatus.RUNNING, event="started", attempts=1)
    approval = store.request_approval(
        run.run_id, "second", title="t", context={}, expires_at=None, on_timeout="reject"
    )
    store.decide_approval(approval.id, approved=False, by="system", expired=True)
    assert store.get_events(run.run_id)[-1].type == "approval.expired"


def test_tasks_lifecycle(store, simple_workflow):
    run = start(store, simple_workflow)
    task = store.create_task(run.run_id, "rescue", title="Fix it", assignee="ops", payload={"a": 1})
    assert task.status == "open" and task.payload == {"a": 1} and task.id.startswith("tk_")
    resolved = store.resolve_task(task.id, by="ops", note="done")
    assert resolved.status == "resolved" and resolved.resolved_by == "ops"
    assert store.list_tasks(status="open") == []
    assert [t.id for t in store.list_tasks(run_id=run.run_id)] == [task.id]
    with pytest.raises(CerebellumError, match="already resolved"):
        store.resolve_task(task.id, by="ops")


def test_leases_are_exclusive_until_expiry(store, simple_workflow, clock):
    run = start(store, simple_workflow)
    assert store.acquire_lease(run.run_id, "a", 30)
    assert not store.acquire_lease(run.run_id, "b", 30)
    assert store.acquire_lease(run.run_id, "a", 30)
    assert not store.is_stale(store.get_run(run.run_id))
    clock.advance(31)
    assert store.is_stale(store.get_run(run.run_id))
    assert store.acquire_lease(run.run_id, "b", 30)
    assert not store.renew_lease(run.run_id, "a", 30)
    assert store.renew_lease(run.run_id, "b", 30)
    store.release_lease(run.run_id, "b")
    assert store.get_run(run.run_id).lease_owner is None


def test_acquire_lease_on_unknown_run_raises(store):
    with pytest.raises(RunNotFound):
        store.acquire_lease("r_nope", "a", 30)


def test_listeners_receive_committed_events(store, simple_workflow):
    seen = []
    unsubscribe = store.add_listener(seen.append)
    run = start(store, simple_workflow)
    unsubscribe()
    store.record_call(run.run_id, "first", "llm", span_id="s", parent_span_id=None, data={})
    assert [e.type for e in seen] == ["run.started"]


def test_events_since_returns_global_tail(store, simple_workflow):
    run = start(store, simple_workflow)
    first_seq = store.get_events(run.run_id)[0].seq
    store.record_call(run.run_id, "first", "llm", span_id="s", parent_span_id=None, data={})
    assert [e.type for e in store.events_since(first_seq)] == ["llm.call"]
    assert [e.type for e in store.get_events(run.run_id, after_seq=first_seq)] == ["llm.call"]


def test_workflow_snapshot_round_trip(store, simple_workflow):
    store.save_workflow(simple_workflow)
    store.save_workflow(simple_workflow)  # idempotent
    source, base_dir = store.get_workflow_source(simple_workflow.digest)
    assert source == simple_workflow.source_yaml
    assert base_dir == simple_workflow.base_dir
    with pytest.raises(CerebellumError, match="snapshot"):
        store.get_workflow_source("missing")


def test_values_are_json_safe(store, simple_workflow):
    run = start(store, simple_workflow)
    store.record_call(
        run.run_id,
        "first",
        "connector",
        span_id="s",
        parent_span_id=None,
        data={"amount": Decimal("12.50"), "day": date(2026, 10, 1)},
    )
    assert store.get_events(run.run_id)[-1].data["amount"] == 12.5
    assert store.get_events(run.run_id)[-1].data["day"] == "2026-10-01"


def test_concurrent_writers_on_one_database(settings, simple_workflow):
    """Review focus: the CLI and the UI server write to the same file through different
    connections at the same time."""
    a = Store(settings.db_path)
    b = Store(settings.db_path)
    try:
        a.save_workflow(simple_workflow)
        run = a.create_run("r_shared01", simple_workflow, {}, {}, mock=True)

        def write(target):
            for i in range(50):
                target.record_call(
                    run.run_id,
                    "first",
                    "connector",
                    span_id=f"s{i}",
                    parent_span_id=None,
                    data={"i": i},
                    cost_usd=0.001,
                )

        threads = [threading.Thread(target=write, args=(s,)) for s in (a, b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(a.get_events(run.run_id)) == 101
        assert b.get_run(run.run_id).cost_usd == pytest.approx(0.1)
    finally:
        a.close()
        b.close()
