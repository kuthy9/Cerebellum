import re

import pytest
from rich.console import Console
from typer.testing import CliRunner

from cerebellum.cli import app as cli
from cerebellum.cli import render
from cerebellum.cli.demo import SCENARIOS
from cerebellum.config import Settings
from cerebellum.connectors.postgres import sandbox_db_path
from cerebellum.runtime.store import Store


@pytest.fixture
def runner(tmp_path, monkeypatch, free_port):
    monkeypatch.setenv("CEREBELLUM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CEREBELLUM_SANDBOX_PORT", str(free_port))
    monkeypatch.delenv("CEREBELLUM_MOCK", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=200, highlight=False))
    monkeypatch.setattr(cli, "err_console", Console(width=200, highlight=False, stderr=True))
    return CliRunner()


def test_demo_runs_five_scenarios_with_mock_ai(runner, tmp_path):
    result = runner.invoke(cli.app, ["demo"])
    text = result.stdout + (result.stderr or "")
    assert result.exit_code == 0, text
    assert "mock AI" in text
    for title in (
        "Small refund",
        "Large refund",
        "Payments API flaky",
        "Payments API down",
        "Suspicious reason",
    ):
        assert title in text
    run_ids = re.findall(r"r_[0-9a-f]{8}", text)
    assert len(set(run_ids)) == 5
    assert "cerebellum approve" in text
    assert "cerebellum ui" in text and "http://127.0.0.1:7400" in text

    settings = Settings.from_env({"CEREBELLUM_HOME": str(tmp_path / "home")})
    with Store(settings.db_path) as store:
        records = store.list_runs()
    decisions = sorted((r.status.value, (r.output or {}).get("decision")) for r in records)
    longest = max((s.title for s in SCENARIOS), key=len)
    for record in records:  # one line per scenario, even in an 80-column terminal
        assert len(render.scenario_line(longest, record).plain.rstrip()) <= 80
    assert decisions == sorted(
        [
            ("succeeded", "refunded"),
            ("waiting_approval", None),
            ("succeeded", "refunded"),
            ("needs_attention", "manual"),
            ("succeeded", "denied"),
        ]
    )

    again = runner.invoke(cli.app, ["demo"])  # the sandbox database is rebuilt each time
    assert again.exit_code == 0, again.stdout


def test_demo_says_it_reset_the_shared_sandbox_database(runner, tmp_path):
    """Review finding: the demo replaced the orders_db sandbox database, which every workflow
    with a sandbox connector named orders_db shares, without a word."""
    path = sandbox_db_path(tmp_path / "home", "orders_db")
    first = runner.invoke(cli.app, ["demo"])
    assert first.exit_code == 0, first.stdout
    assert "reset" not in first.stdout  # nothing existed, nothing was reset
    assert path.exists()
    again = runner.invoke(cli.app, ["demo"])
    assert again.exit_code == 0, again.stdout
    assert f"reset the sandbox database {path}" in again.stdout
    assert "orders_db" in again.stdout
