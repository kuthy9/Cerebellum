import asyncio
import os
import re
import shutil
import socket
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rich.console import Console
from typer.testing import CliRunner

from cerebellum.cli import app as cli
from cerebellum.cli import render
from cerebellum.config import DEFAULT_UI_SHUTDOWN_GRACE_SECONDS, Settings
from cerebellum.evals import load_suite
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus
from cerebellum.runtime.store import Store
from cerebellum.sandbox.server import sandbox_running
from cerebellum.server.app import dashboard_server
from cerebellum.spec import load_workflow
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


def test_run_reports_an_invalid_workflow_even_when_the_sandbox_port_is_busy(
    runner, tmp_path, free_port
):
    """Review finding: `run --sandbox` started the sandbox before loading the workflow, so a
    busy port hid the workflow's issues (as E6 fixed for `eval`)."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: broken\nsteps:\n  - {id: a, type: task, title: x, needs: [ghost]}\n")
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", free_port))
        blocker.listen()
        result = invoke(runner, "run", str(bad), "--sandbox")
    assert result.exit_code == 2, result.text
    assert "unknown step 'ghost'" in result.text and "in use" not in result.text


def test_run_loads_a_workflow_that_needs_the_sandbox_url(runner, tmp_path, monkeypatch, free_port):
    """Loading before the sandbox starts still gives PAYMENTS_URL the sandbox's address."""
    monkeypatch.delenv("PAYMENTS_URL")
    project = tmp_path / "refund"
    shutil.copytree(template_path("refund"), project)
    workflow = project / "workflow.yaml"
    source = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        source.replace("${PAYMENTS_URL:-http://127.0.0.1:8787}", "${PAYMENTS_URL}"),
        encoding="utf-8",
    )
    result = invoke(runner, "run", str(workflow), "-i", f"@{INPUTS / 'small.json'}", "--sandbox")
    assert result.exit_code == 0, result.text
    with Store(Settings.from_env().db_path) as store:
        steps = store.get_steps(run_id_of(result))
    # The refund reached this command's sandbox (the only payments API on that port).
    assert steps["issue_refund"].status.value == "succeeded", steps["issue_refund"]


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


def test_an_approval_rejected_by_its_timeout_is_not_blamed_on_a_human(runner, tmp_path):
    """Review finding: on_timeout: reject was reported as "rejected by a human approver"."""
    started = invoke(runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'large.json'}", "--sandbox")
    assert started.exit_code == 3, started.text
    run_id = run_id_of(started)
    with Store(Settings.from_env().db_path) as store:  # what the engine does once it is due
        [approval] = store.list_approvals(run_id=run_id)
        store.decide_approval(
            approval.id, approved=False, by="system", comment="approval timed out", expired=True
        )
    resumed = invoke(runner, "resume", run_id, "--sandbox")
    assert resumed.exit_code == 1, resumed.text
    for text in (resumed.text, invoke(runner, "status", run_id).text):
        assert "approval timed out" in text and "manager_approval" in text
        assert "human" not in text


SIGN_OFF_FLOW = """
name: sign_off
steps:
  - {id: gate, type: approval, title: Sign off}
  - {id: note, type: validate, needs: [gate], rules: [{expr: "true", message: ok}]}
"""


class FakeClaude:
    """Stands in for the Claude provider; the workflow above never calls it."""

    name = "claude"
    mock = False

    async def generate(self, request, messages):  # pragma: no cover - not used
        raise AssertionError("not expected")


def test_a_claude_run_never_continues_on_mock_ai(runner, tmp_path, monkeypatch):
    """Review finding: approve/reject/resume silently drove a run started with the Claude API
    on the mock provider when no credentials were available (or CEREBELLUM_MOCK was set)."""
    flow = tmp_path / "sign_off.yaml"
    flow.write_text(SIGN_OFF_FLOW, encoding="utf-8")
    settings = Settings.from_env()
    with Store(settings.db_path) as store:
        run = asyncio.run(Engine(store, settings, FakeClaude()).start(load_workflow(flow)))
    assert run.status is RunStatus.WAITING_APPROVAL and run.mock is False

    for command in ("approve", "reject", "resume"):  # CEREBELLUM_MOCK=1 in this fixture
        refused = invoke(runner, command, run.run_id)
        assert refused.exit_code == 1, refused.text
        assert "started with the Claude API" in refused.text
        assert "CEREBELLUM_MOCK" in refused.text

    monkeypatch.delenv("CEREBELLUM_MOCK")
    monkeypatch.setattr("cerebellum.ai.has_anthropic_credentials", lambda *args, **kw: False)
    refused = invoke(runner, "approve", run.run_id, "--by", "alice")
    assert refused.exit_code == 1, refused.text
    assert "started with the Claude API" in refused.text and "ANTHROPIC_API_KEY" in refused.text
    assert "pending" in invoke(runner, "approvals").text

    recorded = invoke(runner, "approve", run.run_id, "--by", "alice", "--no-resume")
    assert recorded.exit_code == 3, recorded.text  # recording a decision needs no AI
    assert "approved by alice" in recorded.text


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


