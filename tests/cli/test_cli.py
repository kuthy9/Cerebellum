import os
import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rich.console import Console
from typer.testing import CliRunner

from cerebellum.cli import app as cli
from cerebellum.config import DEFAULT_UI_SHUTDOWN_GRACE_SECONDS
from cerebellum.evals import load_suite
from cerebellum.sandbox.server import sandbox_running
from cerebellum.server.app import dashboard_server
from cerebellum.templates import template_path

WORKFLOW = str(template_path("refund") / "workflow.yaml")
INPUTS = template_path("refund") / "inputs"
RUN_ID = re.compile(r"r_[0-9a-f]{8}")


@pytest.fixture
def runner(tmp_path, monkeypatch, free_port):
    monkeypatch.setenv("CEREBELLUM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CEREBELLUM_MOCK", "1")
    monkeypatch.setenv("CEREBELLUM_SANDBOX_PORT", str(free_port))
    monkeypatch.setenv("PAYMENTS_URL", f"http://127.0.0.1:{free_port}")
    monkeypatch.delenv("ORDERS_DSN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=200, highlight=False))
    monkeypatch.setattr(cli, "err_console", Console(width=200, highlight=False, stderr=True))
    return CliRunner()


def invoke(runner, *args):
    result = runner.invoke(cli.app, list(args))
    result.text = result.stdout + (result.stderr or "")
    return result


def run_id_of(result):
    match = RUN_ID.search(result.text)
    assert match, result.text
    return match.group(0)


def test_version(runner):
    result = invoke(runner, "--version")
    assert result.exit_code == 0 and "cerebellum 0.2.0" in result.text


def test_validate_ok(runner):
    result = invoke(runner, "validate", WORKFLOW)
    assert result.exit_code == 0, result.text
    assert "refund_request v1 is valid" in result.text


def test_validate_reports_issues_with_exit_code_2(runner, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: broken\nsteps:\n  - {id: a, type: task, title: x, needs: [ghost]}\n")
    result = invoke(runner, "validate", str(bad))
    assert result.exit_code == 2
    assert "steps[0].needs" in result.text and "unknown step 'ghost'" in result.text


def test_show_lists_steps_and_fallbacks(runner):
    result = invoke(runner, "show", WORKFLOW)
    assert result.exit_code == 0
    for name in (
        "fetch_order",
        "assess_request",
        "manager_approval",
        "issue_refund",
        "open_manual_case",
    ):
        assert name in result.text
    assert "⤳ for issue_refund" in result.text


def test_run_small_refund_succeeds(runner):
    result = invoke(runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'small.json'}", "--sandbox")
    assert result.exit_code == 0, result.text
    assert '"decision": "refunded"' in result.text
    run_id = run_id_of(result)
    listing = invoke(runner, "runs")
    assert run_id in listing.text and "succeeded" in listing.text
    trace = invoke(runner, "trace", run_id)
    assert "issue_refund #1" in trace.text and "POST /refunds → 201" in trace.text


def test_sandbox_on_a_custom_port_is_used_when_payments_url_is_unset(runner, monkeypatch):
    """CEREBELLUM_SANDBOX_PORT is what the CLI tells you to set when 8787 is taken."""
    monkeypatch.delenv("PAYMENTS_URL")
    result = invoke(runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'small.json'}", "--sandbox")
    assert result.exit_code == 0, result.text
    assert '"decision": "refunded"' in result.text


def test_large_refund_waits_then_approve_resumes(runner):
    started = invoke(runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'large.json'}", "--sandbox")
    assert started.exit_code == 3, started.text
    run_id = run_id_of(started)
    assert f"cerebellum approve {run_id} manager_approval" in started.text
    pending = invoke(runner, "approvals")
    assert run_id in pending.text and "pending" in pending.text
    approved = invoke(runner, "approve", run_id, "--by", "alice", "-m", "ok", "--sandbox")
    assert approved.exit_code == 0, approved.text
    assert "approved by alice" in approved.text
    assert '"decision": "refunded"' in approved.text
    assert "approved · alice" in invoke(runner, "status", run_id).text


def test_reject_marks_the_run_rejected(runner):
    started = invoke(runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'large.json'}", "--sandbox")
    run_id = run_id_of(started)
    rejected = invoke(runner, "reject", run_id, "manager_approval", "--by", "bob", "-m", "too high")
    assert rejected.exit_code == 1
    assert "rejected by bob" in rejected.text
    assert '"decision": "rejected"' in rejected.text


def test_outage_falls_back_to_a_manual_task(runner):
    result = invoke(
        runner,
        "run",
        WORKFLOW,
        "-i",
        f"@{INPUTS / 'outage.json'}",
        "--sandbox",
        "--sandbox-fail",
        "always",
    )
    assert result.exit_code == 0, result.text
    assert "needs attention" in result.text and "1 manual task(s) open" in result.text
    tasks = invoke(runner, "tasks")
    task_id = re.search(r"tk_[0-9a-f]{8}", tasks.text).group(0)
    resolved = invoke(runner, "tasks", "resolve", task_id, "--by", "ops", "-m", "refunded by hand")
    assert resolved.exit_code == 0 and f"{task_id} resolved by ops" in resolved.text
    assert "no open tasks" in invoke(runner, "tasks").text


def test_run_rejects_bad_input(runner):
    """Review focus: wrong input types are reported before any run is created."""
    result = invoke(runner, "run", WORKFLOW, "-i", '{"order_id": "A1001", "amount": "120"}')
    assert result.exit_code == 2
    assert "input.amount" in result.text and "expected number" in result.text
    assert "no runs yet" in invoke(runner, "runs").text


def test_run_rejects_invalid_json_and_params(runner):
    assert invoke(runner, "run", WORKFLOW, "-i", "{oops").exit_code == 2
    assert invoke(runner, "run", WORKFLOW, "-i", "{}", "-p", "novalue").exit_code == 2
    unknown = invoke(runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'small.json'}", "-p", "nope=1")
    assert unknown.exit_code == 2 and "params.nope" in unknown.text


def test_param_values_are_json_or_the_literal_string():
    """Review finding: YAML parsing turned `no` into False, `on` into True and `010` into 8."""
    params = cli._parse_params(
        [
            "answer=no",
            "switch=on",
            "code=010",
            "limit=1000",
            "ratio=0.5",
            "flag=true",
            "nothing=null",
            'quoted="no"',
            'object={"a": [1, 2]}',
            "text=hello world",
            "empty=",
            "eq=a=b",
        ]
    )
    assert params == {
        "answer": "no",
        "switch": "on",
        "code": "010",
        "limit": 1000,
        "ratio": 0.5,
        "flag": True,
        "nothing": None,
        "quoted": "no",
        "object": {"a": [1, 2]},
        "text": "hello world",
        "empty": "",
        "eq": "a=b",
    }


def test_param_override_changes_the_approval_threshold(runner):
    result = invoke(
        runner,
        "run",
        WORKFLOW,
        "-i",
        f"@{INPUTS / 'large.json'}",
        "-p",
        "approval_threshold=1000",
        "--sandbox",
    )
    assert result.exit_code == 0, result.text
    assert '"decision": "refunded"' in result.text


def test_resume_of_a_finished_run_fails_cleanly(runner):
    done = invoke(runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'fraud.json'}", "--sandbox")
    assert done.exit_code == 0, done.text
    assert '"decision": "denied"' in done.text
    resumed = invoke(runner, "resume", run_id_of(done))
    assert resumed.exit_code == 1 and "cannot be resumed" in resumed.text


def test_status_of_unknown_run(runner):
    result = invoke(runner, "status", "r_00000000")
    assert result.exit_code == 1 and "not found" in result.text


ENV_FLOW = """
name: needs_env
connectors:
  api: {type: rest, base_url: "${CEREBELLUM_TEST_API_URL}"}
steps:
  - {id: gate, type: approval, title: Go ahead}
  - {id: call, type: http, needs: [gate], connector: api, method: GET, path: /ping}
"""


def test_status_shows_a_run_whose_connector_variables_are_unset(runner, tmp_path, monkeypatch):
    """Review finding: `status` opens no connector, yet failed when the snapshot's ${VAR}s were
    unset in this shell. Resuming the run still needs the real values."""
    flow = tmp_path / "needs_env.yaml"
    flow.write_text(ENV_FLOW, encoding="utf-8")
    monkeypatch.setenv("CEREBELLUM_TEST_API_URL", "http://127.0.0.1:9")
    started = invoke(runner, "run", str(flow))
    assert started.exit_code == 3, started.text
    run_id = run_id_of(started)
    monkeypatch.delenv("CEREBELLUM_TEST_API_URL")

    status = invoke(runner, "status", run_id)
    assert status.exit_code == 0, status.text
    assert "gate" in status.text and "call" in status.text and "awaiting approval" in status.text
    trace = invoke(runner, "trace", run_id)
    assert trace.exit_code == 0, trace.text

    for command in (("resume", run_id), ("approve", run_id, "--by", "alice")):
        refused = invoke(runner, *command)
        assert refused.exit_code == 2, refused.text
        assert "CEREBELLUM_TEST_API_URL is not set" in refused.text
    assert invoke(runner, "approvals").text.count(run_id) == 1  # still pending


def test_runs_rejects_unknown_status(runner):
    result = invoke(runner, "runs", "--status", "bogus")
    assert result.exit_code == 2 and "waiting_approval" in result.text


def test_connectors_check_reports_each_connector(runner):
    result = invoke(runner, "connectors", "check", WORKFLOW)
    assert "orders_db" in result.text and "payments" in result.text
    assert result.exit_code == 1  # nothing listens on the sandbox port in this test


def test_init_scaffolds_files_once(runner, tmp_path):
    target = tmp_path / "project"
    first = invoke(runner, "init", str(target))
    assert first.exit_code == 0, first.text
    assert (target / "workflows" / "refund" / "workflow.yaml").exists()
    assert (target / "workflows" / "refund" / "inputs" / "small.json").exists()
    assert (target / ".env.example").exists()
    suite = target / "workflows" / "refund" / "evals.yaml"
    assert suite.exists()
    assert load_suite(suite).suite.suite == "refund_regression"
    assert "cerebellum eval" in first.text
    second = invoke(runner, "init", str(target))
    assert "exists, kept" in second.text
    assert (
        invoke(runner, "validate", str(target / "workflows" / "refund" / "workflow.yaml")).exit_code
        == 0
    )


def fake_server(monkeypatch, on_run=lambda calls: None):
    """Replace the dashboard's uvicorn server: record how it was built, call `on_run` instead
    of serving."""
    calls = {}

    def build(app, **kwargs):
        server = dashboard_server(app, **kwargs)  # the real configuration, never started
        calls.update(kwargs, app=app, config=server.config)
        return SimpleNamespace(run=lambda: on_run(calls))

    monkeypatch.setattr(cli, "dashboard_server", build)
    return calls


def test_ui_serves_the_dashboard_on_localhost(runner, monkeypatch):
    calls = fake_server(monkeypatch)
    result = invoke(runner, "ui", "--port", "7555", "--no-sandbox", "--mock")
    assert result.exit_code == 0, result.text
    assert (calls["host"], calls["port"]) == ("127.0.0.1", 7555)
    assert calls["config"].timeout_graceful_shutdown == DEFAULT_UI_SHUTDOWN_GRACE_SECONDS
    assert isinstance(calls["app"], FastAPI)
    assert "http://127.0.0.1:7555" in result.text and "mock AI" in result.text
    assert "no authentication" not in result.text


def test_ui_warns_when_reachable_beyond_this_machine(runner, monkeypatch):
    fake_server(monkeypatch)
    result = invoke(runner, "ui", "--host", "0.0.0.0", "--no-sandbox")
    assert result.exit_code == 0, result.text
    assert "no authentication" in result.text


def test_ui_starts_the_sandbox_and_points_payments_at_it(runner, monkeypatch):
    seen = {}

    def on_run(calls):
        seen["url"] = os.environ.get("PAYMENTS_URL")
        seen["healthy"] = sandbox_running(seen["url"])

    fake_server(monkeypatch, on_run)
    monkeypatch.delenv("PAYMENTS_URL")
    result = invoke(runner, "ui")
    assert result.exit_code == 0, result.text
    assert seen["healthy"] is True and seen["url"].startswith("http://127.0.0.1:")
    assert "PAYMENTS_URL" not in os.environ


def test_ui_on_loopback_refuses_other_host_names(runner, monkeypatch):
    """Review finding: DNS rebinding. A web page whose domain resolves to 127.0.0.1 is
    same-origin with the dashboard; its requests carry its own Host header and are refused."""
    calls = fake_server(monkeypatch)
    assert invoke(runner, "ui", "--port", "7555", "--no-sandbox", "--mock").exit_code == 0
    client = TestClient(calls["app"], base_url="http://127.0.0.1:7555")
    assert client.get("/api/info").status_code == 200
    assert client.get("/api/info", headers={"host": "localhost:7555"}).status_code == 200
    assert client.get("/api/info", headers={"host": "[::1]:7555"}).status_code == 200
    refused = client.get("/api/info", headers={"host": "rebind.example:7555"})
    assert refused.status_code == 400
    assert (
        client.post("/api/runs/r_x/resume", headers={"host": "rebind.example"}).status_code == 400
    )


def test_ui_beyond_this_machine_accepts_any_host_name(runner, monkeypatch):
    calls = fake_server(monkeypatch)
    assert invoke(runner, "ui", "--host", "0.0.0.0", "--no-sandbox", "--mock").exit_code == 0
    client = TestClient(calls["app"], base_url="http://192.168.1.5:7400")
    assert client.get("/api/info").status_code == 200
