"""Final-review regressions: fallbacks racing parallel branches, crashes during a fallback, and a
human decision landing while the run suspends."""

import asyncio

import httpx
import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus, StepStatus
from cerebellum.spec import parse_workflow

S = StepStatus


def wf(text, tmp_path):
    return parse_workflow(text, base_dir=tmp_path, env={})


def engine_for(store, settings, transports=None):
    return Engine(
        store, settings, MockProvider(latency=(0, 0)), http_transports=transports or {}, jitter=0
    )


async def wait_for_step(store, step_id, status):
    for _ in range(500):
        runs = store.list_runs()
        if runs and store.get_step(runs[0].run_id, step_id).status is status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{step_id} never became {status.value}")


PARALLEL_FALLBACK_YAML = """
name: fallback_with_parallel_branch
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: call, type: http, connector: api, path: /call, on_failure: {fallback: rescue}}
  - {id: other, type: http, connector: api, path: /other}
  - {id: after, type: task, needs: [call], title: after}
fallbacks:
  - {id: rescue, type: http, connector: api, path: /rescue}
"""


async def test_parallel_branch_finishing_during_a_fallback_does_not_cancel_downstream(
    store, settings, tmp_path
):
    async def handler(request):
        if request.url.path == "/call":
            return httpx.Response(400, json={"error": "rejected"})  # not retryable → fallback
        if request.url.path == "/other":
            await wait_for_step(store, "call", S.FAILED)  # finish while the fallback runs
            return httpx.Response(200, json={})
        await wait_for_step(store, "other", S.SUCCEEDED)
        await asyncio.sleep(0.05)  # let the scheduler react to the finished branch
        return httpx.Response(200, json={"rescued": True})

    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(PARALLEL_FALLBACK_YAML, tmp_path))
    steps = store.get_steps(run.run_id)
    assert {sid: rec.status for sid, rec in steps.items()} == {
        "call": S.RECOVERED,
        "other": S.SUCCEEDED,
        "after": S.SUCCEEDED,
        "rescue": S.SUCCEEDED,
    }
    assert run.status is RunStatus.NEEDS_ATTENTION


CRASH_FALLBACK_YAML = """
name: crash_in_fallback
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: call, type: http, connector: api, path: /call, on_failure: {fallback: rescue}}
  - {id: after, type: task, needs: [call], title: after}
fallbacks:
  - {id: rescue, type: task, title: "Handle {{ failure.step }} ({{ failure.kind }})"}
"""


def crashed_while_falling_back(store, workflow, run_id, *, rescue_succeeded):
    """A process died after `call` failed and its fallback had started (or even finished)."""
    store.save_workflow(workflow)
    store.create_run(run_id, workflow, {}, {}, mock=True)
    store.step_transition(run_id, "call", S.RUNNING, event="started", attempts=1)
    store.step_transition(
        run_id,
        "call",
        S.FAILED,
        event="failed",
        error="HTTP 400",
        data={"kind": "http", "retryable": False},
    )
    store.step_transition(
        run_id, "rescue", S.RUNNING, event="started", attempts=1, data={"fallback_for": "call"}
    )
    if rescue_succeeded:
        store.step_transition(
            run_id, "rescue", S.SUCCEEDED, event="succeeded", output={"task_id": "tk_done"}
        )
    assert store.is_stale(store.get_run(run_id))


def counting_transport():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(400, json={})

    return calls, httpx.MockTransport(handler)


async def test_resume_finishes_a_fallback_interrupted_by_a_crash(store, settings, tmp_path):
    workflow = wf(CRASH_FALLBACK_YAML, tmp_path)
    crashed_while_falling_back(store, workflow, "r_fb000001", rescue_succeeded=False)
    calls, transport = counting_transport()

    run = await engine_for(store, settings, {"api": transport}).resume("r_fb000001")

    assert run.status is RunStatus.NEEDS_ATTENTION
    steps = store.get_steps("r_fb000001")
    assert steps["call"].status is S.RECOVERED and steps["after"].status is S.SUCCEEDED
    assert steps["rescue"].attempts == 2
    assert calls == []  # the failed step is not re-run; only its fallback is
    titles = [t.title for t in store.list_tasks(run_id="r_fb000001")]
    assert "Handle call (http)" in titles


async def test_resume_completes_recovery_when_the_fallback_had_already_succeeded(
    store, settings, tmp_path
):
    workflow = wf(CRASH_FALLBACK_YAML, tmp_path)
    crashed_while_falling_back(store, workflow, "r_fb000002", rescue_succeeded=True)
    calls, transport = counting_transport()

    run = await engine_for(store, settings, {"api": transport}).resume("r_fb000002")

    assert run.status is RunStatus.NEEDS_ATTENTION
    steps = store.get_steps("r_fb000002")
    assert steps["call"].status is S.RECOVERED
    assert steps["call"].output == {"task_id": "tk_done"}
    assert steps["rescue"].attempts == 1 and steps["after"].status is S.SUCCEEDED
    assert calls == []


