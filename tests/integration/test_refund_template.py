import json
import sqlite3

import httpx
import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus
from cerebellum.sandbox.payments import FailMode, PaymentsState, create_payments_app
from cerebellum.spec import load_workflow
from cerebellum.templates import template_path

TEMPLATE = template_path("refund")


def load_input(name):
    return json.loads((TEMPLATE / "inputs" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def payments():
    return PaymentsState()


@pytest.fixture
def workflow():
    return load_workflow(TEMPLATE / "workflow.yaml", env={})


@pytest.fixture
def engine(store, settings, payments):
    transport = httpx.ASGITransport(app=create_payments_app(payments))
    return Engine(
        store,
        settings,
        MockProvider(latency=(0, 0)),
        http_transports={"payments": transport},
        jitter=0,
    )


def order_row(settings, order_id):
    with sqlite3.connect(settings.home / "sandbox_orders_db.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT refund_status, refund_id FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        return dict(row)


def statuses(store, run_id):
    return {step_id: record.status.value for step_id, record in store.get_steps(run_id).items()}


def test_template_path_rejects_unknown_names():
    with pytest.raises(FileNotFoundError):
        template_path("nope")


def test_template_is_valid(workflow):
    assert workflow.name == "refund_request"
    assert workflow.step_ids == [
        "fetch_order",
        "policy_check",
        "assess_request",
        "manager_approval",
        "issue_refund",
        "mark_refunded",
    ]
    assert workflow.fallback_ids == ["open_manual_case"]
    assert workflow.connectors["orders_db"].dsn == "sandbox"
    assert workflow.connectors["payments"].base_url == "http://127.0.0.1:8787"
    assert workflow.params == {"approval_threshold": 500}


async def test_small_refund_is_automatic(engine, workflow, store, settings, payments):
    run = await engine.start(workflow, load_input("small"))
    assert run.status is RunStatus.SUCCEEDED, run.error
    assert run.output["decision"] == "refunded"
    assert statuses(store, run.run_id)["manager_approval"] == "skipped"
    assert len(payments.refunds) == 1
    assert order_row(settings, "A1001") == {
        "refund_status": "refunded",
        "refund_id": run.output["refund_id"],
    }
    assert run.mock is True and run.cost_usd == 0.0


async def test_large_refund_needs_approval(engine, workflow, store):
    run = await engine.start(workflow, load_input("large"))
    assert run.status is RunStatus.WAITING_APPROVAL
    [approval] = store.list_approvals(run_id=run.run_id)
    assert approval.title == "Refund 899 for order A1002"
    assert approval.context["steps"]["assess_request"]["risk"] == "medium"
    assert approval.context["steps"]["fetch_order"]["id"] == "A1002"
    run = await engine.decide(run.run_id, approved=True, by="lead")
    assert run.status is RunStatus.SUCCEEDED
    assert run.output["decision"] == "refunded"


async def test_large_refund_rejected(engine, workflow, payments):
    run = await engine.start(workflow, load_input("large"))
    run = await engine.decide(run.run_id, approved=False, by="lead", comment="needs evidence")
    assert run.status is RunStatus.REJECTED
    assert run.output["decision"] == "rejected"
    assert payments.refunds == {}


async def test_flaky_payments_api_is_retried(engine, workflow, store, payments, clock):
    payments.set_fail_mode(FailMode.parse("first:2"))
    run = await engine.start(workflow, load_input("flaky"))
    assert run.status is RunStatus.SUCCEEDED
    assert store.get_step(run.run_id, "issue_refund").attempts == 3
    assert clock.sleeps == [0.5, 1.0]
    assert len(payments.refunds) == 1


async def test_payments_outage_opens_manual_case(engine, workflow, store, settings, payments):
    payments.set_fail_mode(FailMode.parse("always"))
    run = await engine.start(workflow, load_input("outage"))
    assert run.status is RunStatus.NEEDS_ATTENTION
    assert run.output["decision"] == "manual"
    s = statuses(store, run.run_id)
    assert s["issue_refund"] == "recovered"
    assert s["mark_refunded"] == "skipped"
    assert s["open_manual_case"] == "succeeded"
    [task] = store.list_tasks(run_id=run.run_id)
    assert task.assignee == "finance-ops"
    assert task.payload["failed_step"] == "issue_refund"
    assert task.payload["order_id"] == "A1005"
    assert order_row(settings, "A1005")["refund_status"] == "none"


async def test_suspicious_reason_is_denied_by_ai(engine, workflow, store, payments):
    run = await engine.start(workflow, load_input("fraud"))
    assert run.status is RunStatus.SUCCEEDED
    assert run.output["decision"] == "denied"
    s = statuses(store, run.run_id)
    assert s["manager_approval"] == "skipped" and s["issue_refund"] == "skipped"
    assert payments.refunds == {}


@pytest.mark.parametrize(
    ("order_id", "amount", "message"),
    [
        ("A1006", 100, "Only delivered orders can be refunded"),
        ("A1007", 50, "Order has already been refunded"),
        ("A1001", 500, "must be positive and not exceed"),
    ],
)
async def test_policy_violations_fail_the_run(engine, workflow, order_id, amount, message):
    run = await engine.start(workflow, {"order_id": order_id, "amount": amount, "reason": "x"})
    assert run.status is RunStatus.FAILED
    assert message in run.error


async def test_unknown_order_fails_fast(engine, workflow, store):
    run = await engine.start(workflow, {"order_id": "ZZZ", "amount": 10})
    assert run.status is RunStatus.FAILED
    assert store.get_step(run.run_id, "fetch_order").attempts == 1
    assert "expected exactly one row, got 0" in run.error


async def test_refund_without_a_reason_succeeds(engine, workflow, store):
    """`reason` is optional in the template's input; leaving it out must not break rendering."""
    run = await engine.start(workflow, {"order_id": "A1001", "amount": 120})
    assert run.status is RunStatus.SUCCEEDED, run.error
    assert run.output["decision"] == "refunded"
    [call] = [e for e in store.get_events(run.run_id) if e.type == "llm.call"]
    assert "Customer reason: (none given)" in call.data["prompt"]
