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
        {
            "output.decision": "manual",
            "output.ok": True,
            "output.refund_id": None,
            "tasks.count": 0,
        },
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