APPROVAL_YAML = """
name: approval_flow
input:
  amount: {type: number, required: true}
steps:
  - {id: sign_off, type: approval, when: "input.amount > 500", title: "Approve {{ input.amount }}"}
  - {id: pay, type: task, needs: [sign_off], title: "Pay {{ input.amount }}"}
output:
  approved_by: "{{ steps.sign_off.output.by }}"
"""


async def test_decision_landing_while_the_run_suspends_is_applied(
    store, settings, tmp_path, monkeypatch
):
    """Another process decides after the engine's last look but before its suspend commits."""
    real_suspend = store.suspend_run

    def decide_then_suspend(run_id, owner, waiting):
        [approval] = store.list_approvals(run_id=run_id, status="pending")
        store.decide_approval(approval.id, approved=True, by="erin")
        return real_suspend(run_id, owner, waiting)

    monkeypatch.setattr(store, "suspend_run", decide_then_suspend)
    run = await engine_for(store, settings).start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    assert run.status is RunStatus.SUCCEEDED
    assert run.output == {"approved_by": "erin"}


async def test_suspend_run_is_atomic_with_the_approval_check_and_the_lease(
    store, settings, tmp_path
):
    run = await engine_for(store, settings).start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    store.set_run_status(run.run_id, RunStatus.RUNNING, event="resumed")
    assert store.acquire_lease(run.run_id, "owner-a", 60)

    assert store.suspend_run(run.run_id, "owner-a", ["sign_off"]) is True
    suspended = store.get_run(run.run_id)
    assert suspended.status is RunStatus.WAITING_APPROVAL and suspended.lease_owner is None

    store.set_run_status(run.run_id, RunStatus.RUNNING, event="resumed")
    assert store.acquire_lease(run.run_id, "owner-a", 60)
    [approval] = store.list_approvals(run_id=run.run_id)
    store.decide_approval(approval.id, approved=True, by="frank")
    events_before = len(store.get_events(run.run_id))
    assert store.suspend_run(run.run_id, "owner-a", ["sign_off"]) is False
    unchanged = store.get_run(run.run_id)
    assert unchanged.status is RunStatus.RUNNING and unchanged.lease_owner == "owner-a"
    assert len(store.get_events(run.run_id)) == events_before


SLOW_BRANCH_YAML = """
name: slow_branch
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: slow, type: http, connector: api, path: /slow}
  - {id: ready, type: http, connector: api, path: /ready}
  - {id: gate, type: approval, needs: [ready], title: Gate}
"""


def slow_api():
    """`/slow` blocks for `delay[0]` seconds; `/ready` answers once `/slow` is in flight."""
    entered = asyncio.Event()
    delay = [20.0]

    async def handler(request):
        if request.url.path == "/ready":
            await entered.wait()
        else:
            entered.set()
            await asyncio.sleep(delay[0])
        return httpx.Response(200, json={})

    return entered, delay, httpx.MockTransport(handler)


def step_tasks():
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("step:") and not t.done()]


async def test_unexpected_engine_error_cancels_running_steps_and_fails_the_run(
    store, settings, tmp_path, monkeypatch
):
    _, delay, transport = slow_api()

    def broken_request_approval(*args, **kwargs):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(store, "request_approval", broken_request_approval)
    engine = engine_for(store, settings, {"api": transport})
    with pytest.raises(RuntimeError, match="store exploded"):
        await engine.start(wf(SLOW_BRANCH_YAML, tmp_path))

    assert step_tasks() == []  # the sibling branch did not outlive the drive
    [run] = store.list_runs()
    assert run.status is RunStatus.FAILED and run.lease_owner is None
    assert run.error == "engine error: RuntimeError: store exploded"
    failed = [e for e in store.get_events(run.run_id) if e.type == "run.failed"]
    assert failed[0].data["error"] == "engine error: RuntimeError: store exploded"
    steps = store.get_steps(run.run_id)
    assert steps["slow"].status is S.CANCELLED and steps["gate"].status is S.CANCELLED

    monkeypatch.undo()
    delay[0] = 0
    run = await engine.resume(run.run_id)  # resumable once the cause is gone
    assert run.status is RunStatus.WAITING_APPROVAL
    assert store.get_step(run.run_id, "slow").status is S.SUCCEEDED


async def test_cancelling_a_drive_stops_its_steps_and_leaves_the_run_resumable(
    store, settings, tmp_path
):
    entered, delay, transport = slow_api()
    engine = engine_for(store, settings, {"api": transport})
    drive = asyncio.create_task(engine.start(wf(SLOW_BRANCH_YAML, tmp_path)))
    await entered.wait()
    drive.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drive

    assert step_tasks() == []
    [run] = store.list_runs()
    assert run.status is RunStatus.RUNNING and store.is_stale(run)  # like a stopped process
    assert "run.failed" not in [e.type for e in store.get_events(run.run_id)]

    delay[0] = 0
    run = await engine.resume(run.run_id)
    assert run.status is RunStatus.WAITING_APPROVAL
    assert store.get_step(run.run_id, "slow").status is S.SUCCEEDED
