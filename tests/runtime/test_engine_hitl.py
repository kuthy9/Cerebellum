import asyncio

import httpx
import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.errors import CerebellumError, LeaseUnavailable
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus, StepStatus
from cerebellum.sandbox.payments import PaymentsState, create_payments_app
from cerebellum.spec import parse_workflow

APPROVAL_YAML = """
name: approval_flow
input:
  amount: {type: number, required: true}
params:
  threshold: 500
steps:
  - id: assess
    type: validate
    rules: [{expr: "input.amount > 0", message: positive}]
  - id: sign_off
    type: approval
    needs: [assess]
    when: "input.amount > params.threshold"
    title: "Approve {{ input.amount }}"
    show: [assess]
    timeout: 1h
    on_timeout: reject
  - {id: pay, type: task, needs: [sign_off], title: "Pay {{ input.amount }}"}
  - {id: audit, type: task, title: Audit log entry}
output:
  approved_by: "{{ steps.sign_off.output.by }}"
"""


def wf(text, tmp_path):
    return parse_workflow(text, base_dir=tmp_path, env={})


def engine_for(store, settings, transports=None):
    return Engine(
        store,
        settings,
        MockProvider(latency=(0, 0)),
        http_transports=transports or {},
        jitter=0,
    )


async def test_suspends_for_approval_and_releases_lease(store, settings, clock, tmp_path):
    run = await engine_for(store, settings).start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    assert run.status is RunStatus.WAITING_APPROVAL
    assert run.lease_owner is None
    steps = store.get_steps(run.run_id)
    assert steps["sign_off"].status is StepStatus.WAITING
    assert steps["pay"].status is StepStatus.PENDING
    assert steps["audit"].status is StepStatus.SUCCEEDED  # independent branch kept going
    [approval] = store.list_approvals(run_id=run.run_id)
    assert approval.title == "Approve 900"
    assert approval.context == {
        "input": {"amount": 900},
        "steps": {"assess": {"passed": True, "checked": 1}},
    }
    assert approval.expires_at == pytest.approx(clock.now() + 3600)
    suspended = [e for e in store.get_events(run.run_id) if e.type == "run.suspended"]
    assert suspended[0].data["waiting"] == ["sign_off"]


async def test_small_amount_skips_approval(store, settings, tmp_path):
    run = await engine_for(store, settings).start(wf(APPROVAL_YAML, tmp_path), {"amount": 100})
    assert run.status is RunStatus.SUCCEEDED
    assert store.get_step(run.run_id, "sign_off").status is StepStatus.SKIPPED
    assert run.output == {"approved_by": None}


