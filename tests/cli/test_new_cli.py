import json
import os

import pytest
from rich.console import Console
from typer.testing import CliRunner

from cerebellum.ai.base import AIResult, Usage
from cerebellum.authoring import DRAFT_HEADER
from cerebellum.cli import app as cli

VALID = """\
name: invoice_approval
steps:
  - id: check_amount
    type: validate
    rules:
      - {expr: "input.amount > 0", message: Amount must be positive}
"""


class ScriptedProvider:
    name = "scripted"
    mock = False

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def generate(self, request, messages):
        self.calls.append(messages)
        return AIResult(
            text=self.replies.pop(0),
            model=request.model,
            usage=Usage(),
            cost_usd=0.0,
            mock=False,
            stop_reason="end_turn",
            latency_ms=1.0,
        )


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("CEREBELLUM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CEREBELLUM_MOCK", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=200, highlight=False))
    monkeypatch.setattr(cli, "err_console", Console(width=200, highlight=False, stderr=True))
    return CliRunner()


def invoke(runner, *args):
    result = runner.invoke(cli.app, [str(arg) for arg in args])
    result.text = result.stdout + (result.stderr or "")
    return result


def use_provider(monkeypatch, *replies):
    provider = ScriptedProvider(*replies)
    monkeypatch.setattr(cli, "_draft_provider", lambda settings: provider)
    return provider


def test_new_writes_a_validated_draft_with_a_review_header(runner, monkeypatch, tmp_path):
    use_provider(monkeypatch, json.dumps({"yaml": VALID, "summary": "Checks invoice amounts."}))
    target = tmp_path / "workflows" / "invoice.yaml"
    result = invoke(runner, "new", "Check invoice amounts", "-o", target)
    assert result.exit_code == 0, result.text
    text = target.read_text(encoding="utf-8")
    assert text.startswith(DRAFT_HEADER) and "name: invoice_approval" in text
    assert "review it before running" in result.text and "Checks invoice amounts." in result.text
    assert invoke(runner, "validate", target).exit_code == 0


def test_new_refuses_to_overwrite_without_force(runner, monkeypatch, tmp_path):
    provider = use_provider(monkeypatch, json.dumps({"yaml": VALID, "summary": ""}))
    target = tmp_path / "invoice.yaml"
    target.write_text("keep me\n", encoding="utf-8")
    result = invoke(runner, "new", "x", "-o", target)
    assert result.exit_code == 2 and "--force" in result.text
    assert target.read_text(encoding="utf-8") == "keep me\n" and provider.calls == []
    assert invoke(runner, "new", "x", "-o", target, "--force").exit_code == 0
    assert target.read_text(encoding="utf-8").startswith(DRAFT_HEADER)


def refuse_provider(monkeypatch):
    def create(settings):
        raise AssertionError("the provider must not be created for an unusable output target")

    monkeypatch.setattr(cli, "_draft_provider", create)


def test_new_refuses_a_directory_as_output_before_calling_claude(runner, monkeypatch, tmp_path):
    """Review finding: -o naming a directory failed with a traceback after the paid call."""
    refuse_provider(monkeypatch)
    for extra in ((), ("--force",)):
        result = invoke(runner, "new", "x", "-o", tmp_path, *extra)
        assert result.exit_code == 2, result.text
        assert isinstance(result.exception, SystemExit)
        assert "is a directory" in result.text


def test_new_refuses_an_output_it_cannot_create_before_calling_claude(
    runner, monkeypatch, tmp_path
):
    refuse_provider(monkeypatch)
    blocker = tmp_path / "notes.txt"
    blocker.write_text("a file, not a directory\n", encoding="utf-8")
    result = invoke(runner, "new", "x", "-o", blocker / "sub" / "w.yaml")
    assert result.exit_code == 2 and isinstance(result.exception, SystemExit), result.text
    assert "not a directory" in result.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_new_refuses_an_unwritable_directory_before_calling_claude(runner, monkeypatch, tmp_path):
    refuse_provider(monkeypatch)
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        result = invoke(runner, "new", "x", "-o", locked / "w.yaml")
        assert result.exit_code == 2 and isinstance(result.exception, SystemExit), result.text
        assert "not writable" in result.text and str(locked) in result.text
    finally:
        locked.chmod(0o700)
    assert not (locked / "w.yaml").exists()


def test_new_reports_issues_when_the_draft_stays_invalid(runner, monkeypatch, tmp_path):
    bad = json.dumps({"yaml": "name: x\nsteps: []\n", "summary": ""})
    use_provider(monkeypatch, bad, bad, bad)
    target = tmp_path / "invoice.yaml"
    result = invoke(runner, "new", "x", "-o", target)
    assert result.exit_code == 2, result.text
    assert "after 3 attempt(s)" in result.text and "steps" in result.text
    assert not target.exists()


def test_new_without_credentials_writes_nothing(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "has_anthropic_credentials", lambda: False)
    target = tmp_path / "invoice.yaml"
    result = invoke(runner, "new", "x", "-o", target)
    assert result.exit_code == 1 and "ANTHROPIC_API_KEY" in result.text
    assert not target.exists()


def test_new_refuses_forced_mock_mode(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("CEREBELLUM_MOCK", "1")
    result = invoke(runner, "new", "x", "-o", tmp_path / "w.yaml")
    assert result.exit_code == 1 and "CEREBELLUM_MOCK" in result.text