@pytest.mark.parametrize(
    "text", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400", "[1, NaN]", '{"a": 1e400}']
)
def test_param_values_keep_non_finite_numbers_as_text(text):
    """Review finding: json.loads accepts NaN/Infinity and turns 1e400 into inf; a run storing
    one made the dashboard's run endpoints fail (such values are not JSON)."""
    assert cli._parse_params([f"limit={text}"]) == {"limit": text}


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_run_rejects_non_finite_numbers_in_input(runner, tmp_path, bad):
    """Review finding: --input accepted NaN and the infinities (and read 1e400 as one); the run
    it stored made the dashboard's run endpoints answer 500."""
    documents = (
        f'{{"order_id": "A1001", "amount": {bad}}}',
        f'{{"order_id": "A1001", "amount": 10, "notes": {{"lines": [1, {bad}]}}}}',
    )
    for position, text in enumerate(documents):
        path = tmp_path / f"input{position}.json"
        path.write_text(text, encoding="utf-8")
        for raw in (text, f"@{path}"):
            result = invoke(runner, "run", WORKFLOW, "-i", raw)
            assert result.exit_code == 2, (raw, result.text)
            assert isinstance(result.exception, SystemExit), raw
            assert "input is not valid JSON" in result.text and bad in result.text, raw
    assert "no runs yet" in invoke(runner, "runs").text


def test_an_input_file_that_is_not_utf8_is_invalid_input(runner, tmp_path):
    """Review finding: a non-UTF-8 --input file ended in a UnicodeDecodeError traceback."""
    path = tmp_path / "input.json"
    path.write_bytes(b'{"order_id": "\xff"}')
    result = invoke(runner, "run", WORKFLOW, "-i", f"@{path}")
    assert result.exit_code == 2, result.text
    assert isinstance(result.exception, SystemExit)
    assert f"cannot read input file {path}" in result.text and "UTF-8" in result.text


def test_run_stores_a_non_finite_param_as_text(runner):
    result = invoke(
        runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'small.json'}", "-p", "approval_threshold=NaN"
    )
    with Store(Settings.from_env().db_path) as store:
        assert store.get_run(run_id_of(result)).params["approval_threshold"] == "NaN"


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


@pytest.mark.parametrize(("command", "verb"), [("approve", "approved"), ("reject", "rejected")])
def test_a_decision_without_resuming_needs_no_connector_variables(
    runner, tmp_path, monkeypatch, command, verb
):
    """Review finding: `approve/reject --no-resume` opens no connector, yet exited 2 when the
    snapshot's ${VAR}s were unset in this shell. Resuming the run still needs the real values."""
    flow = tmp_path / "needs_env.yaml"
    flow.write_text(ENV_FLOW, encoding="utf-8")
    monkeypatch.setenv("CEREBELLUM_TEST_API_URL", "http://127.0.0.1:9")
    started = invoke(runner, "run", str(flow))
    assert started.exit_code == 3, started.text
    run_id = run_id_of(started)
    monkeypatch.delenv("CEREBELLUM_TEST_API_URL")

    decided = invoke(runner, command, run_id, "--by", "alice", "--no-resume")
    assert decided.exit_code == 3, decided.text  # recorded; the run waits to be resumed
    assert f"{verb} by alice" in decided.text
    assert run_id not in invoke(runner, "approvals").text  # no longer pending
    refused = invoke(runner, "resume", run_id)
    assert refused.exit_code == 2, refused.text
    assert "CEREBELLUM_TEST_API_URL is not set" in refused.text


def test_a_bad_pricing_file_is_a_clear_error(runner, tmp_path, monkeypatch):
    """Review finding: a bad CEREBELLUM_PRICING_FILE ended the CLI with a traceback."""
    monkeypatch.delenv("CEREBELLUM_MOCK")
    monkeypatch.setattr("cerebellum.ai.has_anthropic_credentials", lambda *args, **kw: True)
    prices = tmp_path / "prices.json"
    prices.write_text("{oops", encoding="utf-8")
    monkeypatch.setenv("CEREBELLUM_PRICING_FILE", str(prices))
    result = invoke(runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'small.json'}")
    assert result.exit_code == 1, result.text
    assert isinstance(result.exception, SystemExit)  # handled, not a traceback
    assert "CEREBELLUM_PRICING_FILE" in result.text and "not valid JSON" in result.text


def test_a_bad_numeric_setting_is_a_clear_error(runner, monkeypatch):
    """Review finding: CEREBELLUM_SANDBOX_PORT=abc ended every command in a ValueError
    traceback."""
    monkeypatch.setenv("CEREBELLUM_SANDBOX_PORT", "abc")
    fake_server(monkeypatch)
    commands = (("runs",), ("tasks",), ("ui", "--no-sandbox", "--mock"), ("run", WORKFLOW))
    for command in commands:
        result = invoke(runner, *command)
        assert result.exit_code == 1, (command, result.text)
        assert isinstance(result.exception, SystemExit), command  # handled, not a traceback
        assert "CEREBELLUM_SANDBOX_PORT must be an integer from 1 to 65535, got 'abc'" in (
            result.text
        )
        assert "Traceback" not in result.text


