import asyncio
import logging

import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.errors import CerebellumError, LeaseUnavailable, NotFound, RunNotFound, SpecError
from cerebellum.runtime.engine import Engine
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


async def test_deciding_after_the_deadline_conflicts_and_the_run_follows_on_timeout(
    worker, store, clock, wf
):
    record = worker.start_run(wf, {"amount": 900})
    await worker.drain()
    [pending] = store.list_approvals(status="pending")
    clock.advance(3601)
    with pytest.raises(CerebellumError, match="expired") as info:
        await worker.decide(pending.id, approved=True, by="ui-user")
    assert not isinstance(info.value, NotFound)
    await worker.drain()
    assert store.get_approval(pending.id).decided_by == "system"
    assert store.get_run(record.run_id).status is RunStatus.REJECTED


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


async def test_a_claude_run_is_never_continued_on_mock_ai(worker, store, settings, clock, wf):
    """Review finding: a dashboard without Anthropic credentials (so on mock AI) silently drove
    runs started with the Claude API on the mock provider."""
    run = await Engine(store, settings, FakeClaude()).start(wf, {"amount": 900})
    assert run.status is RunStatus.WAITING_APPROVAL and run.mock is False
    [pending] = store.list_approvals(status="pending")
    with pytest.raises(CerebellumError, match="started with the Claude API") as info:
        await worker.decide(pending.id, approved=True, by="ui-user")
    assert not isinstance(info.value, NotFound)  # the API answers 409
    with pytest.raises(CerebellumError, match="started with the Claude API"):
        worker.resume(run.run_id)
    clock.advance(3601)
    assert worker.sweep() == []
    await worker.drain()
    assert store.get_approval(pending.id).status == "pending"
    assert store.get_run(run.run_id).status is RunStatus.WAITING_APPROVAL


async def test_the_sweep_says_once_why_a_claude_run_keeps_its_overdue_approval(
    worker, store, settings, clock, wf, caplog
):
    """Review finding: on mock AI the sweep skipped overdue approvals of Claude runs on every
    pass without a word, so their on_timeout silently never applied."""
    run = await Engine(store, settings, FakeClaude()).start(wf, {"amount": 900})
    with caplog.at_level(logging.INFO, logger="cerebellum.server.worker"):
        assert worker.sweep() == []  # not overdue yet: nothing to say
        clock.advance(3601)
        for _ in range(3):
            assert worker.sweep() == []
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert run.run_id in record.getMessage()
    assert "started with the Claude API" in record.getMessage()
    assert store.get_run(run.run_id).status is RunStatus.WAITING_APPROVAL


def test_mock_runs_use_a_mock_provider_even_on_a_claude_server(store, settings):
    claude = FakeClaude()
    worker = Worker(store, settings, claude)
    assert worker.engine(mock=False).ai is claude
    assert worker.engine(mock=True).ai.mock is True
