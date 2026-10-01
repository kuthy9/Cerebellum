import re
import shutil
import socket

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from cerebellum.cli import app as cli
from cerebellum.cli import render
from cerebellum.evals.lock import EvalLock
from cerebellum.sandbox.server import start_sandbox
from cerebellum.templates import template_path

WORKFLOW = template_path("refund") / "workflow.yaml"
EVAL_ID = re.compile(r"ev_[0-9a-f]{8}")
RUN_ID = re.compile(r"r_[0-9a-f]{8}")
SUITE = """
suite: smoke
workflow: {workflow}
cases:
  - id: small_refund
    input: {{order_id: A1001, amount: 120, reason: Torn sleeve.}}
    expect: {{status: succeeded, output.decision: {small}}}
  - id: chargeback_denied
    input: {{order_id: A1003, amount: 60, reason: I will file a chargeback.}}
    expect: {{output.decision: denied}}
  - id: unknown_order
    input: {{order_id: Z9999, amount: 10}}
    expect: {{status: failed}}
"""


@pytest.fixture
def runner(tmp_path, monkeypatch, free_port):
    monkeypatch.setenv("CEREBELLUM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CEREBELLUM_MOCK", "1")
    monkeypatch.setenv("CEREBELLUM_SANDBOX_PORT", str(free_port))
    monkeypatch.delenv("PAYMENTS_URL", raising=False)
    monkeypatch.delenv("ORDERS_DSN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=200, highlight=False))
    monkeypatch.setattr(cli, "err_console", Console(width=200, highlight=False, stderr=True))
    return CliRunner()


def invoke(runner, *args):
    result = runner.invoke(cli.app, [str(arg) for arg in args])
    result.text = result.stdout + (result.stderr or "")
    return result


def write_suite(tmp_path, small="refunded"):
    path = tmp_path / "smoke.yaml"
    path.write_text(SUITE.format(workflow=WORKFLOW, small=small), encoding="utf-8")
    return path


def test_eval_runs_a_suite_and_compares_with_the_previous_run(runner, tmp_path):
    suite = write_suite(tmp_path)
    first = invoke(runner, "eval", suite)
    assert first.exit_code == 0, first.text
    assert "3/3 passed" in first.text and "first run of this suite with mock AI" in first.text
    assert "mock AI" in first.text
    assert "fresh sandbox database" in first.text and "(sandbox)" in first.text
    assert "is not the sandbox" not in first.text
    second = invoke(runner, "eval", suite)
    assert second.exit_code == 0, second.text
    first_id = EVAL_ID.search(first.text).group(0)
    assert f"vs {first_id} (mock AI): 3/3 → 3/3" in second.text and "no regressions" in second.text


def text_of(renderable):
    console = Console(width=200, highlight=False, record=True)
    console.print(renderable)
    return console.export_text()


def test_eval_summary_names_the_ai_mode_it_compares(store):
    """Review finding: the summary did not say which AI mode the baseline eval used."""
    for eval_id in ("ev_00000001", "ev_00000002"):
        store.create_eval_run(
            eval_id,
            suite="s",
            suite_path="/x/evals.yaml",
            workflow_name="wf",
            workflow_digest="d1",
            mock=False,
            total=1,
            baseline_id=None,
        )
    baseline = store.finish_eval_run("ev_00000001", status="completed")
    record = store.finish_eval_run("ev_00000002", status="completed")
    compared = text_of(render.eval_summary(record, baseline, [], 0.9))
    assert "vs ev_00000001 (Claude API): 0/1 → 0/1" in compared
    first = text_of(render.eval_summary(record, None, [], 0.9))
    assert "first run of this suite with the Claude API" in first


def test_eval_exit_code_follows_min_pass(runner, tmp_path):
    assert invoke(runner, "eval", write_suite(tmp_path)).exit_code == 0  # the baseline
    broken = write_suite(tmp_path, small="manual")
    result = invoke(runner, "eval", broken)
    assert result.exit_code == 1, result.text  # 2/3 is below the default 90%
    assert 'output.decision: expected "manual" · got "refunded"' in result.text
    assert "↓ regression" in result.text and "1 regression(s): small_refund" in result.text
    lenient = invoke(runner, "eval", broken, "--min-pass", "0.6")
    assert lenient.exit_code == 0, lenient.text


def test_eval_rejects_an_invalid_suite_with_exit_2(runner, tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        f"suite: bad\nworkflow: {WORKFLOW}\ncases:\n"
        "  - id: a\n    input: {order_id: A1}\n    expect: {steps.nope.status: x}\n",
        encoding="utf-8",
    )
    result = invoke(runner, "eval", path)
    assert result.exit_code == 2, result.text
    assert "cases[0].expect.steps.nope.status" in result.text
    assert "cases[0].input.amount" in result.text


def test_an_invalid_suite_is_reported_even_when_the_sandbox_port_is_busy(
    runner, tmp_path, free_port
):
    """Review finding: the sandbox started first, so a busy port hid the suite's issues."""
    path = tmp_path / "bad.yaml"
    path.write_text(
        f"suite: bad\nworkflow: {WORKFLOW}\ncases:\n"
        "  - id: a\n    input: {order_id: A1}\n    expect: {steps.nope.status: x}\n",
        encoding="utf-8",
    )
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", free_port))
        blocker.listen()
        result = invoke(runner, "eval", path)
    assert result.exit_code == 2, result.text
    assert "cases[0].expect.steps.nope.status" in result.text
    assert "in use" not in result.text


def test_a_workflow_that_needs_the_sandbox_url_validates_before_the_sandbox_starts(
    runner, tmp_path, free_port
):
    """The suite is validated before the sandbox starts, with PAYMENTS_URL as the sandbox
    will set it: a connector that requires the variable still loads and targets the sandbox."""
    project = tmp_path / "refund"
    shutil.copytree(template_path("refund"), project)
    workflow = project / "workflow.yaml"
    source = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        source.replace("${PAYMENTS_URL:-http://127.0.0.1:8787}", "${PAYMENTS_URL}"),
        encoding="utf-8",
    )
    suite = tmp_path / "smoke.yaml"
    suite.write_text(SUITE.format(workflow=workflow, small="refunded"), encoding="utf-8")
    result = invoke(runner, "eval", suite)
    assert result.exit_code == 0, result.text
    assert f"http://127.0.0.1:{free_port} (sandbox)" in result.text and "3/3 passed" in result.text