def test_a_corrupt_database_is_a_clear_error(runner, tmp_path, monkeypatch):
    """Review finding: sqlite errors (corrupt or unwritable CEREBELLUM_HOME) were tracebacks."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "cerebellum.db").write_bytes(b"this is not a sqlite database" * 100)
    fake_server(monkeypatch)  # the dashboard checks its database before it serves
    for command in (("runs",), ("status", "r_00000000"), ("ui", "--no-sandbox", "--mock")):
        result = invoke(runner, *command)
        assert result.exit_code == 1, result.text
        assert isinstance(result.exception, SystemExit)
        assert str(home / "cerebellum.db") in result.text and "not a database" in result.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_home_is_a_clear_error(runner, tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        monkeypatch.setenv("CEREBELLUM_HOME", str(locked))
        result = invoke(runner, "runs")
        assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.text
        assert str(locked / "cerebellum.db") in result.text
        monkeypatch.setenv("CEREBELLUM_HOME", str(locked / "home"))
        result = invoke(runner, "runs")
        assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.text
        assert "CEREBELLUM_HOME" in result.text and str(locked / "home") in result.text
    finally:
        locked.chmod(0o700)


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


def test_ui_reports_whether_mock_ai_was_requested(runner, monkeypatch):
    calls = fake_server(monkeypatch)
    monkeypatch.delenv("CEREBELLUM_MOCK")
    monkeypatch.setattr("cerebellum.ai.has_anthropic_credentials", lambda *args: False)

    def info():
        return TestClient(calls["app"], base_url="http://127.0.0.1:7400").get("/api/info").json()

    assert invoke(runner, "ui", "--no-sandbox", "--mock").exit_code == 0
    assert info()["mock"] is True and info()["mock_requested"] is True
    assert invoke(runner, "ui", "--no-sandbox").exit_code == 0  # no credentials: mock anyway
    assert info()["mock"] is True and info()["mock_requested"] is False


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


LOOPBACK_SPELLINGS = (
    "LOCALHOST",
    "Localhost",
    "localhost.",
    " localhost ",
    "127.1",
    "127.0.0.2",
    "0:0:0:0:0:0:0:1",
)


@pytest.mark.parametrize("spelling", LOOPBACK_SPELLINGS)
@pytest.mark.parametrize("source", ["flag", "env"])
def test_ui_on_any_spelling_of_loopback_keeps_the_host_check(runner, monkeypatch, spelling, source):
    """Review finding: only the exact strings 127.0.0.1, localhost and ::1 counted as loopback,
    so LOCALHOST, localhost., 127.1 or 127.0.0.2 (all bound to this machine only) turned the
    DNS-rebinding check off and printed the warning meant for binds reachable from elsewhere."""
    calls = fake_server(monkeypatch)
    args = ["ui", "--port", "7555", "--no-sandbox", "--mock"]
    if source == "env":
        monkeypatch.setenv("CEREBELLUM_UI_HOST", spelling)
    else:
        args += ["--host", spelling]
    result = invoke(runner, *args)
    assert result.exit_code == 0, result.text
    assert "no authentication" not in result.text
    client = TestClient(calls["app"], base_url="http://127.0.0.1:7555")
    name = spelling.strip()
    own = f"[{name}]:7555" if ":" in name else f"{name}:7555"
    assert client.get("/api/info", headers={"host": own}).status_code == 200, own
    assert client.get("/api/info").status_code == 200
    for evil in ("evil.example:7555", "EVIL.example.:7555", "evil.example"):
        assert client.get("/api/info", headers={"host": evil}).status_code == 400, evil


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.1.2.3"])
def test_ui_warns_and_accepts_any_host_name_beyond_this_machine(runner, monkeypatch, host):
    calls = fake_server(monkeypatch)
    result = invoke(runner, "ui", "--host", host, "--no-sandbox", "--mock")
    assert result.exit_code == 0, result.text
    assert "no authentication" in result.text
    client = TestClient(calls["app"], base_url="http://192.168.1.5:7400")
    assert client.get("/api/info", headers={"host": "evil.example:7400"}).status_code == 200


def test_the_host_check_ignores_case_and_a_trailing_dot(runner, monkeypatch):
    """A Host header names the same host in any case and with or without its trailing dot."""
    calls = fake_server(monkeypatch)
    assert invoke(runner, "ui", "--port", "7555", "--no-sandbox", "--mock").exit_code == 0
    client = TestClient(calls["app"], base_url="http://127.0.0.1:7555")
    for host in ("LOCALHOST:7555", "LocalHost.:7555", "localhost.", "127.0.0.1.:7555"):
        assert client.get("/api/info", headers={"host": host}).status_code == 200, host


def test_trace_does_not_draw_spans_closed_by_a_reset_or_cancel_as_running():
    for status in ("interrupted", "cancelled"):
        assert render.SPAN_STYLES.get(status, render.ACCENT) != render.ACCENT
