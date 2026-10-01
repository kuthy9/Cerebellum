import shutil

import httpx
import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.errors import CerebellumError
from cerebellum.evals import EvalRunner, eval_home, load_suite
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus
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
    assert (status["expected"], status["actual"], status["passed"]) == (
        "rejected",
        "succeeded",
        False,
    )
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
    assert small.passed and record.status == "completed", store.get_run(small.run_id).error


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


async def test_eval_runs_are_records_that_cannot_be_resumed_or_decided(
    make_runner, store, settings, payments, clock
):
    """Review finding: resuming an eval case run re-ran it against the main home's sandbox."""
    record = await make_runner().run(load_suite(SUITE, env={}))
    failed = next(
        r.run_id
        for r in store.get_eval_results(record.id)
        if r.case_id == "unknown_order_fails_fast"
    )
    transports = {"payments": httpx.ASGITransport(app=create_payments_app(payments))}
    engine = Engine(store, settings, MockProvider(latency=(0, 0)), http_transports=transports)
    with pytest.raises(CerebellumError, match="eval"):
        await engine.resume(failed)
    assert not (settings.home / "sandbox_orders_db.db").exists()

    # A case run left waiting (its eval was interrupted) is not decided or swept by others.
    driver = Engine(
        store,
        settings,
        MockProvider(latency=(0, 0)),
        http_transports=transports,
        eval_runs=True,
    )
    waiting = await driver.start(
        load_suite(SUITE, env={}).workflow,
        {"order_id": "A1002", "amount": 899},
        eval_run_id=record.id,
    )
    assert waiting.status is RunStatus.WAITING_APPROVAL
    with pytest.raises(CerebellumError, match="eval"):
        await engine.decide(waiting.run_id, approved=True, by="someone")
    clock.advance(25 * 3600)
    assert await engine.expire_due_approvals() == []
    [approval] = store.list_approvals(run_id=waiting.run_id)
    assert approval.status == "pending"