def test_eval_warns_when_a_connector_is_not_the_sandbox(runner, tmp_path, monkeypatch):
    """Review finding: a user-set PAYMENTS_URL was called for real with no hint."""
    monkeypatch.setenv("PAYMENTS_URL", "http://127.0.0.1:1")
    path = tmp_path / "unknown.yaml"
    path.write_text(
        f"suite: unknown_only\nworkflow: {WORKFLOW}\ncases:\n"
        "  - id: unknown\n    input: {order_id: Z9999, amount: 10}\n"
        "    expect: {status: failed}\n",
        encoding="utf-8",
    )
    result = invoke(runner, "eval", path)
    assert result.exit_code == 0, result.text
    assert "payments → http://127.0.0.1:1 is not the sandbox payments API" in result.text


def test_eval_says_when_it_reuses_a_sandbox_another_process_started(runner, tmp_path, free_port):
    """Review finding: an eval silently switched the fail modes of the dashboard's sandbox."""
    dashboard_sandbox = start_sandbox("127.0.0.1", free_port)
    try:
        result = invoke(runner, "eval", write_suite(tmp_path))
    finally:
        dashboard_sandbox.stop()
    assert result.exit_code == 0, result.text
    assert f"reusing the sandbox payments API at http://127.0.0.1:{free_port}" in result.text
    assert "runs started there meanwhile" in result.text


def test_eval_owning_its_sandbox_says_nothing_about_reuse(runner, tmp_path):
    result = invoke(runner, "eval", write_suite(tmp_path))
    assert result.exit_code == 0, result.text
    assert "reusing the sandbox" not in result.text


def test_a_second_eval_in_the_same_home_is_refused(runner, tmp_path, free_port):
    """Review finding: concurrent evals interleaved one sandbox's fail modes, and the first to
    finish stopped the sandbox under the other."""
    shared = start_sandbox("127.0.0.1", free_port, "always")  # the first eval, mid-case
    try:
        with EvalLock(tmp_path / "home"):
            result = invoke(runner, "eval", write_suite(tmp_path))
        assert httpx.get(f"{shared.url}/health").json()["fail_mode"] == "always"
    finally:
        shared.stop()
    assert result.exit_code == 1, result.text
    assert "another `cerebellum eval` is running in" in result.text
    assert "passed" not in result.text
    assert invoke(runner, "eval", write_suite(tmp_path)).exit_code == 0


def test_evals_prune_removes_old_sandbox_directories_and_says_which(runner, tmp_path):
    suite = write_suite(tmp_path)
    first = EVAL_ID.findall(invoke(runner, "eval", suite).text)[-1]
    second = EVAL_ID.findall(invoke(runner, "eval", suite).text)[-1]  # after "vs <baseline>"
    assert first != second
    evals_dir = tmp_path / "home" / "evals"
    assert (evals_dir / first).is_dir() and (evals_dir / second).is_dir()

    result = invoke(runner, "evals", "prune", "--keep", "1")
    assert result.exit_code == 0, result.text
    assert f"{evals_dir / first}" in result.text and "smoke" in result.text
    assert "removed 1 sandbox directory" in result.text and "newest 1 per suite" in result.text
    assert not (evals_dir / first).exists() and (evals_dir / second).is_dir()
    assert f"vs {second}" in invoke(runner, "eval", suite).text  # the history is kept

    again = invoke(runner, "evals", "prune", "--keep", "2")
    assert again.exit_code == 0 and "nothing to prune" in again.text, again.text


def test_runs_leaves_eval_runs_out_unless_asked(runner, tmp_path):
    """Review finding: after `make eval`, `cerebellum runs` showed failed and rejected refunds."""
    assert invoke(runner, "eval", write_suite(tmp_path)).exit_code == 0
    plain = invoke(runner, "runs")
    assert plain.exit_code == 0 and not RUN_ID.search(plain.text), plain.text
    shown = invoke(runner, "runs", "--evals")
    assert len(set(RUN_ID.findall(shown.text))) == 3


def test_packaged_suite_passes_from_the_cli(runner):
    result = invoke(runner, "eval", template_path("refund") / "evals.yaml")
    assert result.exit_code == 0, result.text
    assert "15/15 passed" in result.text
