import pytest

from cerebellum.errors import SpecError
from cerebellum.evals import load_suite
from cerebellum.evals.suite import path_problem
from cerebellum.templates import template_path

SUITE = template_path("refund") / "evals.yaml"
WORKFLOW = template_path("refund") / "workflow.yaml"


def write_suite(tmp_path, body):
    path = tmp_path / "evals.yaml"
    path.write_text(body.replace("WORKFLOW", str(WORKFLOW)), encoding="utf-8")
    return path


def issue_map(exc):
    return {issue.path: issue.message for issue in exc.value.issues}


def test_packaged_suite_loads_with_its_workflow():
    loaded = load_suite(SUITE, env={})
    assert loaded.suite.suite == "refund_regression"
    assert loaded.workflow.name == "refund_request"
    assert len(loaded.suite.cases) == 15
    cases = {case.id: case for case in loaded.suite.cases}
    assert loaded.decision(cases["large_refund_rejected_by_a_human"]) == "rejected"
    assert loaded.decision(cases["small_refund_is_automatic"]) == "approved"
    assert loaded.fail_mode(cases["flaky_payments_api_is_retried"]) == "first:2"
    assert loaded.fail_mode(cases["small_refund_is_automatic"]) == "never"
    assert cases["undelivered_order_is_refused"].asserts
    assert loaded.path == SUITE.resolve()


def test_suite_reports_every_issue_with_its_path(tmp_path):
    path = write_suite(
        tmp_path,
        """
suite: broken
workflow: WORKFLOW
defaults: {sandbox: sometimes}
cases:
  - id: one
    input: {order_id: A1001, amount: 10}
    sandbox: "first:x"
    expect:
      steps.nope.status: succeeded
      steps.fetch_order.colour: red
      steps.policy_check.status.code: 1
      tasks.total: 1
      verdict: ok
      status.code: 1
    assert: ["output.decision ==", "status == 'succeeded'"]
  - id: one
    input: {order_id: A1002}
    expect: {status: failed}
  - id: three
    input: {order_id: A1003, amount: 10}
""",
    )
    with pytest.raises(SpecError) as exc:
        load_suite(path, env={})
    issues = issue_map(exc)
    assert set(issues) == {
        "defaults.sandbox",
        "cases[0].sandbox",
        "cases[0].expect.steps.nope.status",
        "cases[0].expect.steps.fetch_order.colour",
        "cases[0].expect.steps.policy_check.status.code",
        "cases[0].expect.tasks.total",
        "cases[0].expect.verdict",
        "cases[0].expect.status.code",
        "cases[0].assert[0]",
        "cases[1].id",
        "cases[1].input.amount",
        "cases[2]",
    }
    assert "unknown step 'nope'" in issues["cases[0].expect.steps.nope.status"]
    assert "duplicate case id" in issues["cases[1].id"]
    assert issues["cases[1].input.amount"] == "is required"


@pytest.mark.parametrize(
    "path",
    [
        "output.",
        "output..decision",
        "steps.fetch_order..status",
        "steps.fetch_order.output..status",
        "steps.fetch_order.output.",
        ".status",
    ],
)
def test_expect_paths_with_empty_segments_are_rejected(path):
    assert path_problem(path, {"fetch_order"}) == "has an empty segment"


def test_suite_structure_errors_use_yaml_paths(tmp_path):
    path = write_suite(tmp_path, "suite: Bad Name\nworkflow: WORKFLOW\ncases: []\nextra: 1\n")
    with pytest.raises(SpecError) as exc:
        load_suite(path, env={})
    assert {"suite", "cases", "extra"} <= set(issue_map(exc))


def test_suite_reports_workflow_problems(tmp_path):
    path = write_suite(
        tmp_path, "suite: s\nworkflow: missing.yaml\ncases: [{id: a, expect: {status: x}}]\n"
    )
    with pytest.raises(SpecError) as exc:
        load_suite(path, env={})
    [issue] = exc.value.issues
    assert issue.path.startswith("workflow: ") and "cannot read file" in issue.message


def test_unreadable_suite_file(tmp_path):
    with pytest.raises(SpecError) as exc:
        load_suite(tmp_path / "nope.yaml")
    assert "cannot read file" in exc.value.issues[0].message