async def test_approve_resumes_to_success(store, settings, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    run = await engine.decide(run.run_id, approved=True, by="alice", comment="fine")
    assert run.status is RunStatus.SUCCEEDED
    sign_off = store.get_step(run.run_id, "sign_off")
    assert sign_off.output["approved"] is True and sign_off.output["by"] == "alice"
    assert sign_off.output["auto"] is False
    assert run.output == {"approved_by": "alice"}
    assert "run.resumed" in [e.type for e in store.get_events(run.run_id)]


async def test_reject_marks_run_rejected_and_cancels_downstream(store, settings, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    run = await engine.decide(run.run_id, "sign_off", approved=False, by="bob", comment="too much")
    assert run.status is RunStatus.REJECTED
    assert store.get_step(run.run_id, "sign_off").error == "rejected by bob: too much"
    assert store.get_step(run.run_id, "pay").status is StepStatus.CANCELLED


async def test_approval_timeout_applies_on_timeout(store, settings, clock, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    assert await engine.expire_due_approvals() == []
    clock.advance(3601)
    assert await engine.expire_due_approvals() == [run.run_id]
    run = store.get_run(run.run_id)
    assert run.status is RunStatus.REJECTED
    [approval] = store.list_approvals(run_id=run.run_id)
    assert approval.decided_by == "system" and approval.status == "rejected"
    assert "approval.expired" in [e.type for e in store.get_events(run.run_id)]
    assert store.get_step(run.run_id, "sign_off").output["auto"] is True


async def test_expiry_decides_every_overdue_approval_and_resumes_each_run_once(
    store, settings, clock, tmp_path, monkeypatch
):
    text = """
name: two_timed_gates
steps:
  - {id: gate_a, type: approval, title: A, timeout: 1h, on_timeout: approve}
  - {id: gate_b, type: approval, title: B, timeout: 1h, on_timeout: approve}
"""
    engine = engine_for(store, settings)
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.WAITING_APPROVAL
    clock.advance(3601)
    resumed = []
    real_resume = engine.resume

    async def counting_resume(run_id):
        resumed.append(run_id)
        return await real_resume(run_id)

    monkeypatch.setattr(engine, "resume", counting_resume)
    assert await engine.expire_due_approvals() == [run.run_id]
    assert resumed == [run.run_id]
    assert store.get_run(run.run_id).status is RunStatus.SUCCEEDED
    approvals = store.list_approvals(run_id=run.run_id)
    assert [(a.status, a.decided_by) for a in approvals] == [("approved", "system")] * 2


async def test_resume_applies_expiry_by_itself(store, settings, clock, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    clock.advance(3601)
    assert (await engine.resume(run.run_id)).status is RunStatus.REJECTED


async def test_decision_during_execution_is_not_lost(store, settings, tmp_path):
    """Review focus: a human approves while another branch of the same run is still running."""
    text = """
name: concurrent_decision
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: gate, type: approval, title: Gate}
  - {id: slow, type: http, connector: api, path: /slow}
  - {id: after_gate, type: task, needs: [gate], title: after gate}
"""

    async def handler(request):
        for _ in range(200):
            pending = store.list_approvals(status="pending")
            if pending:
                break
            await asyncio.sleep(0.01)
        store.decide_approval(pending[0].id, approved=True, by="carol")
        return httpx.Response(200, json={})

    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.SUCCEEDED
    assert store.get_step(run.run_id, "after_gate").status is StepStatus.SUCCEEDED
    assert "run.suspended" not in [e.type for e in store.get_events(run.run_id)]


async def test_resume_is_refused_while_another_process_holds_the_lease(store, settings, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    assert store.acquire_lease(run.run_id, "someone-else", 60)
    with pytest.raises(LeaseUnavailable):
        await engine.resume(run.run_id)


async def test_decide_while_another_process_owns_the_run_records_the_decision(
    store, settings, tmp_path
):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    store.acquire_lease(run.run_id, "someone-else", 60)
    record = await engine.decide(run.run_id, approved=True, by="dana")
    assert record.status is RunStatus.WAITING_APPROVAL
    assert store.list_approvals(run_id=run.run_id)[0].status == "approved"


CRASH_YAML = """
name: crashy
input: {order_id: {type: string, required: true}}
connectors:
  payments: {type: rest, base_url: "http://payments.test"}
steps:
  - id: prepare
    type: validate
    rules: [{expr: "input.order_id", message: need an order}]
  - id: refund
    type: http
    needs: [prepare]
    connector: payments
    method: POST
    path: /refunds
    body: {order_id: "{{ input.order_id }}", amount: 10}
"""


async def test_crash_mid_refund_resumes_without_double_refund(store, settings, tmp_path):
    state = PaymentsState()
    transport = httpx.ASGITransport(app=create_payments_app(state))
    workflow = wf(CRASH_YAML, tmp_path)
    run_id = "r_crash001"
    # A process that died right after the payments API accepted the refund:
    store.save_workflow(workflow)
    store.create_run(run_id, workflow, {"order_id": "A9"}, {}, mock=True)
    store.step_transition(run_id, "prepare", StepStatus.RUNNING, event="started", attempts=1)
    store.step_transition(
        run_id, "prepare", StepStatus.SUCCEEDED, event="succeeded", output={"passed": True}
    )
    store.step_transition(run_id, "refund", StepStatus.RUNNING, event="started", attempts=1)
    original = state.create(f"{run_id}:refund", "A9", 10.0, "USD", None)
    assert store.is_stale(store.get_run(run_id))

    run = await engine_for(store, settings, {"payments": transport}).resume(run_id)

    assert run.status is RunStatus.SUCCEEDED
    refund = store.get_step(run_id, "refund")
    assert refund.attempts == 2
    assert refund.output["status"] == 200  # replayed by the idempotency key, not created again
    assert refund.output["body"]["id"] == original["id"]
    assert len(state.refunds) == 1
    resets = [e for e in store.get_events(run_id) if e.type == "step.reset"]
    assert resets[0].step_id == "refund" and resets[0].data["reason"] == "interrupted"


async def test_failed_run_can_be_resumed_after_the_dependency_recovers(store, settings, tmp_path):
    text = """
name: flaky_dependency
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: call, type: http, connector: api, path: /x}
  - {id: after, type: task, needs: [call], title: after}
"""
    healthy = {"value": False}

    def handler(request):
        return httpx.Response(201 if healthy["value"] else 503, json={})

    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.FAILED
    assert store.get_step(run.run_id, "after").status is StepStatus.CANCELLED
    healthy["value"] = True
    run = await engine.resume(run.run_id)
    assert run.status is RunStatus.SUCCEEDED
    assert store.get_step(run.run_id, "call").attempts == 2
    assert run.error is None


async def test_finished_runs_cannot_be_resumed(store, settings, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 100})
    with pytest.raises(CerebellumError, match="succeeded and cannot be resumed"):
        await engine.resume(run.run_id)


async def test_decide_requires_exactly_one_pending_approval(store, settings, tmp_path):
    engine = engine_for(store, settings)
    done = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 100})
    with pytest.raises(CerebellumError, match="no pending approval"):
        await engine.decide(done.run_id, approved=True, by="x")
    text = """
name: two_gates
steps:
  - {id: gate_a, type: approval, title: A}
  - {id: gate_b, type: approval, title: B}
"""
    run = await engine.start(wf(text, tmp_path))
    with pytest.raises(CerebellumError, match="several pending approvals"):
        await engine.decide(run.run_id, approved=True, by="x")
    await engine.decide(run.run_id, "gate_a", approved=True, by="x")
    run = await engine.decide(run.run_id, "gate_b", approved=True, by="x")
    assert run.status is RunStatus.SUCCEEDED
