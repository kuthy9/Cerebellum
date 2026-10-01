# Phase 1 — Workflow Runtime + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old agent-DAG library with a YAML-defined business-workflow runtime (connectors, structured AI, validation, human approval, retry/fallback, crash-safe resume, tracing events) driven from a Rich CLI, demonstrated end-to-end by the refund workflow.

**Architecture:** An event-sourced SQLite store (append-only `events` + projections written in one transaction) is the single source of truth. A scheduler runs ready steps in parallel with asyncio, classifies failures (retryable vs not), applies backoff and fallbacks, and suspends the run (process exits) when it reaches a human approval; resume re-drives from the projections under a lease. Step executors talk to pluggable connectors (PostgreSQL/SQLite-sandbox, REST) and AI providers (Claude structured outputs, deterministic mock).

**Tech Stack:** Python ≥3.11, pydantic 2, PyYAML, Jinja2 (sandboxed), jsonschema, httpx, anthropic 1.x, FastAPI + uvicorn (sandbox payments API), Typer + Rich, SQLite (stdlib), optional psycopg 3, pytest + pytest-asyncio, ruff.

**Spec:** `docs/superpowers/specs/2026-10-01-workflow-runtime-design.md`

## Global Constraints

- Python `>=3.11`; src layout package `cerebellum`; console script `cerebellum = "cerebellum.cli.app:main"`.
- Default model `claude-opus-5-5`; mock AI when no Anthropic credentials; `CEREBELLUM_MOCK=1` forces mock.
- Data dir `CEREBELLUM_HOME` (default `./.cerebellum`), database file `cerebellum.db`.
- Sandbox payments API defaults: host `127.0.0.1`, port `8787` (`CEREBELLUM_SANDBOX_HOST` / `CEREBELLUM_SANDBOX_PORT`).
- Only the `connectors:` block supports `${VAR}` / `${VAR:-default}`; SQL text is never templated (bind params only).
- Every `http` step sends `Idempotency-Key: <run_id>:<step_id>` (same value on every attempt).
- Run ids are `r_` + 8 hex chars.
- CLI exit codes: `0` succeeded / needs_attention, `1` failed / rejected / runtime error, `2` invalid workflow or input, `3` waiting for approval.
- Terminal style: monochrome + cyan accent, glyphs `● ◐ ○ ✕ ↻ ⤳ ⊘ ⊗ ⏸`, no emoji.
- The user's CLAUDE.md forbids automatic commits: every "Checkpoint" step runs `git status --short` instead of committing; the user decides when to commit.
- Old modules (`src/engine.py`, `src/message_bus.py`, `src/scenarios.py`, `src/cerebellum.py`, `src/state_machine.py`, `src/_version.py`, `requirements.txt`) are deleted — approved in the spec (§1.5).

## Review Focus

1. **Payments API returns a non-JSON or empty body on an error** → the REST connector must not crash while decoding; the trace keeps the text body and the step is classified by status code. Test: Task 6 `test_rest_error_with_non_json_body`.
2. **Run input with wrong types (e.g. `"amount": "120"`)** → rejected before a run is created, with the field path in the message; CLI exits 2. Tests: Task 3 `test_validate_input_*`, Task 10 `test_bad_input_is_rejected_before_run_creation`, Task 13 `test_run_rejects_bad_input`.
3. **CLI and another process writing the same SQLite file at once** → no `database is locked` failures, no lost events. Test: Task 5 `test_concurrent_writers_on_one_database`.
4. **A human approves while the run is still executing another branch** → the decision is applied in the same drive instead of being lost when the run would otherwise suspend. Test: Task 11 `test_decision_during_execution_is_not_lost`.
5. **A template references the output of a skipped or missing step** → a non-retryable failure whose message names the template, not a crash or a retry storm. Tests: Task 2 `test_strict_render_of_missing_value_names_the_template`, Task 10 `test_template_error_fails_without_retry`.

## File Structure

```
pyproject.toml                         # packaging, deps, pytest + ruff config
Makefile                               # install / test / lint / fmt / demo
docker-compose.yml                     # optional real PostgreSQL seeded with the refund data
src/cerebellum/
  __init__.py, _version.py
  config.py                            # Settings (env-driven defaults), credential detection
  errors.py                            # error hierarchy (SpecError, StepError, BudgetExceeded, ...)
  spec/
    __init__.py                        # public API: Workflow, load_workflow, parse_workflow, ...
    durations.py                       # "500ms" / "30s" / "24h" → seconds
    expressions.py                     # sandboxed Jinja: render / eval_condition / checks
    schemas.py                         # strictify, schema_problems, validation_errors, minimal_instance
    models.py                          # pydantic workflow models
    loader.py                          # YAML → Workflow, env interpolation, semantic validation
    inputs.py                          # validate_input, resolve_params
  runtime/
    __init__.py
    states.py                          # StepStatus / RunStatus + legal transitions
    clock.py                           # Clock protocol, SystemClock, FakeClock
    store.py                           # SQLite event store + projections + leases
    context.py                         # expression context from projections
    retry.py                           # backoff_delay
    engine.py                          # Engine + scheduler (_Execution)
    trace.py                           # events → spans (CLI now, dashboard in phase 2)
  connectors/
    __init__.py                        # registry imports
    base.py                            # Connector ABCs, ConnectorError, registry, ConnectorPool
    postgres.py                        # psycopg connector + SQLite sandbox
    rest.py                            # httpx connector, header redaction
  ai/
    __init__.py                        # select_provider
    base.py                            # AIRequest / AIResult / Usage / AIError / AIProvider
    pricing.py                         # model prices, cost
    mock.py                            # deterministic mock provider
    anthropic_provider.py              # Claude structured outputs
  steps/
    __init__.py                        # EXECUTORS registry
    base.py                            # StepRuntime, elapsed_ms
    query.py, http.py, ai.py, validate.py, task.py
  sandbox/
    __init__.py
    payments.py                        # FastAPI mock payments API (fault injection, idempotency)
    server.py                          # start_sandbox in a background thread / reuse
  templates/
    __init__.py                        # template_path()
    refund/workflow.yaml, seed.sql, inputs/*.json
  cli/
    __init__.py
    render.py                          # Rich renderables
    demo.py                            # demo scenarios
    app.py                             # Typer commands
tests/
  conftest.py                          # shared fixtures (clock, settings, store, simple_workflow, free_port)
  test_config.py
  spec/test_durations.py, test_expressions.py, test_loader.py
  runtime/test_states.py, test_store.py, test_retry.py, test_engine.py, test_engine_hitl.py, test_trace.py
  connectors/test_connectors.py
  ai/test_ai.py
  steps/test_steps.py
  sandbox/test_sandbox.py
  integration/test_refund_template.py
  cli/test_cli.py
```

---

### Task 1: Project scaffold, configuration, errors, durations

**Files:**
- Create: `pyproject.toml`, `Makefile`, `src/cerebellum/__init__.py`, `src/cerebellum/_version.py`, `src/cerebellum/config.py`, `src/cerebellum/errors.py`, `src/cerebellum/spec/__init__.py`, `src/cerebellum/spec/durations.py`, `src/cerebellum/runtime/__init__.py`, `src/cerebellum/sandbox/__init__.py`, `src/cerebellum/cli/__init__.py`
- Modify: `.gitignore`
- Delete: `src/engine.py`, `src/message_bus.py`, `src/scenarios.py`, `src/cerebellum.py`, `src/state_machine.py`, `src/_version.py`, `requirements.txt`
- Test: `tests/test_config.py`, `tests/spec/test_durations.py`

**Interfaces:**
- Produces: `Settings.from_env(env) -> Settings` (fields `home, model, force_mock, pricing_file, sandbox_host, sandbox_port, lease_seconds`, property `db_path`, method `ensure_home()`); constants `DEFAULT_MODEL, DEFAULT_STEP_TIMEOUT_SECONDS, DEFAULT_MAX_PARALLEL, DEFAULT_SANDBOX_PORT, DEFAULT_UI_PORT, DEFAULT_LEASE_SECONDS`; `has_anthropic_credentials(env=None, config_dir=None) -> bool`; errors `CerebellumError, SpecIssue(path, message), SpecError(issues), TemplateError, StepError(message, *, retryable, kind="error", details=None), BudgetExceeded(budget_usd, spent_usd), InvalidTransition, RunNotFound, LeaseUnavailable`; `parse_duration(value) -> float`.

- [ ] **Step 1: Remove the old modules and create the package skeleton**

```bash
cd /Users/krisjiang/Desktop/Cerebellum
rm src/engine.py src/message_bus.py src/scenarios.py src/cerebellum.py src/state_machine.py src/_version.py requirements.txt
mkdir -p src/cerebellum/spec src/cerebellum/runtime src/cerebellum/sandbox src/cerebellum/cli tests/spec
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "cerebellum"
dynamic = ["version"]
description = "Reliable, observable, recoverable business workflows: data, LLMs, APIs and human approvals."
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
authors = [{ name = "Cerebellum Contributors" }]
dependencies = [
    "anthropic>=1.0",
    "fastapi>=0.110",
    "httpx>=0.27",
    "jinja2>=3.1",
    "jsonschema>=4.21",
    "pydantic>=2.7",
    "python-dotenv>=1.0",
    "pyyaml>=6.0",
    "rich>=13.7",
    "typer>=0.12",
    "uvicorn>=0.29",
]

[project.optional-dependencies]
postgres = ["psycopg[binary]>=3.1"]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.5"]

[project.scripts]
cerebellum = "cerebellum.cli.app:main"

[tool.hatch.version]
path = "src/cerebellum/_version.py"

[tool.hatch.build.targets.wheel]
packages = ["src/cerebellum"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
addopts = "-m 'not live' --import-mode=importlib"
markers = [
    "postgres: needs a PostgreSQL server (set CEREBELLUM_TEST_PG_DSN)",
    "live: calls the real Claude API (needs Anthropic credentials)",
]

[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = ["pathlib.Path", "typer.Argument", "typer.Option"]
```

`Makefile`:

```make
PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: install test lint fmt demo

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e ".[dev]"

test: lint
	$(BIN)/pytest -q

lint:
	$(BIN)/ruff format --check src tests
	$(BIN)/ruff check src tests

fmt:
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

demo:
	$(BIN)/cerebellum demo
```

Append to `.gitignore` (after the `*.db-wal` line):

```gitignore
# Cerebellum local data directory (run history, sandbox databases)
.cerebellum/

# Frontend
node_modules/
```

`src/cerebellum/_version.py`:

```python
"""Single source of truth for the package version."""

__version__ = "0.2.0"
```

`src/cerebellum/__init__.py`:

```python
"""Cerebellum: reliable, observable, recoverable business workflows."""

from cerebellum._version import __version__

__all__ = ["__version__"]
```

`src/cerebellum/spec/__init__.py`, `src/cerebellum/runtime/__init__.py`, `src/cerebellum/sandbox/__init__.py`, `src/cerebellum/cli/__init__.py` each contain one docstring line:

```python
"""Workflow definitions: models, loading, validation, expressions."""
```

```python
"""Workflow runtime: event store, scheduler, recovery."""
```

```python
"""Local sandbox services used by demos, tests and evals."""
```

```python
"""Command-line interface."""
```

- [ ] **Step 2: Create the virtualenv and install**

Run: `make install`
Expected: completes without errors; `.venv/bin/pip show cerebellum` reports version `0.2.0`.

- [ ] **Step 3: Write the failing tests**

`tests/test_config.py`:

```python
from pathlib import Path

from cerebellum.config import DEFAULT_MODEL, Settings, has_anthropic_credentials


def test_defaults_when_env_is_empty():
    settings = Settings.from_env({})
    assert settings.home == Path(".cerebellum")
    assert settings.model == DEFAULT_MODEL == "claude-opus-5-5"
    assert settings.force_mock is False
    assert settings.pricing_file is None
    assert settings.sandbox_host == "127.0.0.1"
    assert settings.sandbox_port == 8787
    assert settings.lease_seconds == 30.0
    assert settings.db_path == Path(".cerebellum/cerebellum.db")


def test_env_overrides(tmp_path):
    settings = Settings.from_env(
        {
            "CEREBELLUM_HOME": str(tmp_path),
            "CEREBELLUM_MODEL": "claude-sonnet-5-5",
            "CEREBELLUM_MOCK": "true",
            "CEREBELLUM_PRICING_FILE": str(tmp_path / "prices.json"),
            "CEREBELLUM_SANDBOX_PORT": "9999",
            "CEREBELLUM_LEASE_SECONDS": "5",
        }
    )
    assert settings.home == tmp_path
    assert settings.model == "claude-sonnet-5-5"
    assert settings.force_mock is True
    assert settings.pricing_file == tmp_path / "prices.json"
    assert settings.sandbox_port == 9999
    assert settings.lease_seconds == 5.0


def test_ensure_home_creates_directory(tmp_path):
    settings = Settings.from_env({"CEREBELLUM_HOME": str(tmp_path / "nested" / "home")})
    assert settings.ensure_home().is_dir()


def test_credentials_from_env(tmp_path):
    missing = tmp_path / "no-profile"
    assert has_anthropic_credentials({}, config_dir=missing) is False
    assert has_anthropic_credentials({"ANTHROPIC_API_KEY": "test-key"}, config_dir=missing)
    assert has_anthropic_credentials({"ANTHROPIC_AUTH_TOKEN": "t"}, config_dir=missing)


def test_credentials_from_cli_profile(tmp_path):
    (tmp_path / "config.toml").write_text("profile = 'default'\n")
    assert has_anthropic_credentials({}, config_dir=tmp_path) is True
```

`tests/spec/test_durations.py`:

```python
import pytest

from cerebellum.spec.durations import parse_duration


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("500ms", 0.5), ("30s", 30.0), ("5m", 300.0), ("24h", 86400.0), ("1d", 86400.0),
     (" 2.5s ", 2.5), (3, 3.0), (0.25, 0.25)],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value) == pytest.approx(seconds)


@pytest.mark.parametrize("value", ["", "10", "5 minutes", "-1s", -2, True, None, "1w"])
def test_parse_duration_rejects_invalid(value):
    with pytest.raises(ValueError):
        parse_duration(value)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py tests/spec/test_durations.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebellum.config'` / `cerebellum.spec.durations`.

- [ ] **Step 5: Implement**

`src/cerebellum/config.py`:

```python
"""Centralised configuration. Every default lives here and can be overridden by env vars."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOME = ".cerebellum"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_SANDBOX_HOST = "127.0.0.1"
DEFAULT_SANDBOX_PORT = 8787
DEFAULT_UI_PORT = 7400
DEFAULT_LEASE_SECONDS = 30.0
DEFAULT_STEP_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_PARALLEL = 8

_TRUTHY = {"1", "true", "yes", "on"}
_CREDENTIAL_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
)


@dataclass(frozen=True)
class Settings:
    home: Path
    model: str
    force_mock: bool
    pricing_file: Path | None
    sandbox_host: str
    sandbox_port: int
    lease_seconds: float

    @property
    def db_path(self) -> Path:
        return self.home / "cerebellum.db"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        pricing = env.get("CEREBELLUM_PRICING_FILE")
        return cls(
            home=Path(env.get("CEREBELLUM_HOME", DEFAULT_HOME)).expanduser(),
            model=env.get("CEREBELLUM_MODEL", DEFAULT_MODEL),
            force_mock=env.get("CEREBELLUM_MOCK", "").strip().lower() in _TRUTHY,
            pricing_file=Path(pricing).expanduser() if pricing else None,
            sandbox_host=env.get("CEREBELLUM_SANDBOX_HOST", DEFAULT_SANDBOX_HOST),
            sandbox_port=int(env.get("CEREBELLUM_SANDBOX_PORT", DEFAULT_SANDBOX_PORT)),
            lease_seconds=float(env.get("CEREBELLUM_LEASE_SECONDS", DEFAULT_LEASE_SECONDS)),
        )

    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        return self.home


def has_anthropic_credentials(
    env: Mapping[str, str] | None = None, config_dir: Path | None = None
) -> bool:
    """True when the Anthropic SDK can plausibly resolve credentials (env vars or a CLI profile)."""
    env = os.environ if env is None else env
    if any(env.get(name) for name in _CREDENTIAL_ENV_VARS):
        return True
    config_dir = config_dir or Path.home() / ".config" / "anthropic"
    return config_dir.is_dir() and any(config_dir.iterdir())
```

`src/cerebellum/errors.py`:

```python
"""Error hierarchy shared across Cerebellum."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CerebellumError(Exception):
    """Base class for all Cerebellum errors."""


@dataclass(frozen=True)
class SpecIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class SpecError(CerebellumError):
    """A workflow definition (or run input) failed validation."""

    def __init__(self, issues: list[SpecIssue]):
        self.issues = issues
        super().__init__("; ".join(str(issue) for issue in issues))


class TemplateError(CerebellumError):
    """An expression or template could not be compiled or rendered."""


class StepError(CerebellumError):
    """A step failed. `retryable` drives the retry policy; `kind` classifies the failure."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        kind: str = "error",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind
        self.details = details or {}


class BudgetExceeded(StepError):
    def __init__(self, budget_usd: float, spent_usd: float):
        super().__init__(
            f"budget ${budget_usd:.4f} exceeded (spent ${spent_usd:.4f})",
            retryable=False,
            kind="budget",
            details={"budget_usd": budget_usd, "spent_usd": spent_usd},
        )


class InvalidTransition(CerebellumError):
    """A state change that the state machine does not allow."""


class RunNotFound(CerebellumError):
    """No run with the given id exists."""


class LeaseUnavailable(CerebellumError):
    """Another process currently owns the run."""
```

`src/cerebellum/spec/durations.py`:

```python
"""Parse human-friendly durations such as 500ms, 30s, 5m, 24h."""

from __future__ import annotations

import re

_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)\s*$")


def parse_duration(value: object) -> float:
    """Return seconds. Plain numbers are treated as seconds."""
    if isinstance(value, bool):
        raise ValueError(f"invalid duration {value!r}")
    if isinstance(value, int | float):
        if value < 0:
            raise ValueError(f"duration must be >= 0, got {value!r}")
        return float(value)
    if isinstance(value, str):
        match = _PATTERN.match(value)
        if match:
            return float(match.group(1)) * _UNITS[match.group(2)]
    raise ValueError(f"invalid duration {value!r}; use e.g. 500ms, 30s, 5m, 24h")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py tests/spec/test_durations.py -q`
Expected: all PASS.

- [ ] **Step 7: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`
Expected: lint clean; status lists the deleted old files and the new scaffold.

---

### Task 2: Sandboxed expressions and templates

**Files:**
- Create: `src/cerebellum/spec/expressions.py`
- Test: `tests/spec/test_expressions.py`

**Interfaces:**
- Consumes: `TemplateError` (Task 1).
- Produces: `is_template(text) -> bool`, `check_expression(expr) -> None`, `check_template(value) -> None`, `eval_condition(expr, ctx) -> bool` (lenient, missing ⇒ False), `eval_value(expr, ctx) -> Any` (lenient, missing ⇒ None), `render(value, ctx, *, strict=True) -> Any` (recursive; single `{{ }}` keeps native type; strict missing ⇒ `TemplateError` naming the template).

- [ ] **Step 1: Write the failing tests**

`tests/spec/test_expressions.py`:

```python
import pytest

from cerebellum.errors import TemplateError
from cerebellum.spec.expressions import (
    check_expression,
    check_template,
    eval_condition,
    eval_value,
    is_template,
    render,
)

CTX = {
    "input": {"order_id": "A1001", "amount": 120, "reason": "Arrived BROKEN"},
    "params": {"approval_threshold": 500},
    "steps": {
        "fetch": {
            "output": {"id": "A1001", "amount": 120.0, "items": ["x", "y"]},
            "status": "succeeded",
        },
        "skipped": {"output": None, "status": "skipped"},
    },
}


def test_is_template():
    assert is_template("{{ a }}") and is_template("{% if a %}x{% endif %}")
    assert not is_template("plain text")


def test_single_expression_keeps_native_type():
    assert render("{{ input.amount }}", CTX) == 120
    assert render("{{ steps.fetch.output }}", CTX) == {
        "id": "A1001",
        "amount": 120.0,
        "items": ["x", "y"],
    }
    assert render("  {{ input.amount > 100 }} ", CTX) is True


def test_mixed_string_renders_text_and_json_for_containers():
    assert render("Order {{ input.order_id }}", CTX) == "Order A1001"
    assert render("data={{ steps.fetch.output.items }}", CTX) == 'data=["x", "y"]'
    assert render("{{ input.order_id }}-{{ input.amount }}", CTX) == "A1001-120"


def test_render_recurses_into_dicts_and_lists():
    value = {"a": ["{{ input.amount }}", "x"], "b": 1, "c": None}
    assert render(value, CTX) == {"a": [120, "x"], "b": 1, "c": None}


def test_mapping_keys_win_over_dict_methods():
    assert render("{{ steps.fetch.output.items }}", CTX) == ["x", "y"]


def test_strict_render_of_missing_value_names_the_template():
    with pytest.raises(TemplateError, match=r"steps\.skipped\.output\.id"):
        render("{{ steps.skipped.output.id }}", CTX)
    with pytest.raises(TemplateError, match=r"cannot render 'id=\{\{ input.nope \}\}'"):
        render("id={{ input.nope }}", CTX)


def test_lenient_render_turns_missing_into_none_or_empty():
    assert render("{{ steps.skipped.output.id }}", CTX, strict=False) is None
    assert render("id={{ input.nope }}", CTX, strict=False) == "id="


def test_conditions():
    assert eval_condition("input.amount > params.approval_threshold", CTX) is False
    assert eval_condition("input.amount <= steps.fetch.output.amount", CTX) is True
    assert eval_condition("'broken' in (input.reason or '') | lower", CTX) is True
    assert eval_condition("steps.fetch.status == 'succeeded'", CTX) is True


def test_condition_with_missing_values_is_false():
    assert eval_condition("steps.skipped.output.eligible", CTX) is False
    assert eval_condition("steps.skipped.output.amount > 5", CTX) is False
    assert eval_condition("steps.skipped.output is none", CTX) is True


def test_eval_value_is_lenient():
    assert eval_value("steps.fetch.output.id", CTX) == "A1001"
    assert eval_value("steps.skipped.output.id", CTX) is None


def test_check_expression_reports_syntax_errors():
    check_expression("input.amount > 5")
    with pytest.raises(TemplateError, match="invalid expression"):
        check_expression("input.amount >")


def test_check_template_recurses():
    check_template({"a": ["{{ input.x }}", "plain"], "b": 3})
    with pytest.raises(TemplateError, match="invalid template"):
        check_template({"a": ["{{ input.x "]})


def test_sandbox_blocks_dunder_access():
    assert eval_value("''.__class__.__mro__", {}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/spec/test_expressions.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebellum.spec.expressions'`.

- [ ] **Step 3: Implement**

`src/cerebellum/spec/expressions.py`:

```python
"""Sandboxed Jinja2 expressions and templates.

Two evaluation modes:
* conditions (`when`, validate rules, eval assertions) are lenient: a missing value is falsy and
  an expression that cannot be evaluated because of a missing value is False;
* value templates (params, body, prompt, ...) are strict: referencing a missing value raises
  TemplateError naming the template.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from jinja2 import (
    ChainableUndefined,
    StrictUndefined,
    TemplateSyntaxError,
    Undefined,
    UndefinedError,
)
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

from cerebellum.errors import TemplateError

_SINGLE_EXPR = re.compile(r"^\s*\{\{((?:(?!\{\{|\}\}).)+)\}\}\s*$", re.S)


def _finalize(value: Any) -> Any:
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


class _Env(SandboxedEnvironment):
    """Mappings resolve `a.b` as a key lookup, so data keys like `items` never hit dict methods."""

    def getattr(self, obj: Any, attribute: str) -> Any:
        if isinstance(obj, Mapping):
            try:
                return obj[attribute]
            except KeyError:
                return self.undefined(obj=obj, name=attribute)
        return super().getattr(obj, attribute)


def _make_env(undefined: type[Undefined]) -> _Env:
    env = _Env(undefined=undefined, autoescape=False, finalize=_finalize)
    env.globals.update(len=len, min=min, max=max, abs=abs, round=round)
    return env


_STRICT = _make_env(StrictUndefined)
_LENIENT = _make_env(ChainableUndefined)


def is_template(text: str) -> bool:
    return "{{" in text or "{%" in text


def check_expression(expr: str) -> None:
    try:
        _LENIENT.compile_expression(expr)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"invalid expression {expr!r}: {exc.message}") from exc


def check_template(value: Any) -> None:
    """Recursively compile every template string inside `value`."""
    if isinstance(value, str):
        if is_template(value):
            try:
                _LENIENT.from_string(value)
            except TemplateSyntaxError as exc:
                raise TemplateError(f"invalid template {value!r}: {exc.message}") from exc
    elif isinstance(value, Mapping):
        for item in value.values():
            check_template(item)
    elif isinstance(value, list):
        for item in value:
            check_template(item)


def eval_condition(expr: str, ctx: Mapping[str, Any]) -> bool:
    try:
        result = _LENIENT.compile_expression(expr, undefined_to_none=False)(**ctx)
    except UndefinedError:
        return False
    except TemplateSyntaxError as exc:
        raise TemplateError(f"invalid expression {expr!r}: {exc.message}") from exc
    except SecurityError as exc:
        raise TemplateError(f"unsafe expression {expr!r}: {exc}") from exc
    return bool(result)


def eval_value(expr: str, ctx: Mapping[str, Any]) -> Any:
    """Lenient value evaluation: missing values become None."""
    try:
        result = _LENIENT.compile_expression(expr, undefined_to_none=False)(**ctx)
    except UndefinedError:
        return None
    except (TemplateSyntaxError, SecurityError) as exc:
        raise TemplateError(f"cannot evaluate {expr!r}: {exc}") from exc
    return None if isinstance(result, Undefined) else result


def render(value: Any, ctx: Mapping[str, Any], *, strict: bool = True) -> Any:
    """Render templates inside `value` recursively. A string that is exactly one `{{ expr }}`
    keeps the native type of the expression result."""
    if isinstance(value, str):
        return _render_str(value, ctx, strict)
    if isinstance(value, Mapping):
        return {key: render(item, ctx, strict=strict) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, ctx, strict=strict) for item in value]
    return value


def _render_str(text: str, ctx: Mapping[str, Any], strict: bool) -> Any:
    if not is_template(text):
        return text
    env = _STRICT if strict else _LENIENT
    single = _SINGLE_EXPR.match(text)
    try:
        if single:
            result = env.compile_expression(single.group(1), undefined_to_none=False)(**ctx)
            if isinstance(result, Undefined):
                if strict:
                    raise TemplateError(f"cannot render {text!r}: value is undefined")
                return None
            return result
        return env.from_string(text).render(**ctx)
    except UndefinedError as exc:
        if strict:
            raise TemplateError(f"cannot render {text!r}: {exc.message}") from exc
        return None
    except (TemplateSyntaxError, SecurityError) as exc:
        raise TemplateError(f"cannot render {text!r}: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/spec/test_expressions.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 3: Workflow models, loader, semantic validation, inputs

**Files:**
- Create: `src/cerebellum/spec/schemas.py`, `src/cerebellum/spec/models.py`, `src/cerebellum/spec/loader.py`, `src/cerebellum/spec/inputs.py`
- Modify: `src/cerebellum/spec/__init__.py`
- Test: `tests/spec/test_schemas.py`, `tests/spec/test_loader.py`

**Interfaces:**
- Consumes: `parse_duration` (Task 1), expression checks (Task 2), `SpecError/SpecIssue/TemplateError`, config constants.
- Produces:
  - `schemas.strictify(schema) -> dict`, `schemas.schema_problems(schema) -> list[str]`, `schemas.validation_errors(schema, instance) -> list[str]`, `schemas.minimal_instance(schema) -> Any`.
  - Models: `RetryPolicy(max, backoff, base, max_delay)`, `OnFailure(fallback)`, steps `QueryStep, HttpStep, AiStep, ValidateStep, ApprovalStep, TaskStep` (common fields `id, description, needs, when, timeout, retry, on_failure`), `MockRule(when, output)`, `ValidationRule(expr, message)`, `InputField`, `PostgresConnectorSpec(type, dsn, seed)`, `RestConnectorSpec(type, base_url, headers, timeout, health_path)`, `Limits(budget_usd, max_parallel)`, `Workflow` with fields above plus `source_yaml, base_dir, digest`, methods `step(id)`, `is_fallback(id)`, properties `step_ids`, `fallback_ids`.
  - `load_workflow(path, *, env=None) -> Workflow`, `parse_workflow(text, *, base_dir=".", env=None) -> Workflow`, `semantic_issues(wf) -> list[SpecIssue]`.
  - `validate_input(wf, data) -> dict`, `resolve_params(wf, overrides=None) -> dict`.

- [ ] **Step 1: Write the failing tests**

`tests/spec/test_schemas.py`:

```python
from cerebellum.spec.schemas import (
    minimal_instance,
    schema_problems,
    strictify,
    validation_errors,
)

SCHEMA = {
    "type": "object",
    "required": ["eligible", "risk", "tags"],
    "properties": {
        "eligible": {"type": "boolean"},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "tags": {"type": "array", "items": {"type": "object", "properties": {"k": {"type": "string"}}}},
        "score": {"type": "number", "minimum": 0.5},
    },
}


def test_strictify_adds_additional_properties_recursively_without_mutating():
    strict = strictify(SCHEMA)
    assert strict["additionalProperties"] is False
    assert strict["properties"]["tags"]["items"]["additionalProperties"] is False
    assert "additionalProperties" not in SCHEMA


def test_strictify_keeps_explicit_value():
    assert strictify({"type": "object", "additionalProperties": True})["additionalProperties"]


def test_schema_problems():
    assert schema_problems(SCHEMA) == []
    assert schema_problems({"type": "string"}) == [
        "output_schema must have type: object at the root"
    ]
    assert schema_problems({"type": "object", "properties": {"a": {"type": 5}}})


def test_validation_errors_lists_paths():
    errors = validation_errors(strictify(SCHEMA), {"eligible": "yes", "risk": "low", "tags": []})
    assert errors == ["eligible: 'yes' is not of type 'boolean'"]
    assert validation_errors(SCHEMA, {"eligible": True, "risk": "low", "tags": []}) == []


def test_minimal_instance_satisfies_schema():
    value = minimal_instance(strictify(SCHEMA))
    assert value == {"eligible": False, "risk": "low", "tags": []}
    assert validation_errors(strictify(SCHEMA), value) == []
    assert minimal_instance({"type": "integer", "minimum": 3}) == 3
    assert minimal_instance({"type": "string", "minLength": 2}) == "xx"
    assert minimal_instance({"anyOf": [{"type": "null"}, {"type": "string"}]}) is None
```

`tests/spec/test_loader.py`:

```python
import copy

import pytest
import yaml

from cerebellum.errors import SpecError
from cerebellum.spec import load_workflow, parse_workflow, resolve_params, validate_input
from cerebellum.spec.models import AiStep, HttpStep

BASE = {
    "name": "demo",
    "params": {"threshold": 100},
    "input": {
        "order_id": {"type": "string", "required": True},
        "amount": {"type": "number", "required": True},
        "tier": {"type": "string", "enum": ["gold", "silver"]},
    },
    "connectors": {
        "db": {"type": "postgres", "dsn": "sandbox"},
        "api": {"type": "rest", "base_url": "${API_URL:-http://127.0.0.1:9}"},
    },
    "steps": [
        {"id": "load", "type": "query", "connector": "db", "sql": "SELECT 1 AS one",
         "expect": "one"},
        {"id": "check", "type": "validate", "needs": ["load"],
         "rules": [{"expr": "steps.load.output.one == 1", "message": "one must be 1"}]},
        {"id": "call", "type": "http", "needs": ["check"], "connector": "api",
         "method": "POST", "path": "/x", "body": {"id": "{{ input.order_id }}"},
         "retry": {"max": 2, "base": "200ms"}, "on_failure": {"fallback": "manual"}},
    ],
    "fallbacks": [{"id": "manual", "type": "task", "title": "Handle {{ input.order_id }}"}],
    "output": {"code": "{{ steps.call.output.status }}"},
}


def dump(data):
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def variant(mutate):
    data = copy.deepcopy(BASE)
    mutate(data)
    return dump(data)


def issues_of(text, env=None):
    with pytest.raises(SpecError) as info:
        parse_workflow(text, env=env or {})
    return [str(issue) for issue in info.value.issues]


def test_parse_valid_workflow(tmp_path):
    wf = parse_workflow(dump(BASE), base_dir=tmp_path, env={})
    assert wf.name == "demo"
    assert wf.step_ids == ["load", "check", "call"]
    assert wf.fallback_ids == ["manual"]
    assert wf.is_fallback("manual") and not wf.is_fallback("call")
    assert wf.connectors["api"].base_url == "http://127.0.0.1:9"
    call = wf.step("call")
    assert isinstance(call, HttpStep)
    assert call.retry.base == pytest.approx(0.2)
    assert call.timeout == 30.0
    assert len(wf.digest) == 16
    assert wf.base_dir == str(tmp_path)
    assert wf.source_yaml.startswith("name: demo")


def test_digest_depends_on_text_and_base_dir(tmp_path):
    a = parse_workflow(dump(BASE), base_dir=tmp_path, env={})
    b = parse_workflow(dump(BASE), base_dir=tmp_path / "other", env={})
    assert a.digest != b.digest


def test_load_workflow_from_file(tmp_path):
    path = tmp_path / "wf.yaml"
    path.write_text(dump(BASE), encoding="utf-8")
    wf = load_workflow(path, env={"API_URL": "https://payments.example"})
    assert wf.connectors["api"].base_url == "https://payments.example"
    assert wf.base_dir == str(tmp_path.resolve())


def test_missing_file_is_a_spec_error(tmp_path):
    with pytest.raises(SpecError, match="cannot read file"):
        load_workflow(tmp_path / "missing.yaml")


def test_missing_env_var_without_default():
    text = variant(lambda d: d["connectors"]["api"].update(base_url="${PAYMENTS_URL}"))
    assert issues_of(text) == [
        "connectors.api.base_url: environment variable PAYMENTS_URL is not set"
    ]


def test_invalid_yaml():
    assert issues_of("name: [unclosed")[0].startswith("<yaml>:")


def test_root_must_be_mapping():
    assert issues_of("- a\n- b\n") == ["<root>: workflow must be a YAML mapping"]


def test_structural_error_has_a_path():
    def drop_sql(d):
        del d["steps"][0]["sql"]

    assert any(issue.startswith("steps[0]") and "sql" in issue for issue in issues_of(variant(drop_sql)))


def test_duplicate_ids():
    text = variant(lambda d: d["fallbacks"].append({"id": "load", "type": "task", "title": "x"}))
    assert "fallbacks[1].id: duplicate step id 'load'" in issues_of(text)


def test_unknown_dependency():
    text = variant(lambda d: d["steps"][1].update(needs=["nope"]))
    assert "steps[1].needs: unknown step 'nope'" in issues_of(text)


def test_dependency_on_fallback_is_rejected():
    text = variant(lambda d: d["steps"][1].update(needs=["manual"]))
    assert "steps[1].needs: 'manual' is a fallback step and cannot be a dependency" in issues_of(
        text
    )


def test_cycle_is_rejected():
    text = variant(lambda d: d["steps"][0].update(needs=["call"]))
    assert any(issue.startswith("steps: dependency cycle") for issue in issues_of(text))


def test_unknown_fallback():
    text = variant(lambda d: d["steps"][2].update(on_failure={"fallback": "ghost"}))
    assert any("unknown fallback 'ghost'" in issue for issue in issues_of(text))


def test_fallback_restrictions():
    def mutate(d):
        d["fallbacks"][0].update(needs=["load"], when="true")

    issues = issues_of(variant(mutate))
    assert "fallbacks[0].needs: fallback steps cannot declare needs" in issues
    assert "fallbacks[0].when: fallback steps cannot declare when" in issues


def test_fallback_used_twice():
    text = variant(lambda d: d["steps"][1].update(on_failure={"fallback": "manual"}))
    assert any("already used by step 'check'" in issue for issue in issues_of(text))


def test_connector_type_mismatch():
    text = variant(lambda d: d["steps"][0].update(connector="api"))
    assert "steps[0].connector: step type 'query' needs a 'postgres' connector, 'api' is 'rest'" in (
        issues_of(text)
    )


def test_unknown_connector():
    text = variant(lambda d: d["steps"][0].update(connector="warehouse"))
    assert "steps[0].connector: unknown connector 'warehouse'" in issues_of(text)


def test_templated_sql_is_rejected():
    text = variant(lambda d: d["steps"][0].update(sql="SELECT * FROM t WHERE id = '{{ input.order_id }}'"))
    assert any("bind parameters" in issue for issue in issues_of(text))


def test_bad_expression_reports_path():
    text = variant(lambda d: d["steps"][1]["rules"][0].update(expr="steps.load.output >"))
    issues = issues_of(text)
    assert any(issue.startswith("steps[1].rules[0].expr: invalid expression") for issue in issues)


def test_approval_restrictions():
    def mutate(d):
        d["steps"].append(
            {"id": "gate", "type": "approval", "needs": ["check"], "title": "ok?",
             "show": ["ghost"], "retry": {"max": 1}}
        )

    issues = issues_of(variant(mutate))
    assert "steps[3]: approval steps cannot declare retry or on_failure" in issues
    assert "steps[3].show: unknown step 'ghost'" in issues


def test_ai_schema_is_checked_and_strictified():
    ai_step = {
        "id": "judge", "type": "ai", "needs": ["check"], "prompt": "Judge {{ input.order_id }}",
        "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    }
    wf = parse_workflow(variant(lambda d: d["steps"].append(ai_step)), env={})
    judge = wf.step("judge")
    assert isinstance(judge, AiStep)
    assert judge.output_schema["additionalProperties"] is False

    bad = dict(ai_step, output_schema={"type": "string"})
    issues = issues_of(variant(lambda d: d["steps"].append(bad)))
    assert "steps[3].output_schema: output_schema must have type: object at the root" in issues


def test_output_templates_are_checked():
    text = variant(lambda d: d["output"].update(code="{{ broken "))
    assert any(issue.startswith("output.code: invalid template") for issue in issues_of(text))


def test_validate_input_accepts_valid_data():
    wf = parse_workflow(dump(BASE), env={})
    data = {"order_id": "A1", "amount": 12.5, "tier": "gold", "extra": True}
    assert validate_input(wf, data) == data


def test_validate_input_reports_every_problem():
    wf = parse_workflow(dump(BASE), env={})
    with pytest.raises(SpecError) as info:
        validate_input(wf, {"amount": "120", "tier": "bronze"})
    assert [str(i) for i in info.value.issues] == [
        "input.order_id: is required",
        "input.amount: expected number, got str '120'",
        "input.tier: must be one of ['gold', 'silver']",
    ]


def test_validate_input_rejects_bool_for_number_and_non_objects():
    wf = parse_workflow(dump(BASE), env={})
    with pytest.raises(SpecError, match="expected number, got bool"):
        validate_input(wf, {"order_id": "A1", "amount": True})
    with pytest.raises(SpecError, match="must be a JSON object"):
        validate_input(wf, ["not", "an", "object"])


def test_resolve_params():
    wf = parse_workflow(dump(BASE), env={})
    assert resolve_params(wf) == {"threshold": 100}
    assert resolve_params(wf, {"threshold": 900}) == {"threshold": 900}
    with pytest.raises(SpecError, match="params.unknown: is not declared"):
        resolve_params(wf, {"unknown": 1})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/spec/test_schemas.py tests/spec/test_loader.py -q`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` for `cerebellum.spec.schemas` and `load_workflow`.

- [ ] **Step 3: Implement `schemas.py`**

`src/cerebellum/spec/schemas.py`:

```python
"""JSON Schema helpers for structured AI outputs."""

from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

_NESTED_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")
_NESTED_MAPS = ("properties", "$defs", "definitions", "patternProperties")


def strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy of `schema` where every object forbids additional properties (Claude structured
    outputs require it). Explicit `additionalProperties` values are kept."""
    result = copy.deepcopy(schema)
    _strictify_in_place(result)
    return result


def _strictify_in_place(node: Any) -> None:
    if not isinstance(node, dict):
        return
    if node.get("type") == "object" or "properties" in node:
        node.setdefault("additionalProperties", False)
    for key in _NESTED_MAPS:
        children = node.get(key)
        if isinstance(children, dict):
            for child in children.values():
                _strictify_in_place(child)
    for key in _NESTED_LISTS:
        children = node.get(key)
        if isinstance(children, list):
            for child in children:
                _strictify_in_place(child)
    if isinstance(node.get("items"), dict):
        _strictify_in_place(node["items"])


def schema_problems(schema: dict[str, Any]) -> list[str]:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return [exc.message]
    if schema.get("type") != "object":
        return ["output_schema must have type: object at the root"]
    return []


def validation_errors(schema: dict[str, Any], instance: Any) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: [str(p) for p in e.absolute_path])
    return [_format(error) for error in errors]


def _format(error: Any) -> str:
    path = "/".join(str(part) for part in error.absolute_path)
    return f"{path or '<root>'}: {error.message}"


def minimal_instance(schema: dict[str, Any]) -> Any:
    """Deterministic smallest value that satisfies the common schema keywords."""
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            return minimal_instance(schema[key][0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        return {name: minimal_instance(properties.get(name, {})) for name in schema.get("required", [])}
    if kind == "array":
        return [minimal_instance(schema.get("items", {})) for _ in range(schema.get("minItems", 0))]
    if kind == "string":
        return "x" * schema.get("minLength", 0)
    if kind in ("number", "integer"):
        if "minimum" in schema:
            value = schema["minimum"]
        elif "exclusiveMinimum" in schema:
            value = schema["exclusiveMinimum"] + 1
        else:
            value = 0
        return int(value) if kind == "integer" else value
    if kind == "boolean":
        return False
    return None
```

- [ ] **Step 4: Implement `models.py`**

`src/cerebellum/spec/models.py`:

```python
"""Pydantic models for workflow definitions."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from cerebellum.config import DEFAULT_MAX_PARALLEL, DEFAULT_STEP_TIMEOUT_SECONDS
from cerebellum.spec.durations import parse_duration
from cerebellum.spec.schemas import strictify

Duration = Annotated[float, BeforeValidator(parse_duration)]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryPolicy(_Model):
    max: int = Field(0, ge=0, le=20)
    backoff: Literal["fixed", "exponential"] = "exponential"
    base: Duration = 1.0
    max_delay: Duration = 30.0


class OnFailure(_Model):
    fallback: str


class _StepBase(_Model):
    id: Identifier
    description: str = ""
    needs: list[str] = Field(default_factory=list)
    when: str | None = None
    timeout: Duration = DEFAULT_STEP_TIMEOUT_SECONDS
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    on_failure: OnFailure | None = None


class QueryStep(_StepBase):
    type: Literal["query"]
    connector: str
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    expect: Literal["one", "many", "none", "any"] = "any"


class HttpStep(_StepBase):
    type: Literal["http"]
    connector: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str
    body: Any = None
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)


class MockRule(_Model):
    when: str | None = None
    output: Any


class AiStep(_StepBase):
    type: Literal["ai"]
    prompt: str
    system: str | None = None
    output_schema: dict[str, Any]
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    max_tokens: int = Field(4096, ge=1, le=128000)
    max_repairs: int = Field(2, ge=0, le=5)
    mock: list[MockRule] = Field(default_factory=list)

    @field_validator("output_schema")
    @classmethod
    def _strict_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return strictify(value)


class ValidationRule(_Model):
    expr: str
    message: str


class ValidateStep(_StepBase):
    type: Literal["validate"]
    rules: list[ValidationRule] = Field(min_length=1)


class ApprovalStep(_StepBase):
    type: Literal["approval"]
    title: str
    show: list[str] = Field(default_factory=list)
    timeout: Duration | None = None  # how long to wait for a human; None waits forever
    on_timeout: Literal["approve", "reject"] = "reject"


class TaskStep(_StepBase):
    type: Literal["task"]
    title: str
    assignee: str = "unassigned"
    payload: dict[str, Any] = Field(default_factory=dict)


Step = Annotated[
    QueryStep | HttpStep | AiStep | ValidateStep | ApprovalStep | TaskStep,
    Field(discriminator="type"),
]


class InputField(_Model):
    type: Literal["string", "number", "integer", "boolean", "object", "array"] = "string"
    required: bool = False
    enum: list[Any] | None = None
    description: str = ""


class PostgresConnectorSpec(_Model):
    type: Literal["postgres"]
    dsn: str
    seed: str | None = None


class RestConnectorSpec(_Model):
    type: Literal["rest"]
    base_url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: Duration = 10.0
    health_path: str = "/health"


ConnectorSpec = Annotated[PostgresConnectorSpec | RestConnectorSpec, Field(discriminator="type")]


class Limits(_Model):
    budget_usd: float | None = Field(None, gt=0)
    max_parallel: int = Field(DEFAULT_MAX_PARALLEL, ge=1, le=64)


class Workflow(_Model):
    name: Identifier
    version: int = Field(1, ge=1)
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, InputField] = Field(default_factory=dict)
    connectors: dict[str, ConnectorSpec] = Field(default_factory=dict)
    limits: Limits = Field(default_factory=Limits)
    steps: list[Step] = Field(min_length=1)
    fallbacks: list[Step] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)

    # Filled in by the loader; not part of the YAML surface.
    source_yaml: str = Field("", exclude=True)
    base_dir: str = Field(".", exclude=True)
    digest: str = Field("", exclude=True)

    def step(self, step_id: str) -> Step:
        for step in (*self.steps, *self.fallbacks):
            if step.id == step_id:
                return step
        raise KeyError(step_id)

    @property
    def step_ids(self) -> list[str]:
        return [step.id for step in self.steps]

    @property
    def fallback_ids(self) -> list[str]:
        return [step.id for step in self.fallbacks]

    def is_fallback(self, step_id: str) -> bool:
        return step_id in self.fallback_ids
```

- [ ] **Step 5: Implement `loader.py` and `inputs.py`**

`src/cerebellum/spec/loader.py`:

```python
"""Load workflow YAML, interpolate connector env vars, validate structure and semantics."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from cerebellum.errors import SpecError, SpecIssue, TemplateError
from cerebellum.spec.expressions import check_expression, check_template, is_template
from cerebellum.spec.models import (
    AiStep,
    ApprovalStep,
    HttpStep,
    QueryStep,
    TaskStep,
    ValidateStep,
    Workflow,
)
from cerebellum.spec.schemas import schema_problems

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_CONNECTOR_TYPES = {"query": "postgres", "http": "rest"}


def load_workflow(path: str | Path, *, env: Mapping[str, str] | None = None) -> Workflow:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError([SpecIssue(str(path), f"cannot read file: {exc.strerror}")]) from exc
    return parse_workflow(text, base_dir=path.resolve().parent, env=env)


def parse_workflow(
    text: str, *, base_dir: str | Path = ".", env: Mapping[str, str] | None = None
) -> Workflow:
    env = os.environ if env is None else env
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError([SpecIssue("<yaml>", str(exc))]) from exc
    if not isinstance(data, dict):
        raise SpecError([SpecIssue("<root>", "workflow must be a YAML mapping")])

    issues: list[SpecIssue] = []
    if "connectors" in data:
        data["connectors"] = _interpolate(data["connectors"], env, "connectors", issues)
    if issues:
        raise SpecError(issues)

    try:
        workflow = Workflow.model_validate(data)
    except ValidationError as exc:
        raise SpecError([SpecIssue(_loc(err["loc"]), err["msg"]) for err in exc.errors()]) from exc

    issues = semantic_issues(workflow)
    if issues:
        raise SpecError(issues)

    base = str(Path(base_dir))
    workflow.source_yaml = text
    workflow.base_dir = base
    workflow.digest = hashlib.sha256(f"{base}\0{text}".encode()).hexdigest()[:16]
    return workflow


def _loc(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out or "<root>"


def _interpolate(value: Any, env: Mapping[str, str], path: str, issues: list[SpecIssue]) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if env.get(name):
                return env[name]
            if default is not None:
                return default
            issues.append(SpecIssue(path, f"environment variable {name} is not set"))
            return ""

        return _ENV_REF.sub(replace, value)
    if isinstance(value, dict):
        return {key: _interpolate(item, env, f"{path}.{key}", issues) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, env, f"{path}[{i}]", issues) for i, item in enumerate(value)]
    return value


def semantic_issues(wf: Workflow) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    located = [(f"steps[{i}]", step, False) for i, step in enumerate(wf.steps)] + [
        (f"fallbacks[{i}]", step, True) for i, step in enumerate(wf.fallbacks)
    ]
    main = set(wf.step_ids)
    fallbacks = set(wf.fallback_ids)
    all_ids = main | fallbacks

    seen: set[str] = set()
    for path, step, _ in located:
        if step.id in seen:
            issues.append(SpecIssue(f"{path}.id", f"duplicate step id '{step.id}'"))
        seen.add(step.id)

    fallback_users: dict[str, str] = {}
    for path, step, is_fallback in located:
        if is_fallback:
            if step.needs:
                issues.append(SpecIssue(f"{path}.needs", "fallback steps cannot declare needs"))
            if step.when:
                issues.append(SpecIssue(f"{path}.when", "fallback steps cannot declare when"))
            if step.on_failure:
                issues.append(
                    SpecIssue(f"{path}.on_failure", "fallback steps cannot declare on_failure")
                )
        else:
            for dep in step.needs:
                if dep == step.id:
                    issues.append(SpecIssue(f"{path}.needs", "a step cannot depend on itself"))
                elif dep in fallbacks:
                    issues.append(
                        SpecIssue(
                            f"{path}.needs",
                            f"'{dep}' is a fallback step and cannot be a dependency",
                        )
                    )
                elif dep not in main:
                    issues.append(SpecIssue(f"{path}.needs", f"unknown step '{dep}'"))
            if step.on_failure:
                target = step.on_failure.fallback
                if target not in fallbacks:
                    issues.append(
                        SpecIssue(
                            f"{path}.on_failure.fallback",
                            f"unknown fallback '{target}' (declare it under fallbacks:)",
                        )
                    )
                elif target in fallback_users:
                    issues.append(
                        SpecIssue(
                            f"{path}.on_failure.fallback",
                            f"fallback '{target}' is already used by step '{fallback_users[target]}'",
                        )
                    )
                else:
                    fallback_users[target] = step.id

        if isinstance(step, ApprovalStep):
            if is_fallback:
                issues.append(SpecIssue(path, "approval steps cannot be fallbacks"))
            if step.retry.max or step.on_failure:
                issues.append(SpecIssue(path, "approval steps cannot declare retry or on_failure"))
            for ref in step.show:
                if ref not in all_ids:
                    issues.append(SpecIssue(f"{path}.show", f"unknown step '{ref}'"))

        if isinstance(step, QueryStep | HttpStep):
            expected = _CONNECTOR_TYPES[step.type]
            spec = wf.connectors.get(step.connector)
            if spec is None:
                issues.append(SpecIssue(f"{path}.connector", f"unknown connector '{step.connector}'"))
            elif spec.type != expected:
                issues.append(
                    SpecIssue(
                        f"{path}.connector",
                        f"step type '{step.type}' needs a '{expected}' connector, "
                        f"'{step.connector}' is '{spec.type}'",
                    )
                )

        if isinstance(step, QueryStep) and is_template(step.sql):
            issues.append(
                SpecIssue(f"{path}.sql", "SQL is never templated; use :name bind parameters")
            )

        if isinstance(step, AiStep):
            for problem in schema_problems(step.output_schema):
                issues.append(SpecIssue(f"{path}.output_schema", problem))

        issues.extend(_expression_issues(path, step))

    graph = {step.id: {dep for dep in step.needs if dep in main} for step in wf.steps}
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        issues.append(SpecIssue("steps", f"dependency cycle: {' -> '.join(exc.args[1])}"))

    for key, value in wf.output.items():
        try:
            check_template(value)
        except TemplateError as exc:
            issues.append(SpecIssue(f"output.{key}", str(exc)))
    return issues


def _expression_issues(path: str, step: Any) -> list[SpecIssue]:
    issues: list[SpecIssue] = []

    def expr(field: str, value: str) -> None:
        try:
            check_expression(value)
        except TemplateError as exc:
            issues.append(SpecIssue(f"{path}.{field}", str(exc)))

    def template(field: str, value: Any) -> None:
        try:
            check_template(value)
        except TemplateError as exc:
            issues.append(SpecIssue(f"{path}.{field}", str(exc)))

    if step.when:
        expr("when", step.when)
    if isinstance(step, QueryStep):
        template("params", step.params)
    elif isinstance(step, HttpStep):
        template("path", step.path)
        template("body", step.body)
        template("headers", step.headers)
        template("query", step.query)
    elif isinstance(step, AiStep):
        template("prompt", step.prompt)
        if step.system:
            template("system", step.system)
        for i, rule in enumerate(step.mock):
            if rule.when:
                expr(f"mock[{i}].when", rule.when)
            template(f"mock[{i}].output", rule.output)
    elif isinstance(step, ValidateStep):
        for i, rule in enumerate(step.rules):
            expr(f"rules[{i}].expr", rule.expr)
    elif isinstance(step, ApprovalStep):
        template("title", step.title)
    elif isinstance(step, TaskStep):
        template("title", step.title)
        template("payload", step.payload)
    return issues
```

`src/cerebellum/spec/inputs.py`:

```python
"""Validate run input against the workflow's input declaration and resolve params."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from cerebellum.errors import SpecError, SpecIssue
from cerebellum.spec.models import Workflow

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def validate_input(wf: Workflow, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SpecError([SpecIssue("input", "run input must be a JSON object")])
    issues: list[SpecIssue] = []
    for name, field in wf.input.items():
        value = data.get(name)
        if value is None:
            if field.required:
                issues.append(SpecIssue(f"input.{name}", "is required"))
            continue
        if not _TYPE_CHECKS[field.type](value):
            issues.append(
                SpecIssue(
                    f"input.{name}",
                    f"expected {field.type}, got {type(value).__name__} {value!r}",
                )
            )
        elif field.enum is not None and value not in field.enum:
            issues.append(SpecIssue(f"input.{name}", f"must be one of {field.enum}"))
    if issues:
        raise SpecError(issues)
    return dict(data)


def resolve_params(wf: Workflow, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - set(wf.params))
    if unknown:
        raise SpecError([SpecIssue(f"params.{key}", "is not declared in the workflow") for key in unknown])
    return {**wf.params, **overrides}
```

Replace `src/cerebellum/spec/__init__.py`:

```python
"""Workflow definitions: models, loading, validation, expressions."""

from cerebellum.spec.inputs import resolve_params, validate_input
from cerebellum.spec.loader import load_workflow, parse_workflow
from cerebellum.spec.models import Workflow

__all__ = ["Workflow", "load_workflow", "parse_workflow", "resolve_params", "validate_input"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/spec -q`
Expected: all PASS. If `test_structural_error_has_a_path` fails because pydantic's discriminated-union location differs, print the issues once (`-s` with a temporary `print`) and adjust only the assertion's prefix check, not the loader.

- [ ] **Step 7: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 4: State machine and clock

**Files:**
- Create: `src/cerebellum/runtime/states.py`, `src/cerebellum/runtime/clock.py`
- Test: `tests/runtime/test_states.py`

**Interfaces:**
- Consumes: `InvalidTransition` (Task 1).
- Produces: `StepStatus` (`pending, running, retrying, waiting, succeeded, failed, skipped, cancelled, recovered`), `RunStatus` (`running, waiting_approval, succeeded, failed, rejected, needs_attention`), `STEP_TRANSITIONS`, `STEP_DONE_OK`, `STEP_BLOCKING`, `STEP_ACTIVE`, `RUN_TRANSITIONS`, `RUN_TERMINAL`, `RUN_RESUMABLE`, `check_step_transition(step_id, current, target)`, `check_run_transition(run_id, current, target)`; `Clock` protocol (`now() -> float`, `async sleep(seconds)`), `SystemClock`, `FakeClock(start=1_790_000_000.0)` with `advance(seconds)` and `sleeps: list[float]`.

- [ ] **Step 1: Write the failing tests**

`tests/runtime/test_states.py`:

```python
import pytest

from cerebellum.errors import InvalidTransition
from cerebellum.runtime.clock import FakeClock, SystemClock
from cerebellum.runtime.states import (
    RUN_RESUMABLE,
    STEP_BLOCKING,
    STEP_DONE_OK,
    RunStatus,
    StepStatus,
    check_run_transition,
    check_step_transition,
)

S = StepStatus
R = RunStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.PENDING, S.RUNNING), (S.PENDING, S.SKIPPED), (S.PENDING, S.CANCELLED),
        (S.RUNNING, S.SUCCEEDED), (S.RUNNING, S.RETRYING), (S.RETRYING, S.RUNNING),
        (S.RUNNING, S.WAITING), (S.WAITING, S.SUCCEEDED), (S.WAITING, S.FAILED),
        (S.FAILED, S.RECOVERED), (S.RUNNING, S.PENDING), (S.FAILED, S.PENDING),
        (S.CANCELLED, S.PENDING),
    ],
)
def test_allowed_step_transitions(current, target):
    check_step_transition("s", current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [(S.PENDING, S.SUCCEEDED), (S.SUCCEEDED, S.RUNNING), (S.SKIPPED, S.PENDING),
     (S.RECOVERED, S.FAILED), (S.WAITING, S.RETRYING)],
)
def test_forbidden_step_transitions(current, target):
    with pytest.raises(InvalidTransition, match=f"s: {current.value} -> {target.value}"):
        check_step_transition("s", current, target)


def test_run_transitions():
    check_run_transition("r", R.RUNNING, R.WAITING_APPROVAL)
    check_run_transition("r", R.WAITING_APPROVAL, R.RUNNING)
    check_run_transition("r", R.FAILED, R.RUNNING)
    for terminal in (R.SUCCEEDED, R.REJECTED, R.NEEDS_ATTENTION):
        with pytest.raises(InvalidTransition):
            check_run_transition("r", terminal, R.RUNNING)


def test_status_sets():
    assert STEP_DONE_OK == {S.SUCCEEDED, S.SKIPPED, S.RECOVERED}
    assert STEP_BLOCKING == {S.FAILED, S.CANCELLED}
    assert RUN_RESUMABLE == {R.RUNNING, R.WAITING_APPROVAL, R.FAILED}


def test_status_values_are_strings():
    assert S.WAITING == "waiting" and R.WAITING_APPROVAL.value == "waiting_approval"


async def test_fake_clock_records_sleeps_and_advances():
    clock = FakeClock(start=100.0)
    await clock.sleep(1.5)
    clock.advance(2)
    assert clock.now() == 103.5
    assert clock.sleeps == [1.5]


async def test_system_clock():
    clock = SystemClock()
    before = clock.now()
    await clock.sleep(0)
    assert clock.now() >= before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/runtime/test_states.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebellum.runtime.states'`.

- [ ] **Step 3: Implement**

`src/cerebellum/runtime/states.py`:

```python
"""Step and run state machines. Only the listed transitions are legal."""

from __future__ import annotations

from enum import StrEnum

from cerebellum.errors import InvalidTransition


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    RECOVERED = "recovered"


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    NEEDS_ATTENTION = "needs_attention"


S = StepStatus
R = RunStatus

STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    S.PENDING: frozenset({S.RUNNING, S.SKIPPED, S.CANCELLED}),
    # RUNNING -> PENDING / RETRYING -> PENDING: reset of an interrupted attempt on resume.
    S.RUNNING: frozenset({S.SUCCEEDED, S.FAILED, S.RETRYING, S.WAITING, S.CANCELLED, S.PENDING}),
    S.RETRYING: frozenset({S.RUNNING, S.CANCELLED, S.PENDING}),
    S.WAITING: frozenset({S.SUCCEEDED, S.FAILED, S.CANCELLED}),
    # FAILED -> RECOVERED: a fallback succeeded. FAILED/CANCELLED -> PENDING: resume after failure.
    S.FAILED: frozenset({S.RECOVERED, S.PENDING}),
    S.CANCELLED: frozenset({S.PENDING}),
    S.SUCCEEDED: frozenset(),
    S.SKIPPED: frozenset(),
    S.RECOVERED: frozenset(),
}

STEP_DONE_OK = frozenset({S.SUCCEEDED, S.SKIPPED, S.RECOVERED})
STEP_BLOCKING = frozenset({S.FAILED, S.CANCELLED})
STEP_ACTIVE = frozenset({S.RUNNING, S.RETRYING})

RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    R.RUNNING: frozenset(
        {R.WAITING_APPROVAL, R.SUCCEEDED, R.FAILED, R.REJECTED, R.NEEDS_ATTENTION}
    ),
    R.WAITING_APPROVAL: frozenset({R.RUNNING}),
    R.FAILED: frozenset({R.RUNNING}),
    R.SUCCEEDED: frozenset(),
    R.REJECTED: frozenset(),
    R.NEEDS_ATTENTION: frozenset(),
}

RUN_TERMINAL = frozenset({R.SUCCEEDED, R.FAILED, R.REJECTED, R.NEEDS_ATTENTION})
RUN_RESUMABLE = frozenset({R.RUNNING, R.WAITING_APPROVAL, R.FAILED})


def check_step_transition(step_id: str, current: StepStatus, target: StepStatus) -> None:
    if target not in STEP_TRANSITIONS[current]:
        raise InvalidTransition(f"{step_id}: {current.value} -> {target.value} is not allowed")


def check_run_transition(run_id: str, current: RunStatus, target: RunStatus) -> None:
    if target not in RUN_TRANSITIONS[current]:
        raise InvalidTransition(f"{run_id}: {current.value} -> {target.value} is not allowed")
```

`src/cerebellum/runtime/clock.py`:

```python
"""Injectable time source so retries, leases and approval timeouts are testable."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> float:
        return time.time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeClock:
    """Deterministic clock: sleep() advances time instantly and records the requested delay."""

    def __init__(self, start: float = 1_790_000_000.0):
        self._now = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += seconds
        await asyncio.sleep(0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/runtime/test_states.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 5: Event store with projections and leases

**Files:**
- Create: `src/cerebellum/runtime/store.py`, `tests/conftest.py`
- Test: `tests/runtime/test_store.py`

**Interfaces:**
- Consumes: `Workflow` (Task 3, attributes `digest, name, version, source_yaml, base_dir, steps, fallbacks`), states + clock (Task 4), `CerebellumError, RunNotFound`.
- Produces (all used by later tasks verbatim):
  - Records (frozen dataclasses): `RunRecord(run_id, workflow_digest, workflow_name, status: RunStatus, input, params, output, error, cost_usd, mock, created_at, updated_at, ended_at, lease_owner, lease_until, eval_run_id)`, `StepRecord(run_id, step_id, status: StepStatus, attempts, output, error, started_at, ended_at, cost_usd)` with property `duration`, `EventRecord(seq, run_id, step_id, span_id, parent_span_id, type, ts, data)`, `ApprovalRecord(id, run_id, step_id, status, title, context, requested_at, expires_at, on_timeout, decided_at, decided_by, comment)`, `TaskRecord(id, run_id, step_id, title, assignee, payload, status, created_at, resolved_at, resolved_by, note)`.
  - `Store(path, *, clock=None)`; context manager; `close()`; `clock`.
  - `save_workflow(wf)`, `get_workflow_source(digest) -> (source_yaml, base_dir)`.
  - `create_run(run_id, wf, input, params, *, mock, eval_run_id=None) -> RunRecord`, `get_run(run_id) -> RunRecord` (raises `RunNotFound`), `list_runs(*, status=None, limit=50, eval_run_id=None)`.
  - `set_run_status(run_id, status, *, event, output=UNSET, error=UNSET, data=None) -> RunRecord` (event type `run.<event>`).
  - `step_transition(run_id, step_id, target, *, event, span_id=None, attempts=None, output=UNSET, error=UNSET, started_at=None, ended_at=None, data=None) -> StepRecord` (event type `step.<event>`).
  - `get_steps(run_id) -> dict[str, StepRecord]` (workflow order), `get_step(run_id, step_id)`.
  - `record_call(run_id, step_id, kind, *, span_id, parent_span_id, data, cost_usd=0.0) -> EventRecord` (event type `<kind>.call`).
  - `request_approval(run_id, step_id, *, title, context, expires_at, on_timeout, span_id=None) -> ApprovalRecord` (also moves the step to `waiting`), `decide_approval(approval_id, *, approved, by, comment="", expired=False) -> ApprovalRecord`, `get_approval(id)`, `get_approval_for_step(run_id, step_id) -> ApprovalRecord | None`, `list_approvals(*, status=None, run_id=None)`.
  - `create_task(run_id, step_id, *, title, assignee, payload, span_id=None) -> TaskRecord`, `resolve_task(task_id, *, by, note="")`, `list_tasks(*, status=None, run_id=None)`.
  - `acquire_lease(run_id, owner, seconds) -> bool`, `renew_lease(...) -> bool`, `release_lease(run_id, owner)`, `is_stale(run) -> bool`.
  - `get_events(run_id, *, after_seq=0)`, `events_since(seq, *, limit=500)`, `add_listener(fn) -> unsubscribe`.
  - Module helpers: `UNSET`, `to_json(value)`.

- [ ] **Step 1: Write the shared fixtures**

`tests/conftest.py`:

```python
import socket

import pytest

from cerebellum.config import Settings
from cerebellum.runtime.clock import FakeClock
from cerebellum.runtime.store import Store
from cerebellum.spec import parse_workflow

SIMPLE_YAML = """
name: simple
steps:
  - id: first
    type: validate
    rules:
      - {expr: "true", message: never fails}
  - id: second
    type: task
    needs: [first]
    title: "Follow up {{ input.order_id }}"
fallbacks:
  - id: rescue
    type: task
    title: rescue
"""


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env({"CEREBELLUM_HOME": str(tmp_path / "home")})


@pytest.fixture
def store(settings, clock):
    with Store(settings.db_path, clock=clock) as s:
        yield s


@pytest.fixture
def simple_workflow(tmp_path):
    return parse_workflow(SIMPLE_YAML, base_dir=tmp_path, env={})


@pytest.fixture
def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
```

- [ ] **Step 2: Write the failing tests**

`tests/runtime/test_store.py`:

```python
import threading

import pytest

from cerebellum.errors import CerebellumError, InvalidTransition, RunNotFound
from cerebellum.runtime.states import RunStatus, StepStatus
from cerebellum.runtime.store import Store


def start(store, wf, run_id="r_test0001"):
    store.save_workflow(wf)
    return store.create_run(run_id, wf, {"order_id": "A1"}, {"k": 1}, mock=True)


def test_create_run_initialises_projection_and_event(store, simple_workflow):
    run = start(store, simple_workflow)
    assert run.status is RunStatus.RUNNING
    assert run.input == {"order_id": "A1"} and run.params == {"k": 1} and run.mock is True
    steps = store.get_steps(run.run_id)
    assert list(steps) == ["first", "second", "rescue"]
    assert all(step.status is StepStatus.PENDING for step in steps.values())
    events = store.get_events(run.run_id)
    assert [e.type for e in events] == ["run.started"]
    assert events[0].data["workflow"] == "simple"


def test_get_run_unknown_raises(store):
    with pytest.raises(RunNotFound):
        store.get_run("r_missing")


def test_step_transition_updates_projection_and_appends_event(store, simple_workflow, clock):
    run = start(store, simple_workflow)
    store.step_transition(
        run.run_id, "first", StepStatus.RUNNING, event="started", span_id="first#1",
        attempts=1, started_at=clock.now(),
    )
    clock.advance(2)
    record = store.step_transition(
        run.run_id, "first", StepStatus.SUCCEEDED, event="succeeded", span_id="first#1",
        output={"ok": True}, ended_at=clock.now(),
    )
    assert record.status is StepStatus.SUCCEEDED
    assert record.output == {"ok": True}
    assert record.attempts == 1
    assert record.duration == 2
    events = store.get_events(run.run_id)
    assert [e.type for e in events] == ["run.started", "step.started", "step.succeeded"]
    assert events[-1].span_id == "first#1"
    assert events[-1].data == {"status": "succeeded", "output": {"ok": True}}


def test_invalid_step_transition_raises_and_writes_nothing(store, simple_workflow):
    run = start(store, simple_workflow)
    with pytest.raises(InvalidTransition):
        store.step_transition(run.run_id, "first", StepStatus.SUCCEEDED, event="succeeded")
    assert [e.type for e in store.get_events(run.run_id)] == ["run.started"]
    assert store.get_step(run.run_id, "first").status is StepStatus.PENDING


def test_unknown_step_raises(store, simple_workflow):
    run = start(store, simple_workflow)
    with pytest.raises(CerebellumError, match="unknown step"):
        store.step_transition(run.run_id, "ghost", StepStatus.RUNNING, event="started")


def test_record_call_accumulates_cost(store, simple_workflow):
    run = start(store, simple_workflow)
    for cost in (0.25, 0.5):
        store.record_call(
            run.run_id, "first", "llm", span_id=f"c{cost}", parent_span_id="first#1",
            data={"model": "m"}, cost_usd=cost,
        )
    assert store.get_run(run.run_id).cost_usd == pytest.approx(0.75)
    assert store.get_step(run.run_id, "first").cost_usd == pytest.approx(0.75)
    event = store.get_events(run.run_id)[-1]
    assert event.type == "llm.call"
    assert event.parent_span_id == "first#1"
    assert event.data == {"model": "m", "cost_usd": 0.5}


def test_run_status_transitions(store, simple_workflow):
    run = start(store, simple_workflow)
    waiting = store.set_run_status(run.run_id, RunStatus.WAITING_APPROVAL, event="suspended")
    assert waiting.ended_at is None
    store.set_run_status(run.run_id, RunStatus.RUNNING, event="resumed")
    done = store.set_run_status(
        run.run_id, RunStatus.SUCCEEDED, event="completed", output={"decision": "ok"}
    )
    assert done.output == {"decision": "ok"}
    assert done.ended_at is not None
    with pytest.raises(InvalidTransition):
        store.set_run_status(run.run_id, RunStatus.RUNNING, event="resumed")
    types = [e.type for e in store.get_events(run.run_id)]
    assert types[-3:] == ["run.suspended", "run.resumed", "run.completed"]


def test_list_runs_filters(store, simple_workflow):
    start(store, simple_workflow, "r_a")
    start(store, simple_workflow, "r_b")
    store.set_run_status("r_b", RunStatus.FAILED, event="completed", error="boom")
    assert {r.run_id for r in store.list_runs()} == {"r_a", "r_b"}
    assert [r.run_id for r in store.list_runs(status=RunStatus.FAILED)] == ["r_b"]
    assert store.get_run("r_b").error == "boom"


def test_approval_lifecycle(store, simple_workflow, clock):
    run = start(store, simple_workflow)
    store.step_transition(run.run_id, "second", StepStatus.RUNNING, event="started", attempts=1)
    approval = store.request_approval(
        run.run_id, "second", title="Approve?", context={"x": 1},
        expires_at=clock.now() + 60, on_timeout="reject", span_id="second#1",
    )
    assert approval.status == "pending"
    assert approval.context == {"x": 1}
    assert approval.id.startswith("ap_")
    assert store.get_step(run.run_id, "second").status is StepStatus.WAITING
    assert [a.id for a in store.list_approvals(status="pending")] == [approval.id]

    decided = store.decide_approval(approval.id, approved=True, by="alice", comment="ok")
    assert decided.status == "approved" and decided.decided_by == "alice"
    with pytest.raises(CerebellumError, match="already approved"):
        store.decide_approval(approval.id, approved=False, by="bob")
    assert store.get_approval_for_step(run.run_id, "second").id == approval.id
    assert store.get_approval_for_step(run.run_id, "first") is None
    types = [e.type for e in store.get_events(run.run_id)]
    assert "approval.requested" in types and "approval.decided" in types


def test_expired_decision_uses_its_own_event(store, simple_workflow):
    run = start(store, simple_workflow)
    store.step_transition(run.run_id, "second", StepStatus.RUNNING, event="started", attempts=1)
    approval = store.request_approval(
        run.run_id, "second", title="t", context={}, expires_at=None, on_timeout="reject"
    )
    store.decide_approval(approval.id, approved=False, by="system", expired=True)
    assert store.get_events(run.run_id)[-1].type == "approval.expired"


def test_tasks_lifecycle(store, simple_workflow):
    run = start(store, simple_workflow)
    task = store.create_task(
        run.run_id, "rescue", title="Fix it", assignee="ops", payload={"a": 1}
    )
    assert task.status == "open" and task.payload == {"a": 1} and task.id.startswith("tk_")
    resolved = store.resolve_task(task.id, by="ops", note="done")
    assert resolved.status == "resolved" and resolved.resolved_by == "ops"
    assert store.list_tasks(status="open") == []
    assert [t.id for t in store.list_tasks(run_id=run.run_id)] == [task.id]
    with pytest.raises(CerebellumError, match="already resolved"):
        store.resolve_task(task.id, by="ops")


def test_leases_are_exclusive_until_expiry(store, simple_workflow, clock):
    run = start(store, simple_workflow)
    assert store.acquire_lease(run.run_id, "a", 30)
    assert not store.acquire_lease(run.run_id, "b", 30)
    assert store.acquire_lease(run.run_id, "a", 30)
    assert not store.is_stale(store.get_run(run.run_id))
    clock.advance(31)
    assert store.is_stale(store.get_run(run.run_id))
    assert store.acquire_lease(run.run_id, "b", 30)
    assert not store.renew_lease(run.run_id, "a", 30)
    assert store.renew_lease(run.run_id, "b", 30)
    store.release_lease(run.run_id, "b")
    assert store.get_run(run.run_id).lease_owner is None


def test_acquire_lease_on_unknown_run_raises(store):
    with pytest.raises(RunNotFound):
        store.acquire_lease("r_nope", "a", 30)


def test_listeners_receive_committed_events(store, simple_workflow):
    seen = []
    unsubscribe = store.add_listener(seen.append)
    run = start(store, simple_workflow)
    unsubscribe()
    store.record_call(run.run_id, "first", "llm", span_id="s", parent_span_id=None, data={})
    assert [e.type for e in seen] == ["run.started"]


def test_events_since_returns_global_tail(store, simple_workflow):
    run = start(store, simple_workflow)
    first_seq = store.get_events(run.run_id)[0].seq
    store.record_call(run.run_id, "first", "llm", span_id="s", parent_span_id=None, data={})
    assert [e.type for e in store.events_since(first_seq)] == ["llm.call"]
    assert [e.type for e in store.get_events(run.run_id, after_seq=first_seq)] == ["llm.call"]


def test_workflow_snapshot_round_trip(store, simple_workflow):
    store.save_workflow(simple_workflow)
    store.save_workflow(simple_workflow)  # idempotent
    source, base_dir = store.get_workflow_source(simple_workflow.digest)
    assert source == simple_workflow.source_yaml
    assert base_dir == simple_workflow.base_dir
    with pytest.raises(CerebellumError, match="snapshot"):
        store.get_workflow_source("missing")


def test_values_are_json_safe(store, simple_workflow):
    from datetime import date
    from decimal import Decimal

    run = start(store, simple_workflow)
    store.record_call(
        run.run_id, "first", "connector", span_id="s", parent_span_id=None,
        data={"amount": Decimal("12.50"), "day": date(2026, 10, 1)},
    )
    assert store.get_events(run.run_id)[-1].data["amount"] == 12.5
    assert store.get_events(run.run_id)[-1].data["day"] == "2026-10-01"


def test_concurrent_writers_on_one_database(settings, simple_workflow):
    """Review focus: the CLI and the UI server write to the same file through different
    connections at the same time."""
    a = Store(settings.db_path)
    b = Store(settings.db_path)
    try:
        a.save_workflow(simple_workflow)
        run = a.create_run("r_shared01", simple_workflow, {}, {}, mock=True)

        def write(target):
            for i in range(50):
                target.record_call(
                    run.run_id, "first", "connector", span_id=f"s{i}", parent_span_id=None,
                    data={"i": i}, cost_usd=0.001,
                )

        threads = [threading.Thread(target=write, args=(s,)) for s in (a, b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(a.get_events(run.run_id)) == 101
        assert b.get_run(run.run_id).cost_usd == pytest.approx(0.1)
    finally:
        a.close()
        b.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/runtime/test_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebellum.runtime.store'` (raised from `tests/conftest.py`).

- [ ] **Step 4: Implement**

`src/cerebellum/runtime/store.py`:

```python
"""SQLite event store. Every mutation appends to `events` and updates the projection tables
(runs, step_states, approvals, tasks) inside one transaction, so the projections can always be
explained by the event log."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cerebellum.errors import CerebellumError, RunNotFound
from cerebellum.runtime.clock import Clock, SystemClock
from cerebellum.runtime.states import (
    RUN_TERMINAL,
    RunStatus,
    StepStatus,
    check_run_transition,
    check_step_transition,
)

if TYPE_CHECKING:
    from cerebellum.spec.models import Workflow

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    digest      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL,
    source_yaml TEXT NOT NULL,
    base_dir    TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    workflow_digest TEXT NOT NULL REFERENCES workflows(digest),
    workflow_name   TEXT NOT NULL,
    status          TEXT NOT NULL,
    input           TEXT NOT NULL,
    params          TEXT NOT NULL,
    output          TEXT,
    error           TEXT,
    cost_usd        REAL NOT NULL DEFAULT 0,
    mock            INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    ended_at        REAL,
    lease_owner     TEXT,
    lease_until     REAL,
    eval_run_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
CREATE TABLE IF NOT EXISTS events (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    step_id        TEXT,
    span_id        TEXT,
    parent_span_id TEXT,
    type           TEXT NOT NULL,
    ts             REAL NOT NULL,
    data           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
CREATE TABLE IF NOT EXISTS step_states (
    run_id     TEXT NOT NULL,
    step_id    TEXT NOT NULL,
    position   INTEGER NOT NULL,
    status     TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    output     TEXT,
    error      TEXT,
    started_at REAL,
    ended_at   REAL,
    cost_usd   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, step_id)
);
CREATE TABLE IF NOT EXISTS approvals (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    step_id      TEXT NOT NULL,
    status       TEXT NOT NULL,
    title        TEXT NOT NULL,
    context      TEXT NOT NULL,
    requested_at REAL NOT NULL,
    expires_at   REAL,
    on_timeout   TEXT NOT NULL,
    decided_at   REAL,
    decided_by   TEXT,
    comment      TEXT,
    UNIQUE (run_id, step_id)
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    step_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    assignee    TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    resolved_at REAL,
    resolved_by TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""

UNSET: Any = object()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    workflow_digest: str
    workflow_name: str
    status: RunStatus
    input: dict[str, Any]
    params: dict[str, Any]
    output: Any
    error: str | None
    cost_usd: float
    mock: bool
    created_at: float
    updated_at: float
    ended_at: float | None
    lease_owner: str | None
    lease_until: float | None
    eval_run_id: str | None


@dataclass(frozen=True)
class StepRecord:
    run_id: str
    step_id: str
    status: StepStatus
    attempts: int
    output: Any
    error: str | None
    started_at: float | None
    ended_at: float | None
    cost_usd: float

    @property
    def duration(self) -> float | None:
        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at


@dataclass(frozen=True)
class EventRecord:
    seq: int
    run_id: str
    step_id: str | None
    span_id: str | None
    parent_span_id: str | None
    type: str
    ts: float
    data: dict[str, Any]


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    run_id: str
    step_id: str
    status: str
    title: str
    context: dict[str, Any]
    requested_at: float
    expires_at: float | None
    on_timeout: str
    decided_at: float | None
    decided_by: str | None
    comment: str | None


@dataclass(frozen=True)
class TaskRecord:
    id: str
    run_id: str
    step_id: str
    title: str
    assignee: str
    payload: dict[str, Any]
    status: str
    created_at: float
    resolved_at: float | None
    resolved_by: str | None
    note: str | None


def _run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        workflow_digest=row["workflow_digest"],
        workflow_name=row["workflow_name"],
        status=RunStatus(row["status"]),
        input=json.loads(row["input"]),
        params=json.loads(row["params"]),
        output=_loads(row["output"]),
        error=row["error"],
        cost_usd=row["cost_usd"],
        mock=bool(row["mock"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        ended_at=row["ended_at"],
        lease_owner=row["lease_owner"],
        lease_until=row["lease_until"],
        eval_run_id=row["eval_run_id"],
    )


def _step(row: sqlite3.Row) -> StepRecord:
    return StepRecord(
        run_id=row["run_id"],
        step_id=row["step_id"],
        status=StepStatus(row["status"]),
        attempts=row["attempts"],
        output=_loads(row["output"]),
        error=row["error"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        cost_usd=row["cost_usd"],
    )


def _event(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        seq=row["seq"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        span_id=row["span_id"],
        parent_span_id=row["parent_span_id"],
        type=row["type"],
        ts=row["ts"],
        data=json.loads(row["data"]),
    )


def _approval(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        id=row["id"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        status=row["status"],
        title=row["title"],
        context=json.loads(row["context"]),
        requested_at=row["requested_at"],
        expires_at=row["expires_at"],
        on_timeout=row["on_timeout"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        comment=row["comment"],
    )


def _task(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        id=row["id"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        title=row["title"],
        assignee=row["assignee"],
        payload=json.loads(row["payload"]),
        status=row["status"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        resolved_by=row["resolved_by"],
        note=row["note"],
    )


class _Tx:
    def __init__(self, conn: sqlite3.Connection, now: float):
        self.conn = conn
        self.now = now
        self.events: list[EventRecord] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def event(
        self,
        run_id: str,
        type: str,
        *,
        step_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> EventRecord:
        payload = to_json(data or {})
        cursor = self.conn.execute(
            "INSERT INTO events(run_id, step_id, span_id, parent_span_id, type, ts, data) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, step_id, span_id, parent_span_id, type, self.now, payload),
        )
        record = EventRecord(
            seq=cursor.lastrowid or 0,
            run_id=run_id,
            step_id=step_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            type=type,
            ts=self.now,
            data=json.loads(payload),
        )
        self.events.append(record)
        return record


class Store:
    def __init__(self, path: str | Path, *, clock: Clock | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock: Clock = clock or SystemClock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None, timeout=10.0
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._listeners: list[Callable[[EventRecord], None]] = []
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.executescript(SCHEMA)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def add_listener(self, listener: Callable[[EventRecord], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    @contextmanager
    def _tx(self) -> Iterator[_Tx]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            tx = _Tx(self._conn, self.clock.now())
            try:
                yield tx
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
        for event in tx.events:
            for listener in list(self._listeners):
                try:
                    listener(event)
                except Exception:  # listeners are display-only; they must never break a run
                    pass

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ── workflows ─────────────────────────────────────────────────────────────

    def save_workflow(self, wf: Workflow) -> None:
        with self._tx() as tx:
            tx.execute(
                "INSERT OR IGNORE INTO workflows(digest, name, version, source_yaml, base_dir, "
                "created_at) VALUES (?,?,?,?,?,?)",
                (wf.digest, wf.name, wf.version, wf.source_yaml, wf.base_dir, tx.now),
            )

    def get_workflow_source(self, digest: str) -> tuple[str, str]:
        rows = self._rows("SELECT source_yaml, base_dir FROM workflows WHERE digest=?", (digest,))
        if not rows:
            raise CerebellumError(f"workflow snapshot {digest!r} not found")
        return rows[0]["source_yaml"], rows[0]["base_dir"]

    # ── runs ──────────────────────────────────────────────────────────────────

    def create_run(
        self,
        run_id: str,
        wf: Workflow,
        input: dict[str, Any],
        params: dict[str, Any],
        *,
        mock: bool,
        eval_run_id: str | None = None,
    ) -> RunRecord:
        with self._tx() as tx:
            tx.execute(
                "INSERT INTO runs(run_id, workflow_digest, workflow_name, status, input, params, "
                "mock, created_at, updated_at, eval_run_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, wf.digest, wf.name, RunStatus.RUNNING.value, to_json(input),
                    to_json(params), int(mock), tx.now, tx.now, eval_run_id,
                ),
            )
            for position, step in enumerate((*wf.steps, *wf.fallbacks)):
                tx.execute(
                    "INSERT INTO step_states(run_id, step_id, position, status) VALUES (?,?,?,?)",
                    (run_id, step.id, position, StepStatus.PENDING.value),
                )
            tx.event(
                run_id,
                "run.started",
                data={
                    "workflow": wf.name, "version": wf.version, "digest": wf.digest,
                    "input": input, "params": params, "mock": mock, "eval_run_id": eval_run_id,
                },
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        rows = self._rows("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if not rows:
            raise RunNotFound(f"run {run_id!r} not found")
        return _run(rows[0])

    def list_runs(
        self,
        *,
        status: RunStatus | None = None,
        limit: int = 50,
        eval_run_id: str | None = None,
    ) -> list[RunRecord]:
        clauses, params = [], []
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        if eval_run_id is not None:
            clauses.append("eval_run_id=?")
            params.append(eval_run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._rows(
            f"SELECT * FROM runs {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (*params, limit),
        )
        return [_run(row) for row in rows]

    def set_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        event: str,
        output: Any = UNSET,
        error: Any = UNSET,
        data: dict[str, Any] | None = None,
    ) -> RunRecord:
        with self._tx() as tx:
            rows = tx.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchall()
            if not rows:
                raise RunNotFound(f"run {run_id!r} not found")
            check_run_transition(run_id, RunStatus(rows[0]["status"]), status)
            sets = ["status=?", "updated_at=?", "ended_at=?"]
            values: list[Any] = [status.value, tx.now, tx.now if status in RUN_TERMINAL else None]
            if output is not UNSET:
                sets.append("output=?")
                values.append(None if output is None else to_json(output))
            if error is not UNSET:
                sets.append("error=?")
                values.append(error)
            tx.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id=?", (*values, run_id))
            payload: dict[str, Any] = {"status": status.value, **(data or {})}
            if output is not UNSET and output is not None:
                payload["output"] = output
            if error is not UNSET and error:
                payload["error"] = error
            tx.event(run_id, f"run.{event}", data=payload)
        return self.get_run(run_id)

    # ── steps ─────────────────────────────────────────────────────────────────

    def get_steps(self, run_id: str) -> dict[str, StepRecord]:
        rows = self._rows("SELECT * FROM step_states WHERE run_id=? ORDER BY position", (run_id,))
        return {row["step_id"]: _step(row) for row in rows}

    def get_step(self, run_id: str, step_id: str) -> StepRecord:
        rows = self._rows(
            "SELECT * FROM step_states WHERE run_id=? AND step_id=?", (run_id, step_id)
        )
        if not rows:
            raise CerebellumError(f"unknown step {step_id!r} in run {run_id!r}")
        return _step(rows[0])

    def step_transition(
        self,
        run_id: str,
        step_id: str,
        target: StepStatus,
        *,
        event: str,
        span_id: str | None = None,
        attempts: int | None = None,
        output: Any = UNSET,
        error: Any = UNSET,
        started_at: float | None = None,
        ended_at: float | None = None,
        data: dict[str, Any] | None = None,
    ) -> StepRecord:
        with self._tx() as tx:
            _apply_step_transition(
                tx, run_id, step_id, target, event=event, span_id=span_id, attempts=attempts,
                output=output, error=error, started_at=started_at, ended_at=ended_at, data=data,
            )
        return self.get_step(run_id, step_id)

    def record_call(
        self,
        run_id: str,
        step_id: str,
        kind: str,
        *,
        span_id: str,
        parent_span_id: str | None,
        data: dict[str, Any],
        cost_usd: float = 0.0,
    ) -> EventRecord:
        with self._tx() as tx:
            if cost_usd:
                tx.execute(
                    "UPDATE step_states SET cost_usd = cost_usd + ? WHERE run_id=? AND step_id=?",
                    (cost_usd, run_id, step_id),
                )
                tx.execute(
                    "UPDATE runs SET cost_usd = cost_usd + ?, updated_at=? WHERE run_id=?",
                    (cost_usd, tx.now, run_id),
                )
            return tx.event(
                run_id,
                f"{kind}.call",
                step_id=step_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                data={**data, "cost_usd": cost_usd},
            )

    # ── approvals ─────────────────────────────────────────────────────────────

    def request_approval(
        self,
        run_id: str,
        step_id: str,
        *,
        title: str,
        context: dict[str, Any],
        expires_at: float | None,
        on_timeout: str,
        span_id: str | None = None,
    ) -> ApprovalRecord:
        with self._tx() as tx:
            existing = tx.execute(
                "SELECT id FROM approvals WHERE run_id=? AND step_id=?", (run_id, step_id)
            ).fetchall()
            if existing:
                approval_id = existing[0]["id"]
            else:
                approval_id = "ap_" + secrets.token_hex(4)
                tx.execute(
                    "INSERT INTO approvals(id, run_id, step_id, status, title, context, "
                    "requested_at, expires_at, on_timeout) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        approval_id, run_id, step_id, "pending", title, to_json(context), tx.now,
                        expires_at, on_timeout,
                    ),
                )
                tx.event(
                    run_id,
                    "approval.requested",
                    step_id=step_id,
                    span_id=span_id,
                    data={"approval_id": approval_id, "title": title, "expires_at": expires_at},
                )
            _apply_step_transition(
                tx, run_id, step_id, StepStatus.WAITING, event="waiting", span_id=span_id,
                data={"approval_id": approval_id},
            )
        return self.get_approval(approval_id)

    def decide_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        by: str,
        comment: str = "",
        expired: bool = False,
    ) -> ApprovalRecord:
        with self._tx() as tx:
            rows = tx.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchall()
            if not rows:
                raise CerebellumError(f"approval {approval_id!r} not found")
            row = rows[0]
            if row["status"] != "pending":
                raise CerebellumError(f"approval {approval_id} is already {row['status']}")
            status = "approved" if approved else "rejected"
            tx.execute(
                "UPDATE approvals SET status=?, decided_at=?, decided_by=?, comment=? WHERE id=?",
                (status, tx.now, by, comment, approval_id),
            )
            tx.execute("UPDATE runs SET updated_at=? WHERE run_id=?", (tx.now, row["run_id"]))
            tx.event(
                row["run_id"],
                "approval.expired" if expired else "approval.decided",
                step_id=row["step_id"],
                data={"approval_id": approval_id, "decision": status, "by": by, "comment": comment},
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        rows = self._rows("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if not rows:
            raise CerebellumError(f"approval {approval_id!r} not found")
        return _approval(rows[0])

    def get_approval_for_step(self, run_id: str, step_id: str) -> ApprovalRecord | None:
        rows = self._rows(
            "SELECT * FROM approvals WHERE run_id=? AND step_id=?", (run_id, step_id)
        )
        return _approval(rows[0]) if rows else None

    def list_approvals(
        self, *, status: str | None = None, run_id: str | None = None
    ) -> list[ApprovalRecord]:
        clauses, params = [], []
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._rows(f"SELECT * FROM approvals {where} ORDER BY requested_at, id", tuple(params))
        return [_approval(row) for row in rows]

    # ── tasks ─────────────────────────────────────────────────────────────────

    def create_task(
        self,
        run_id: str,
        step_id: str,
        *,
        title: str,
        assignee: str,
        payload: dict[str, Any],
        span_id: str | None = None,
    ) -> TaskRecord:
        task_id = "tk_" + secrets.token_hex(4)
        with self._tx() as tx:
            tx.execute(
                "INSERT INTO tasks(id, run_id, step_id, title, assignee, payload, status, "
                "created_at) VALUES (?,?,?,?,?,?,?,?)",
                (task_id, run_id, step_id, title, assignee, to_json(payload), "open", tx.now),
            )
            tx.event(
                run_id,
                "task.created",
                step_id=step_id,
                span_id=span_id,
                data={"task_id": task_id, "title": title, "assignee": assignee},
            )
        return self.get_task(task_id)

    def resolve_task(self, task_id: str, *, by: str, note: str = "") -> TaskRecord:
        with self._tx() as tx:
            rows = tx.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchall()
            if not rows:
                raise CerebellumError(f"task {task_id!r} not found")
            if rows[0]["status"] != "open":
                raise CerebellumError(f"task {task_id} is already {rows[0]['status']}")
            tx.execute(
                "UPDATE tasks SET status='resolved', resolved_at=?, resolved_by=?, note=? "
                "WHERE id=?",
                (tx.now, by, note, task_id),
            )
            tx.event(
                rows[0]["run_id"],
                "task.resolved",
                step_id=rows[0]["step_id"],
                data={"task_id": task_id, "by": by, "note": note},
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> TaskRecord:
        rows = self._rows("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            raise CerebellumError(f"task {task_id!r} not found")
        return _task(rows[0])

    def list_tasks(self, *, status: str | None = None, run_id: str | None = None) -> list[TaskRecord]:
        clauses, params = [], []
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._rows(f"SELECT * FROM tasks {where} ORDER BY created_at, id", tuple(params))
        return [_task(row) for row in rows]

    # ── leases ────────────────────────────────────────────────────────────────

    def acquire_lease(self, run_id: str, owner: str, seconds: float) -> bool:
        with self._lock:
            now = self.clock.now()
            cursor = self._conn.execute(
                "UPDATE runs SET lease_owner=?, lease_until=? WHERE run_id=? AND "
                "(lease_owner IS NULL OR lease_owner=? OR lease_until IS NULL OR lease_until < ?)",
                (owner, now + seconds, run_id, owner, now),
            )
            if cursor.rowcount == 1:
                return True
        self.get_run(run_id)  # raises RunNotFound for unknown ids
        return False

    def renew_lease(self, run_id: str, owner: str, seconds: float) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE runs SET lease_until=? WHERE run_id=? AND lease_owner=?",
                (self.clock.now() + seconds, run_id, owner),
            )
            return cursor.rowcount == 1

    def release_lease(self, run_id: str, owner: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET lease_owner=NULL, lease_until=NULL WHERE run_id=? AND lease_owner=?",
                (run_id, owner),
            )

    def is_stale(self, run: RunRecord) -> bool:
        """A run that claims to be running but has no live lease (its process died)."""
        if run.status is not RunStatus.RUNNING:
            return False
        return run.lease_until is None or run.lease_until < self.clock.now()

    # ── events ────────────────────────────────────────────────────────────────

    def get_events(self, run_id: str, *, after_seq: int = 0) -> list[EventRecord]:
        rows = self._rows(
            "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after_seq)
        )
        return [_event(row) for row in rows]

    def events_since(self, seq: int, *, limit: int = 500) -> list[EventRecord]:
        rows = self._rows("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?", (seq, limit))
        return [_event(row) for row in rows]


def _apply_step_transition(
    tx: _Tx,
    run_id: str,
    step_id: str,
    target: StepStatus,
    *,
    event: str,
    span_id: str | None = None,
    attempts: int | None = None,
    output: Any = UNSET,
    error: Any = UNSET,
    started_at: float | None = None,
    ended_at: float | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    rows = tx.execute(
        "SELECT status FROM step_states WHERE run_id=? AND step_id=?", (run_id, step_id)
    ).fetchall()
    if not rows:
        raise CerebellumError(f"unknown step {step_id!r} in run {run_id!r}")
    check_step_transition(step_id, StepStatus(rows[0]["status"]), target)
    sets = ["status=?"]
    values: list[Any] = [target.value]
    if attempts is not None:
        sets.append("attempts=?")
        values.append(attempts)
    if output is not UNSET:
        sets.append("output=?")
        values.append(None if output is None else to_json(output))
    if error is not UNSET:
        sets.append("error=?")
        values.append(error)
    if started_at is not None:
        sets.append("started_at=?")
        values.append(started_at)
    if ended_at is not None:
        sets.append("ended_at=?")
        values.append(ended_at)
    tx.execute(
        f"UPDATE step_states SET {', '.join(sets)} WHERE run_id=? AND step_id=?",
        (*values, run_id, step_id),
    )
    tx.execute("UPDATE runs SET updated_at=? WHERE run_id=?", (tx.now, run_id))
    payload: dict[str, Any] = {"status": target.value, **(data or {})}
    if output is not UNSET and output is not None:
        payload["output"] = output
    if error is not UNSET and error:
        payload["error"] = error
    tx.event(run_id, f"step.{event}", step_id=step_id, span_id=span_id, data=payload)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/runtime/test_store.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 6: Connectors (base, SQLite sandbox / PostgreSQL, REST)

**Files:**
- Create: `src/cerebellum/connectors/__init__.py`, `src/cerebellum/connectors/base.py`, `src/cerebellum/connectors/postgres.py`, `src/cerebellum/connectors/rest.py`
- Test: `tests/connectors/test_connectors.py`

**Interfaces:**
- Consumes: `StepError`, `CerebellumError`, `PostgresConnectorSpec`, `RestConnectorSpec`.
- Produces: `ConnectorError(StepError)`, `HealthStatus(ok, detail, latency_ms=0.0)`, `ConnectorEnv(home: Path, base_dir: Path, http_transports: Mapping[str, httpx.AsyncBaseTransport] = {})`, `Connector` (`name`, `async open/close/health`), `SqlConnector.query(sql, params) -> list[dict]`, `SqlConnector.execute(sql, params) -> int`, `HttpResponse(status, body, headers, elapsed_ms)`, `HttpConnector.request(method, path, *, json=None, headers=None, query=None, idempotency_key=None) -> HttpResponse` and `default_headers` property, `register_connector(type)`, `create_connector(name, spec, env)`, `ConnectorPool(specs, env)` with `async get(name)` / `async close()`, `jsonable(value)`; in `postgres.py`: `to_pyformat(sql)`, `sandbox_db_path(home, name)`, `SqliteSandboxConnector`, `PostgresConnector`, `SANDBOX_DSN = "sandbox"`; in `rest.py`: `redact_headers(headers)`, `RestConnector`, `REDACTED = "***"`.

- [ ] **Step 1: Write the failing tests**

`tests/connectors/test_connectors.py`:

```python
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from cerebellum.connectors import (
    ConnectorEnv,
    ConnectorError,
    ConnectorPool,
    create_connector,
    jsonable,
)
from cerebellum.connectors.postgres import (
    PostgresConnector,
    SqliteSandboxConnector,
    sandbox_db_path,
    to_pyformat,
)
from cerebellum.connectors.rest import RestConnector, redact_headers
from cerebellum.spec.models import PostgresConnectorSpec, RestConnectorSpec

SEED = """
CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, amount NUMERIC(10, 2) NOT NULL,
                                   status TEXT NOT NULL);
INSERT INTO orders (id, amount, status) VALUES ('A1', 120.00, 'delivered'), ('A2', 15.50, 'shipped')
ON CONFLICT (id) DO NOTHING;
"""


@pytest.fixture
def seed_file(tmp_path):
    path = tmp_path / "seed.sql"
    path.write_text(SEED)
    return path


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM t WHERE id = :id", "SELECT * FROM t WHERE id = %(id)s"),
        ("SELECT x::text FROM t WHERE a = :a_1", "SELECT x::text FROM t WHERE a = %(a_1)s"),
        ("SELECT ':not' AS s, '5%' AS p WHERE b = :b", "SELECT ':not' AS s, '5%%' AS p WHERE b = %(b)s"),
        ("SELECT 'it''s :x' WHERE c = :c", "SELECT 'it''s :x' WHERE c = %(c)s"),
        ("SELECT 10 % 3", "SELECT 10 %% 3"),
    ],
)
def test_to_pyformat(sql, expected):
    assert to_pyformat(sql) == expected


def test_jsonable():
    assert jsonable(Decimal("12.50")) == 12.5
    assert jsonable(date(2026, 10, 1)) == "2026-10-01"
    assert jsonable(datetime(2026, 10, 1, 8, 30)) == "2026-10-01T08:30:00"
    assert jsonable(b"\x01\xff") == "01ff"
    assert jsonable("x") == "x"


async def test_sandbox_seeds_once_and_queries(tmp_path, seed_file):
    db = tmp_path / "sandbox.db"
    connector = SqliteSandboxConnector("orders_db", db, seed_file)
    rows = await connector.query("SELECT id, amount, status FROM orders WHERE id = :id", {"id": "A1"})
    assert rows == [{"id": "A1", "amount": 120, "status": "delivered"}]
    assert await connector.execute(
        "UPDATE orders SET status = 'refunded' WHERE id = :id", {"id": "A1"}
    ) == 1
    await connector.close()

    reopened = SqliteSandboxConnector("orders_db", db, seed_file)
    rows = await reopened.query("SELECT status FROM orders WHERE id = :id", {"id": "A1"})
    assert rows == [{"status": "refunded"}]  # not re-seeded
    health = await reopened.health()
    assert health.ok and "sqlite sandbox" in health.detail
    await reopened.close()


async def test_sandbox_missing_param_is_not_retryable(tmp_path, seed_file):
    connector = SqliteSandboxConnector("db", tmp_path / "s.db", seed_file)
    with pytest.raises(ConnectorError) as info:
        await connector.query("SELECT * FROM orders WHERE id = :id", {})
    assert info.value.retryable is False and info.value.kind == "sql"
    await connector.close()


async def test_sandbox_bad_seed_removes_partial_file(tmp_path):
    bad = tmp_path / "bad.sql"
    bad.write_text("CREATE TABLE broken (;")
    db = tmp_path / "s.db"
    connector = SqliteSandboxConnector("db", db, bad)
    with pytest.raises(ConnectorError, match="cannot seed") as info:
        await connector.open()
    assert info.value.retryable is False
    assert not db.exists()


def test_factory_picks_sandbox_or_postgres(tmp_path):
    env = ConnectorEnv(home=tmp_path, base_dir=tmp_path)
    sandbox = create_connector(
        "orders_db", PostgresConnectorSpec(type="postgres", dsn="sandbox", seed="seed.sql"), env
    )
    assert isinstance(sandbox, SqliteSandboxConnector)
    assert sandbox.path == sandbox_db_path(tmp_path, "orders_db") == tmp_path / "sandbox_orders_db.db"
    assert sandbox.seed == tmp_path / "seed.sql"
    real = create_connector(
        "orders_db", PostgresConnectorSpec(type="postgres", dsn="postgresql://u@h/db"), env
    )
    assert isinstance(real, PostgresConnector)


def make_rest(handler, **spec):
    spec = RestConnectorSpec(type="rest", base_url="http://api.test", **spec)
    return RestConnector("api", spec, transport=httpx.MockTransport(handler))


async def test_rest_success_sends_idempotency_key_and_default_headers():
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content
        return httpx.Response(201, json={"id": "rf_1"}, headers={"x-request-id": "abc"})

    rest = make_rest(handler, headers={"Authorization": "Bearer secret"})
    response = await rest.request(
        "POST", "/refunds", json={"amount": 10}, headers={"X-Trace": "t"}, idempotency_key="r_1:pay"
    )
    assert response.status == 201
    assert response.body == {"id": "rf_1"}
    assert response.headers == {"content-type": "application/json", "x-request-id": "abc"}
    assert seen["headers"]["idempotency-key"] == "r_1:pay"
    assert seen["headers"]["authorization"] == "Bearer secret"
    assert seen["headers"]["x-trace"] == "t"
    assert rest.default_headers == {"Authorization": "Bearer secret"}
    await rest.close()


async def test_rest_5xx_and_429_are_retryable_with_details():
    rest = make_rest(lambda request: httpx.Response(503, json={"detail": "down"}))
    with pytest.raises(ConnectorError) as info:
        await rest.request("POST", "/refunds")
    assert info.value.retryable is True
    assert info.value.kind == "http_status"
    assert info.value.details["status"] == 503
    assert info.value.details["body"] == {"detail": "down"}

    limited = make_rest(lambda request: httpx.Response(429))
    with pytest.raises(ConnectorError) as info:
        await limited.request("GET", "/x")
    assert info.value.retryable is True


async def test_rest_4xx_is_not_retryable():
    rest = make_rest(lambda request: httpx.Response(404, json={"detail": "missing"}))
    with pytest.raises(ConnectorError, match="returned HTTP 404") as info:
        await rest.request("GET", "/refunds/x")
    assert info.value.retryable is False


async def test_rest_error_with_non_json_body():
    """Review focus: a gateway answering with HTML/plain text or nothing must not crash decoding."""
    text = make_rest(
        lambda request: httpx.Response(502, text="Bad Gateway", headers={"content-type": "text/html"})
    )
    with pytest.raises(ConnectorError) as info:
        await text.request("POST", "/refunds")
    assert info.value.details["body"] == "Bad Gateway"

    broken_json = make_rest(
        lambda request: httpx.Response(
            500, content=b"{not json", headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(ConnectorError) as info:
        await broken_json.request("POST", "/refunds")
    assert info.value.details["body"] == "{not json"

    empty = make_rest(lambda request: httpx.Response(500))
    with pytest.raises(ConnectorError) as info:
        await empty.request("POST", "/refunds")
    assert info.value.details["body"] is None


async def test_rest_timeouts_and_connection_errors_are_retryable():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ConnectorError) as info:
        await make_rest(timeout).request("GET", "/x")
    assert info.value.retryable is True and info.value.kind == "timeout"
    with pytest.raises(ConnectorError) as info:
        await make_rest(refused).request("GET", "/x")
    assert info.value.retryable is True and info.value.kind == "connection"


async def test_rest_health():
    healthy = make_rest(lambda request: httpx.Response(200, json={"ok": True}))
    assert (await healthy.health()).ok

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    status = await make_rest(refused).health()
    assert status.ok is False and "unreachable" in status.detail


def test_redact_headers():
    headers = {
        "Authorization": "Bearer x", "X-Api-Key": "k", "Cookie": "c", "X-Auth-Token": "t",
        "Idempotency-Key": "r_1:pay", "Content-Type": "application/json",
    }
    assert redact_headers(headers) == {
        "Authorization": "***", "X-Api-Key": "***", "Cookie": "***", "X-Auth-Token": "***",
        "Idempotency-Key": "r_1:pay", "Content-Type": "application/json",
    }


async def test_pool_opens_lazily_once_and_closes(tmp_path, seed_file):
    env = ConnectorEnv(home=tmp_path, base_dir=seed_file.parent)
    pool = ConnectorPool(
        {"orders_db": PostgresConnectorSpec(type="postgres", dsn="sandbox", seed=seed_file.name)},
        env,
    )
    first = await pool.get("orders_db")
    second = await pool.get("orders_db")
    assert first is second
    assert await first.query("SELECT COUNT(*) AS n FROM orders", {}) == [{"n": 2}]
    await pool.close()


@pytest.mark.postgres
async def test_real_postgres_roundtrip():
    dsn = os.environ.get("CEREBELLUM_TEST_PG_DSN")
    if not dsn:
        pytest.skip("CEREBELLUM_TEST_PG_DSN is not set")
    connector = PostgresConnector("pg", dsn)
    assert await connector.query("SELECT :x::int + 1 AS y", {"x": 1}) == [{"y": 2}]
    assert (await connector.health()).ok
    await connector.close()


def test_unknown_connector_type_is_reported(tmp_path):
    class Fake:
        type = "kafka"

    from cerebellum.errors import CerebellumError

    with pytest.raises(CerebellumError, match="kafka"):
        create_connector("x", Fake(), ConnectorEnv(home=tmp_path, base_dir=Path(".")))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/connectors -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebellum.connectors'`.

- [ ] **Step 3: Implement `base.py` and `__init__.py`**

`src/cerebellum/connectors/base.py`:

```python
"""Connector contracts, registry and a lazily-opening connector pool."""

from __future__ import annotations

import abc
import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from cerebellum.errors import CerebellumError, StepError


class ConnectorError(StepError):
    """A connector call failed; `retryable` follows the error-classification table."""


@dataclass(frozen=True)
class HealthStatus:
    ok: bool
    detail: str
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ConnectorEnv:
    home: Path
    base_dir: Path
    http_transports: Mapping[str, httpx.AsyncBaseTransport] = field(default_factory=dict)


class Connector(abc.ABC):
    def __init__(self, name: str):
        self.name = name

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @abc.abstractmethod
    async def health(self) -> HealthStatus: ...


class SqlConnector(Connector):
    @abc.abstractmethod
    async def query(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def execute(self, sql: str, params: Mapping[str, Any]) -> int: ...


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any
    headers: dict[str, str]
    elapsed_ms: float


class HttpConnector(Connector):
    @property
    def default_headers(self) -> dict[str, str]:
        return {}

    @abc.abstractmethod
    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> HttpResponse: ...


Factory = Callable[[str, Any, ConnectorEnv], Connector]
_REGISTRY: dict[str, Factory] = {}


def register_connector(type_name: str) -> Callable[[Factory], Factory]:
    def decorator(factory: Factory) -> Factory:
        _REGISTRY[type_name] = factory
        return factory

    return decorator


def create_connector(name: str, spec: Any, env: ConnectorEnv) -> Connector:
    factory = _REGISTRY.get(spec.type)
    if factory is None:
        raise CerebellumError(f"no connector registered for type {spec.type!r}")
    return factory(name, spec, env)


def jsonable(value: Any) -> Any:
    """Convert driver values (Decimal, dates, bytes, UUID) into JSON-friendly values."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, UUID):
        return str(value)
    return value


class ConnectorPool:
    """Opens each connector on first use and closes them all together."""

    def __init__(self, specs: Mapping[str, Any], env: ConnectorEnv):
        self._specs = dict(specs)
        self._env = env
        self._open: dict[str, Connector] = {}
        self._lock = asyncio.Lock()

    async def get(self, name: str) -> Connector:
        async with self._lock:
            if name not in self._open:
                if name not in self._specs:
                    raise CerebellumError(f"connector {name!r} is not declared")
                connector = create_connector(name, self._specs[name], self._env)
                await connector.open()
                self._open[name] = connector
            return self._open[name]

    async def close(self) -> None:
        connectors, self._open = list(self._open.values()), {}
        for connector in connectors:
            try:
                await connector.close()
            except Exception:  # closing is best effort; the run outcome is already recorded
                pass
```

`src/cerebellum/connectors/__init__.py`:

```python
"""Connectors to external systems. Importing this package registers the built-in types."""

from cerebellum.connectors import postgres as _postgres  # noqa: F401  (registers "postgres")
from cerebellum.connectors import rest as _rest  # noqa: F401  (registers "rest")
from cerebellum.connectors.base import (
    Connector,
    ConnectorEnv,
    ConnectorError,
    ConnectorPool,
    HealthStatus,
    HttpConnector,
    HttpResponse,
    SqlConnector,
    create_connector,
    jsonable,
    register_connector,
)

__all__ = [
    "Connector",
    "ConnectorEnv",
    "ConnectorError",
    "ConnectorPool",
    "HealthStatus",
    "HttpConnector",
    "HttpResponse",
    "SqlConnector",
    "create_connector",
    "jsonable",
    "register_connector",
]
```

- [ ] **Step 4: Implement `postgres.py`**

`src/cerebellum/connectors/postgres.py`:

```python
"""PostgreSQL connector (psycopg 3) plus a SQLite sandbox used when `dsn: sandbox`."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from cerebellum.connectors.base import (
    ConnectorEnv,
    ConnectorError,
    HealthStatus,
    SqlConnector,
    jsonable,
    register_connector,
)

SANDBOX_DSN = "sandbox"
T = TypeVar("T")


def sandbox_db_path(home: Path, connector_name: str) -> Path:
    return home / f"sandbox_{connector_name}.db"


def to_pyformat(sql: str) -> str:
    """Convert `:name` placeholders to psycopg's `%(name)s`, escape literal `%`, and leave
    `::casts` and quoted text untouched."""
    out: list[str] = []
    i, n, quote = 0, len(sql), ""
    while i < n:
        ch = sql[i]
        if quote:
            out.append("%%" if ch == "%" else ch)
            if ch == quote:
                quote = ""
            i += 1
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
        elif ch == "%":
            out.append("%%")
            i += 1
        elif sql.startswith("::", i):
            out.append("::")
            i += 2
        elif ch == ":" and i + 1 < n and (sql[i + 1].isalpha() or sql[i + 1] == "_"):
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            out.append(f"%({sql[i + 1 : j]})s")
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class SqliteSandboxConnector(SqlConnector):
    """Runs the same parameterised SQL against a local SQLite file seeded on first use."""

    def __init__(self, name: str, path: Path, seed: Path | None):
        super().__init__(name)
        self.path = path
        self.seed = seed
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._conn is None:
            self._conn = await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> sqlite3.Connection:
        fresh = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        if fresh and self.seed is not None:
            try:
                conn.executescript(self.seed.read_text(encoding="utf-8"))
                conn.commit()
            except (OSError, sqlite3.Error) as exc:
                conn.close()
                self.path.unlink(missing_ok=True)
                raise ConnectorError(
                    f"cannot seed sandbox database from {self.seed}: {exc}",
                    retryable=False,
                    kind="seed",
                ) from exc
        return conn

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    async def query(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        def op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(sql, dict(params)).fetchall()
            return [{key: jsonable(row[key]) for key in row.keys()} for row in rows]

        return await self._run(op)

    async def execute(self, sql: str, params: Mapping[str, Any]) -> int:
        def op(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(sql, dict(params))
            conn.commit()
            return cursor.rowcount

        return await self._run(op)

    async def _run(self, op: Callable[[sqlite3.Connection], T]) -> T:
        await self.open()
        assert self._conn is not None
        async with self._lock:
            try:
                return await asyncio.to_thread(op, self._conn)
            except sqlite3.OperationalError as exc:
                transient = "locked" in str(exc) or "busy" in str(exc)
                raise ConnectorError(f"sqlite error: {exc}", retryable=transient, kind="sql") from exc
            except sqlite3.Error as exc:
                raise ConnectorError(f"sqlite error: {exc}", retryable=False, kind="sql") from exc

    async def health(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            await self.query("SELECT 1 AS ok", {})
        except ConnectorError as exc:
            return HealthStatus(False, str(exc))
        elapsed = (time.perf_counter() - started) * 1000
        return HealthStatus(True, f"sqlite sandbox at {self.path}", elapsed)


class PostgresConnector(SqlConnector):
    def __init__(self, name: str, dsn: str):
        super().__init__(name)
        self.dsn = dsn
        self._conn: Any = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._conn is not None:
            return
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise ConnectorError(
                "PostgreSQL support requires: pip install 'cerebellum[postgres]'",
                retryable=False,
                kind="dependency",
            ) from exc
        try:
            self._conn = await psycopg.AsyncConnection.connect(
                self.dsn, autocommit=True, row_factory=dict_row
            )
        except psycopg.OperationalError as exc:
            raise ConnectorError(
                f"cannot connect to postgres: {exc}", retryable=True, kind="connection"
            ) from exc

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await conn.close()

    async def query(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        return await self._run(sql, params, fetch=True)

    async def execute(self, sql: str, params: Mapping[str, Any]) -> int:
        return await self._run(sql, params, fetch=False)

    async def _run(self, sql: str, params: Mapping[str, Any], *, fetch: bool) -> Any:
        import psycopg

        await self.open()
        async with self._lock:
            try:
                async with self._conn.cursor() as cursor:
                    await cursor.execute(to_pyformat(sql), dict(params))
                    if fetch:
                        rows = await cursor.fetchall()
                        return [{key: jsonable(value) for key, value in row.items()} for row in rows]
                    return cursor.rowcount
            except psycopg.OperationalError as exc:
                self._conn = None  # reconnect on the next attempt
                raise ConnectorError(
                    f"postgres connection error: {exc}", retryable=True, kind="connection"
                ) from exc
            except psycopg.Error as exc:
                raise ConnectorError(f"postgres error: {exc}", retryable=False, kind="sql") from exc

    async def health(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            await self.query("SELECT 1 AS ok", {})
        except ConnectorError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(True, "postgres reachable", (time.perf_counter() - started) * 1000)


@register_connector("postgres")
def _make_postgres(name: str, spec: Any, env: ConnectorEnv) -> SqlConnector:
    if spec.dsn == SANDBOX_DSN:
        seed = env.base_dir / spec.seed if spec.seed else None
        return SqliteSandboxConnector(name, sandbox_db_path(env.home, name), seed)
    return PostgresConnector(name, spec.dsn)
```

- [ ] **Step 5: Implement `rest.py`**

`src/cerebellum/connectors/rest.py`:

```python
"""REST connector on httpx with error classification and header redaction."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

import httpx

from cerebellum.connectors.base import (
    ConnectorEnv,
    ConnectorError,
    HealthStatus,
    HttpConnector,
    HttpResponse,
    register_connector,
)

REDACTED = "***"
_SENSITIVE = re.compile(r"authorization|token|secret|password|cookie|key", re.I)
_NOT_SENSITIVE = {"idempotency-key"}
_RESPONSE_HEADERS = ("content-type", "location", "retry-after", "x-request-id", "idempotency-key")
_MAX_TEXT_BODY = 2000


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        key: REDACTED
        if key.lower() not in _NOT_SENSITIVE and _SENSITIVE.search(key)
        else value
        for key, value in headers.items()
    }


def _decode_body(response: httpx.Response) -> Any:
    if not response.content:
        return None
    if "json" in response.headers.get("content-type", ""):
        try:
            return response.json()
        except ValueError:
            pass
    text = response.text
    return text if len(text) <= _MAX_TEXT_BODY else text[:_MAX_TEXT_BODY] + "…"


class RestConnector(HttpConnector):
    def __init__(self, name: str, spec: Any, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(name)
        self.spec = spec
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def default_headers(self) -> dict[str, str]:
        return dict(self.spec.headers)

    async def open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.spec.base_url,
                headers=self.spec.headers,
                timeout=self.spec.timeout,
                transport=self._transport,
            )

    async def close(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> HttpResponse:
        await self.open()
        assert self._client is not None
        merged = dict(headers or {})
        if idempotency_key:
            merged["Idempotency-Key"] = idempotency_key
        started = time.perf_counter()
        try:
            response = await self._client.request(
                method, path, json=json, headers=merged, params=dict(query) if query else None
            )
        except httpx.TimeoutException as exc:
            raise ConnectorError(f"{method} {path} timed out", retryable=True, kind="timeout") from exc
        except httpx.TransportError as exc:
            raise ConnectorError(
                f"{method} {path} connection failed: {exc}", retryable=True, kind="connection"
            ) from exc
        elapsed = (time.perf_counter() - started) * 1000
        result = HttpResponse(
            status=response.status_code,
            body=_decode_body(response),
            headers={k: v for k, v in response.headers.items() if k.lower() in _RESPONSE_HEADERS},
            elapsed_ms=elapsed,
        )
        if response.status_code >= 400:
            retryable = response.status_code >= 500 or response.status_code in (408, 429)
            raise ConnectorError(
                f"{method} {path} returned HTTP {response.status_code}",
                retryable=retryable,
                kind="http_status",
                details={"status": result.status, "body": result.body, "elapsed_ms": elapsed},
            )
        return result

    async def health(self) -> HealthStatus:
        await self.open()
        assert self._client is not None
        started = time.perf_counter()
        target = f"{self.spec.base_url}{self.spec.health_path}"
        try:
            response = await self._client.get(self.spec.health_path, timeout=min(self.spec.timeout, 5.0))
        except httpx.HTTPError as exc:
            return HealthStatus(False, f"{target} unreachable: {exc}")
        elapsed = (time.perf_counter() - started) * 1000
        return HealthStatus(
            response.status_code < 400, f"GET {target} → HTTP {response.status_code}", elapsed
        )


@register_connector("rest")
def _make_rest(name: str, spec: Any, env: ConnectorEnv) -> RestConnector:
    return RestConnector(name, spec, transport=env.http_transports.get(name))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/connectors -q`
Expected: all PASS, `test_real_postgres_roundtrip` SKIPPED (no DSN) or deselected.

- [ ] **Step 7: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 7: Sandbox payments API

**Files:**
- Create: `src/cerebellum/sandbox/payments.py`, `src/cerebellum/sandbox/server.py`
- Test: `tests/sandbox/test_sandbox.py`

**Interfaces:**
- Consumes: `CerebellumError`.
- Produces: `SERVICE_NAME = "cerebellum-sandbox-payments"`, `FailMode.parse(text) -> FailMode` (`never | always | first:N | rate:P`, `str()` round-trips), `PaymentsState(fail=None, *, seed=None)` with `refunds: dict`, `set_fail_mode(mode)`, `replay(key)`, `should_fail(key)`, `create(key, order_id, amount, currency, reason) -> dict`; `create_payments_app(state=None) -> FastAPI` (`GET /health`, `POST /refunds` honouring `Idempotency-Key`, `GET /refunds/{id}`, `PUT /_sandbox/fail-mode`); `SandboxHandle(url, owned)` with `set_fail_mode(mode)`, `stop()`, context manager; `sandbox_running(url) -> bool`; `start_sandbox(host, port, fail="never") -> SandboxHandle`.

- [ ] **Step 1: Write the failing tests**

`tests/sandbox/test_sandbox.py`:

```python
import httpx
import pytest
from fastapi.testclient import TestClient

from cerebellum.errors import CerebellumError
from cerebellum.sandbox.payments import FailMode, PaymentsState, create_payments_app
from cerebellum.sandbox.server import sandbox_running, start_sandbox

REFUND = {"order_id": "A1001", "amount": 120.0}


@pytest.mark.parametrize(
    ("text", "expected"),
    [("never", FailMode("never")), ("always", FailMode("always")),
     ("first:2", FailMode("first", n=2)), ("rate:0.3", FailMode("rate", p=0.3)),
     (" FIRST:0 ", FailMode("first", n=0))],
)
def test_fail_mode_parse(text, expected):
    mode = FailMode.parse(text)
    assert mode == expected
    assert FailMode.parse(str(mode)) == mode


@pytest.mark.parametrize("text", ["sometimes", "first:", "first:-1", "rate:2", "rate:x", ""])
def test_fail_mode_rejects_invalid(text):
    with pytest.raises(ValueError, match="invalid fail mode"):
        FailMode.parse(text)


def client_for(state=None):
    return TestClient(create_payments_app(state or PaymentsState()))


def test_create_and_replay_by_idempotency_key():
    client = client_for()
    first = client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "r_1:pay"})
    assert first.status_code == 201
    body = first.json()
    assert body["id"].startswith("rf_") and body["status"] == "succeeded"
    replay = client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "r_1:pay"})
    assert replay.status_code == 200 and replay.json()["id"] == body["id"]
    other = client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "r_2:pay"})
    assert other.json()["id"] != body["id"]
    assert client.get(f"/refunds/{body['id']}").json()["order_id"] == "A1001"
    assert client.get("/refunds/rf_missing").status_code == 404


def test_first_n_fails_per_key_then_succeeds():
    client = client_for(PaymentsState(FailMode.parse("first:2")))
    codes = [
        client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "k1"}).status_code
        for _ in range(3)
    ]
    assert codes == [503, 503, 201]
    assert client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "k2"}).status_code == 503


def test_always_and_switching_fail_mode():
    state = PaymentsState(FailMode.parse("always"))
    client = client_for(state)
    assert client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "k"}).status_code == 503
    assert client.put("/_sandbox/fail-mode", json={"mode": "never"}).json() == {"fail_mode": "never"}
    assert client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "k"}).status_code == 201
    assert client.put("/_sandbox/fail-mode", json={"mode": "bogus"}).status_code == 422
    assert len(state.refunds) == 1


def test_validation_errors_are_422():
    client = client_for()
    assert client.post("/refunds", json={"order_id": "A1", "amount": 0}).status_code == 422


def test_health_reports_service_and_mode():
    body = client_for(PaymentsState(FailMode.parse("first:1"))).get("/health").json()
    assert body == {"ok": True, "service": "cerebellum-sandbox-payments", "fail_mode": "first:1",
                    "refunds": 0}


def test_start_sandbox_runs_in_background_and_is_reused(free_port):
    handle = start_sandbox("127.0.0.1", free_port, "never")
    try:
        assert handle.owned and sandbox_running(handle.url)
        reused = start_sandbox("127.0.0.1", free_port, "first:1")
        assert reused.owned is False
        assert httpx.get(f"{handle.url}/health").json()["fail_mode"] == "first:1"
        reused.stop()  # no-op for a borrowed sandbox
        assert sandbox_running(handle.url)
    finally:
        handle.stop()
    assert not sandbox_running(handle.url)


def test_start_sandbox_refuses_a_port_used_by_something_else(free_port):
    import socket

    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", free_port))
        blocker.listen()
        with pytest.raises(CerebellumError, match="in use by another service"):
            start_sandbox("127.0.0.1", free_port)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/sandbox -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebellum.sandbox.payments'`.

- [ ] **Step 3: Implement**

`src/cerebellum/sandbox/payments.py`:

```python
"""Local mock payments API used by the demo, tests and evals.

It honours `Idempotency-Key` (a replayed key returns the original refund) and supports fault
injection so retries and fallbacks can be demonstrated deterministically."""

from __future__ import annotations

import random
import secrets
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

SERVICE_NAME = "cerebellum-sandbox-payments"


@dataclass(frozen=True)
class FailMode:
    kind: Literal["never", "first", "always", "rate"] = "never"
    n: int = 0
    p: float = 0.0

    @classmethod
    def parse(cls, text: str) -> FailMode:
        raw = text.strip().lower()
        if raw == "never":
            return cls("never")
        if raw == "always":
            return cls("always")
        name, _, arg = raw.partition(":")
        try:
            if name == "first" and arg and int(arg) >= 0:
                return cls("first", n=int(arg))
            if name == "rate" and arg and 0.0 <= float(arg) <= 1.0:
                return cls("rate", p=float(arg))
        except ValueError:
            pass
        raise ValueError(f"invalid fail mode {text!r}; use never, always, first:N or rate:P")

    def __str__(self) -> str:
        if self.kind == "first":
            return f"first:{self.n}"
        if self.kind == "rate":
            return f"rate:{self.p:g}"
        return self.kind


class PaymentsState:
    def __init__(self, fail: FailMode | None = None, *, seed: int | None = None):
        self.fail = fail or FailMode()
        self.refunds: dict[str, dict[str, Any]] = {}
        self._by_key: dict[str, str] = {}
        self._attempts: Counter[str] = Counter()
        self._rng = random.Random(seed)
        self._lock = threading.Lock()

    def set_fail_mode(self, mode: FailMode) -> None:
        with self._lock:
            self.fail = mode
            self._attempts.clear()

    def replay(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            refund_id = self._by_key.get(key)
            return self.refunds[refund_id] if refund_id else None

    def should_fail(self, key: str) -> bool:
        with self._lock:
            self._attempts[key] += 1
            attempt = self._attempts[key]
            if self.fail.kind == "always":
                return True
            if self.fail.kind == "first":
                return attempt <= self.fail.n
            if self.fail.kind == "rate":
                return self._rng.random() < self.fail.p
            return False

    def create(
        self, key: str, order_id: str, amount: float, currency: str, reason: str | None
    ) -> dict[str, Any]:
        with self._lock:
            if key in self._by_key:
                return self.refunds[self._by_key[key]]
            refund_id = "rf_" + secrets.token_hex(5)
            record = {
                "id": refund_id,
                "order_id": order_id,
                "amount": amount,
                "currency": currency,
                "reason": reason,
                "status": "succeeded",
                "idempotency_key": key,
                "created_at": time.time(),
            }
            self.refunds[refund_id] = record
            self._by_key[key] = refund_id
            return record


class RefundRequest(BaseModel):
    order_id: str = Field(min_length=1)
    amount: float = Field(gt=0)
    currency: str = "USD"
    reason: str | None = None


class FailModeRequest(BaseModel):
    mode: str


def create_payments_app(state: PaymentsState | None = None) -> FastAPI:
    app = FastAPI(title="Cerebellum Sandbox Payments", docs_url=None, redoc_url=None)
    payments = state or PaymentsState()
    app.state.payments = payments

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": SERVICE_NAME,
            "fail_mode": str(payments.fail),
            "refunds": len(payments.refunds),
        }

    @app.post("/refunds", status_code=201)
    def create_refund(
        body: RefundRequest, idempotency_key: str | None = Header(default=None)
    ) -> Any:
        key = idempotency_key or "anon_" + secrets.token_hex(6)
        existing = payments.replay(key)
        if existing is not None:
            return JSONResponse(existing, status_code=200)
        if payments.should_fail(key):
            raise HTTPException(503, detail="payments provider unavailable (sandbox fault injection)")
        return payments.create(key, body.order_id, body.amount, body.currency, body.reason)

    @app.get("/refunds/{refund_id}")
    def get_refund(refund_id: str) -> dict[str, Any]:
        record = payments.refunds.get(refund_id)
        if record is None:
            raise HTTPException(404, detail=f"refund {refund_id} not found")
        return record

    @app.put("/_sandbox/fail-mode")
    def set_fail_mode(body: FailModeRequest) -> dict[str, str]:
        try:
            mode = FailMode.parse(body.mode)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        payments.set_fail_mode(mode)
        return {"fail_mode": str(mode)}

    return app
```

`src/cerebellum/sandbox/server.py`:

```python
"""Run the sandbox payments API in a background thread, or reuse one that is already up."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass

import httpx
import uvicorn

from cerebellum.errors import CerebellumError
from cerebellum.sandbox.payments import SERVICE_NAME, FailMode, PaymentsState, create_payments_app


@dataclass
class SandboxHandle:
    url: str
    owned: bool
    _server: uvicorn.Server | None = None
    _thread: threading.Thread | None = None

    def set_fail_mode(self, mode: str) -> None:
        FailMode.parse(mode)
        response = httpx.put(f"{self.url}/_sandbox/fail-mode", json={"mode": mode}, timeout=5.0)
        response.raise_for_status()

    def stop(self) -> None:
        if self.owned and self._server is not None:
            self._server.should_exit = True
            if self._thread is not None:
                self._thread.join(timeout=5.0)

    def __enter__(self) -> SandboxHandle:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def sandbox_running(url: str) -> bool:
    try:
        response = httpx.get(f"{url}/health", timeout=0.5)
        return response.status_code == 200 and response.json().get("service") == SERVICE_NAME
    except (httpx.HTTPError, ValueError):
        return False


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def start_sandbox(host: str, port: int, fail: str = "never") -> SandboxHandle:
    url = f"http://{host}:{port}"
    if sandbox_running(url):
        handle = SandboxHandle(url, owned=False)
        handle.set_fail_mode(fail)
        return handle
    if _port_in_use(host, port):
        raise CerebellumError(
            f"port {port} is in use by another service; set CEREBELLUM_SANDBOX_PORT"
        )
    app = create_payments_app(PaymentsState(FailMode.parse(fail)))
    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", lifespan="off", access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="cerebellum-sandbox", daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            server.should_exit = True
            raise CerebellumError(f"sandbox payments API failed to start on {url}")
        time.sleep(0.02)
    return SandboxHandle(url, owned=True, _server=server, _thread=thread)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/sandbox -q`
Expected: all PASS.

- [ ] **Step 5: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 8: AI providers (pricing, mock, Claude structured outputs)

**Files:**
- Create: `src/cerebellum/ai/__init__.py`, `src/cerebellum/ai/base.py`, `src/cerebellum/ai/pricing.py`, `src/cerebellum/ai/mock.py`, `src/cerebellum/ai/anthropic_provider.py`
- Test: `tests/ai/test_ai.py`

**Interfaces:**
- Consumes: `StepError`, `MockRule` (Task 3), expressions (Task 2), `minimal_instance` (Task 3), `Settings`, `has_anthropic_credentials` (Task 1).
- Produces: `AIError(StepError)`, `Usage(input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0)`, `AIRequest(model, prompt, schema, system=None, effort=None, max_tokens=4096, mock_rules=(), context={})`, `AIResult(text, model, usage, cost_usd, mock, stop_reason, latency_ms)`, `AIProvider` protocol (`name: str`, `mock: bool`, `async generate(request, messages) -> AIResult`); `ModelPrice`, `DEFAULT_PRICES`, `Pricing(prices=None)` with `load(path)` and `cost(model, usage)`; `MockProvider(*, latency=(0.05, 0.3), sleep=asyncio.sleep, seed=7)`; `AnthropicProvider(client=None, *, pricing=None, refusal_fallbacks=True)` with `build_params(request, messages)`; `REFUSAL_FALLBACK_BETA`, `REFUSAL_FALLBACK_MODELS`; `ProviderChoice(provider, reason)`, `select_provider(settings, *, force_mock=False, env=None, config_dir=None)`.

- [ ] **Step 1: Write the failing tests**

`tests/ai/test_ai.py`:

```python
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from cerebellum.ai import ProviderChoice, select_provider
from cerebellum.ai.anthropic_provider import REFUSAL_FALLBACK_BETA, AnthropicProvider
from cerebellum.ai.base import AIError, AIRequest, Usage
from cerebellum.ai.mock import MockProvider
from cerebellum.ai.pricing import DEFAULT_PRICES, ModelPrice, Pricing
from cerebellum.config import Settings
from cerebellum.spec.models import MockRule

SCHEMA = {
    "type": "object",
    "required": ["eligible", "risk"],
    "additionalProperties": False,
    "properties": {"eligible": {"type": "boolean"}, "risk": {"enum": ["low", "high"]}},
}


def test_default_prices_include_current_models():
    assert DEFAULT_PRICES["claude-opus-5-5"] == ModelPrice(4.0, 20.0, cache_read=0.20)
    assert DEFAULT_PRICES["claude-sonnet-5-5"].input == 2.0
    assert DEFAULT_PRICES["claude-haiku-4-5"].output == 5.0


def test_pricing_cost():
    pricing = Pricing()
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert pricing.cost("claude-opus-5-5", usage) == pytest.approx(24.0)
    cached = Usage(cache_read_input_tokens=1_000_000, cache_creation_input_tokens=1_000_000)
    assert pricing.cost("claude-opus-5-5", cached) == pytest.approx(0.20 + 5.0)
    assert pricing.cost("unknown-model", usage) == 0.0


def test_pricing_file_override(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"claude-opus-5-5": {"input": 1, "output": 2}, "custom": {"input": 3, "output": 4}}))
    pricing = Pricing.load(path)
    assert pricing.cost("claude-opus-5-5", Usage(input_tokens=1_000_000)) == pytest.approx(1.0)
    assert pricing.cost("custom", Usage(output_tokens=1_000_000)) == pytest.approx(4.0)
    assert Pricing.load(None).prices == DEFAULT_PRICES


def request(**overrides):
    base = dict(model="claude-opus-5-5", prompt="Assess", schema=SCHEMA,
                context={"input": {"reason": "Fraud attempt", "amount": 900}, "params": {"t": 500}})
    base.update(overrides)
    return AIRequest(**base)


async def test_mock_picks_first_matching_rule_and_renders_templates():
    rules = (
        MockRule(when="'fraud' in input.reason | lower", output={"eligible": False, "risk": "high", "note": "{{ input.amount }}"}),
        MockRule(output={"eligible": True, "risk": "low"}),
    )
    result = await MockProvider(latency=(0, 0)).generate(request(mock_rules=rules), [])
    assert json.loads(result.text) == {"eligible": False, "risk": "high", "note": 900}
    assert result.mock is True and result.cost_usd == 0.0 and result.usage == Usage()

    calm = request(mock_rules=rules, context={"input": {"reason": "late", "amount": 1}})
    assert json.loads((await MockProvider(latency=(0, 0)).generate(calm, [])).text)["risk"] == "low"


async def test_mock_without_rules_returns_minimal_valid_instance():
    result = await MockProvider(latency=(0, 0)).generate(request(), [])
    assert json.loads(result.text) == {"eligible": False, "risk": "low"}


async def test_mock_simulates_latency_through_injected_sleep():
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    await MockProvider(latency=(0.1, 0.2), sleep=fake_sleep).generate(request(), [])
    assert len(delays) == 1 and 0.1 <= delays[0] <= 0.2


class FakeMessages:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def create(self, **params):
        self.calls.append(params)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def fake_client(outcome):
    messages = FakeMessages(outcome)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def message(stop_reason="end_turn", text='{"eligible": true, "risk": "low"}', model="claude-opus-5-5", stop_details=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        model=model,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=None,
                              cache_creation_input_tokens=0),
    )


async def test_anthropic_success_builds_structured_output_request_and_prices_usage():
    client, messages = fake_client(message())
    provider = AnthropicProvider(client)
    history = [{"role": "user", "content": "Assess"}]
    result = await provider.generate(request(system="Be strict", effort="low"), history)
    params = messages.calls[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["messages"] == history
    assert params["system"] == "Be strict"
    assert params["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"}
    assert params["betas"] == [REFUSAL_FALLBACK_BETA] and params["fallbacks"] == "default"
    assert result.text == '{"eligible": true, "risk": "low"}'
    assert result.usage == Usage(input_tokens=1000, output_tokens=200)
    assert result.cost_usd == pytest.approx((1000 * 4 + 200 * 20) / 1_000_000)
    assert result.mock is False and result.model == "claude-opus-5-5"


def test_refusal_fallbacks_only_for_supported_models():
    provider = AnthropicProvider(fake_client(message())[0])
    params = provider.build_params(request(model="claude-haiku-4-5"), [])
    assert "betas" not in params and "fallbacks" not in params
    assert "system" not in params and "effort" not in params["output_config"]
    off = AnthropicProvider(fake_client(message())[0], refusal_fallbacks=False)
    assert "fallbacks" not in off.build_params(request(), [])


async def test_refusal_is_not_retryable_and_keeps_category():
    client, _ = fake_client(message(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber")))
    with pytest.raises(AIError, match="refusal category: cyber") as info:
        await AnthropicProvider(client).generate(request(), [])
    assert info.value.retryable is False and info.value.kind == "refusal"
    assert info.value.details == {"category": "cyber"}


async def test_max_tokens_is_not_retryable():
    client, _ = fake_client(message(stop_reason="max_tokens"))
    with pytest.raises(AIError, match="max_tokens") as info:
        await AnthropicProvider(client).generate(request(), [])
    assert info.value.retryable is False


def _api_error(cls, status):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls("boom", response=response, body=None)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (_api_error(anthropic.RateLimitError, 429), True),
        (_api_error(anthropic.InternalServerError, 500), True),
        (_api_error(anthropic.BadRequestError, 400), False),
        (_api_error(anthropic.AuthenticationError, 401), False),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), True),
    ],
)
async def test_api_errors_are_classified(error, retryable):
    client, _ = fake_client(error)
    with pytest.raises(AIError) as info:
        await AnthropicProvider(client).generate(request(), [])
    assert info.value.retryable is retryable


def test_select_provider(tmp_path):
    settings = Settings.from_env({"CEREBELLUM_HOME": str(tmp_path)})
    empty_profile = tmp_path / "no-profile"
    forced = select_provider(settings, force_mock=True, env={"ANTHROPIC_API_KEY": "k"}, config_dir=empty_profile)
    assert isinstance(forced, ProviderChoice) and forced.provider.mock and "requested" in forced.reason
    env_forced = select_provider(Settings.from_env({"CEREBELLUM_MOCK": "1"}), env={"ANTHROPIC_API_KEY": "k"}, config_dir=empty_profile)
    assert env_forced.provider.mock
    real = select_provider(settings, env={"ANTHROPIC_API_KEY": "k"}, config_dir=empty_profile)
    assert isinstance(real.provider, AnthropicProvider) and "claude-opus-5-5" in real.reason
    fallback = select_provider(settings, env={}, config_dir=empty_profile)
    assert fallback.provider.mock and "no Anthropic credentials" in fallback.reason


@pytest.mark.live
async def test_live_claude_structured_output():
    result = await AnthropicProvider().generate(
        request(prompt='Return {"eligible": true, "risk": "low"}.'),
        [{"role": "user", "content": 'Return {"eligible": true, "risk": "low"}.'}],
    )
    assert json.loads(result.text)["risk"] in ("low", "high")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/ai -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebellum.ai'`.

- [ ] **Step 3: Implement**

`src/cerebellum/ai/base.py`:

```python
"""Provider-agnostic AI contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from cerebellum.errors import StepError
from cerebellum.spec.models import MockRule


class AIError(StepError):
    """An AI provider call failed."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class AIRequest:
    model: str
    prompt: str
    schema: dict[str, Any]
    system: str | None = None
    effort: str | None = None
    max_tokens: int = 4096
    mock_rules: tuple[MockRule, ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIResult:
    text: str
    model: str
    usage: Usage
    cost_usd: float
    mock: bool
    stop_reason: str
    latency_ms: float


class AIProvider(Protocol):
    name: str
    mock: bool

    async def generate(self, request: AIRequest, messages: list[dict[str, Any]]) -> AIResult: ...
```

`src/cerebellum/ai/pricing.py`:

```python
"""USD prices per million tokens. Override or extend with CEREBELLUM_PRICING_FILE (JSON:
{"model": {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0}})."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cerebellum.ai.base import Usage


@dataclass(frozen=True)
class ModelPrice:
    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None

    def cost(self, usage: Usage) -> float:
        cache_read = self.cache_read if self.cache_read is not None else self.input * 0.1
        cache_write = self.cache_write if self.cache_write is not None else self.input * 1.25
        total = (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_read_input_tokens * cache_read
            + usage.cache_creation_input_tokens * cache_write
        )
        return round(total / 1_000_000, 8)


DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-fable-5-1": ModelPrice(10.0, 50.0),
    "claude-opus-5-5": ModelPrice(4.0, 20.0, cache_read=0.20),
    "claude-opus-5": ModelPrice(5.0, 25.0),
    "claude-sonnet-5-5": ModelPrice(2.0, 10.0, cache_read=0.20),
    "claude-sonnet-5": ModelPrice(2.0, 10.0),
    "claude-haiku-4-5": ModelPrice(1.0, 5.0),
}


class Pricing:
    def __init__(self, prices: dict[str, ModelPrice] | None = None):
        self.prices = dict(DEFAULT_PRICES if prices is None else prices)

    @classmethod
    def load(cls, path: Path | None) -> Pricing:
        pricing = cls()
        if path is None:
            return pricing
        data = json.loads(path.read_text(encoding="utf-8"))
        for model, entry in data.items():
            pricing.prices[model] = ModelPrice(
                float(entry["input"]),
                float(entry["output"]),
                entry.get("cache_read"),
                entry.get("cache_write"),
            )
        return pricing

    def cost(self, model: str, usage: Usage) -> float:
        price = self.prices.get(model)
        return price.cost(usage) if price else 0.0
```

`src/cerebellum/ai/mock.py`:

```python
"""Deterministic offline provider driven by the step's explicit `mock:` rules."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from cerebellum.ai.base import AIRequest, AIResult, Usage
from cerebellum.spec.expressions import eval_condition, render
from cerebellum.spec.schemas import minimal_instance


class MockProvider:
    name = "mock"
    mock = True

    def __init__(
        self,
        *,
        latency: tuple[float, float] = (0.05, 0.3),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        seed: int = 7,
    ):
        self._latency = latency
        self._sleep = sleep
        self._rng = random.Random(seed)

    async def generate(self, request: AIRequest, messages: list[dict[str, Any]]) -> AIResult:
        started = time.perf_counter()
        low, high = self._latency
        if high > 0:
            await self._sleep(self._rng.uniform(low, high))
        output = self._pick(request)
        return AIResult(
            text=json.dumps(output, ensure_ascii=False),
            model=request.model,
            usage=Usage(),
            cost_usd=0.0,
            mock=True,
            stop_reason="end_turn",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    @staticmethod
    def _pick(request: AIRequest) -> Any:
        for rule in request.mock_rules:
            if rule.when is None or eval_condition(rule.when, request.context):
                return render(rule.output, request.context, strict=False)
        return minimal_instance(request.schema)
```

`src/cerebellum/ai/anthropic_provider.py`:

```python
"""Claude provider using native structured outputs (output_config.format = json_schema)."""

from __future__ import annotations

import time
from typing import Any

import anthropic

from cerebellum.ai.base import AIError, AIRequest, AIResult, Usage
from cerebellum.ai.pricing import Pricing

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept the server-side refusal fallback ("default" routing).
REFUSAL_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


class AnthropicProvider:
    name = "anthropic"
    mock = False

    def __init__(
        self,
        client: Any | None = None,
        *,
        pricing: Pricing | None = None,
        refusal_fallbacks: bool = True,
    ):
        # The engine owns retries (with backoff and tracing), so the SDK must not retry silently.
        self._client = client if client is not None else anthropic.AsyncAnthropic(max_retries=0)
        self._pricing = pricing or Pricing()
        self._refusal_fallbacks = refusal_fallbacks

    def build_params(self, request: AIRequest, messages: list[dict[str, Any]]) -> dict[str, Any]:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": request.schema}}
        if request.effort:
            output_config["effort"] = request.effort
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
            "output_config": output_config,
        }
        if request.system:
            params["system"] = request.system
        if self._refusal_fallbacks and request.model in REFUSAL_FALLBACK_MODELS:
            params["betas"] = [REFUSAL_FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    async def generate(self, request: AIRequest, messages: list[dict[str, Any]]) -> AIResult:
        params = self.build_params(request, messages)
        started = time.perf_counter()
        try:
            message = await self._client.beta.messages.create(**params)
        except anthropic.APIStatusError as exc:
            raise AIError(
                f"Claude API error {exc.status_code}: {exc.message}",
                retryable=exc.status_code in _RETRYABLE_STATUS,
                kind="api",
                details={"status": exc.status_code},
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise AIError(f"cannot reach the Claude API: {exc}", retryable=True, kind="connection") from exc
        latency = (time.perf_counter() - started) * 1000

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise AIError(
                f"Claude declined the request (refusal category: {category or 'unspecified'})",
                retryable=False,
                kind="refusal",
                details={"category": category},
            )
        if message.stop_reason == "max_tokens":
            raise AIError(
                f"output truncated at max_tokens={request.max_tokens}; raise max_tokens on the step",
                retryable=False,
                kind="max_tokens",
            )

        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        raw = message.usage
        usage = Usage(
            input_tokens=raw.input_tokens or 0,
            output_tokens=raw.output_tokens or 0,
            cache_read_input_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
        )
        return AIResult(
            text=text,
            model=message.model,
            usage=usage,
            cost_usd=self._pricing.cost(message.model, usage),
            mock=False,
            stop_reason=message.stop_reason or "end_turn",
            latency_ms=latency,
        )
```

`src/cerebellum/ai/__init__.py`:

```python
"""AI providers and provider selection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cerebellum.ai.anthropic_provider import AnthropicProvider
from cerebellum.ai.base import AIError, AIProvider, AIRequest, AIResult, Usage
from cerebellum.ai.mock import MockProvider
from cerebellum.ai.pricing import Pricing
from cerebellum.config import Settings, has_anthropic_credentials

__all__ = [
    "AIError", "AIProvider", "AIRequest", "AIResult", "AnthropicProvider", "MockProvider",
    "ProviderChoice", "Usage", "select_provider",
]


@dataclass(frozen=True)
class ProviderChoice:
    provider: AIProvider
    reason: str


def select_provider(
    settings: Settings,
    *,
    force_mock: bool = False,
    env: Mapping[str, str] | None = None,
    config_dir: Path | None = None,
) -> ProviderChoice:
    if force_mock or settings.force_mock:
        return ProviderChoice(MockProvider(), "mock AI (requested)")
    if has_anthropic_credentials(env, config_dir):
        provider = AnthropicProvider(pricing=Pricing.load(settings.pricing_file))
        return ProviderChoice(provider, f"Claude API ({settings.model})")
    return ProviderChoice(MockProvider(), "mock AI (no Anthropic credentials found)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ai -q`
Expected: all PASS (the `live` test is deselected by `addopts`).

- [ ] **Step 5: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 9: Step executors

**Files:**
- Create: `src/cerebellum/steps/__init__.py`, `src/cerebellum/steps/base.py`, `src/cerebellum/steps/query.py`, `src/cerebellum/steps/http.py`, `src/cerebellum/steps/ai.py`, `src/cerebellum/steps/validate.py`, `src/cerebellum/steps/task.py`
- Test: `tests/steps/test_steps.py`

**Interfaces:**
- Consumes: `ConnectorPool`, `SqlConnector`, `HttpConnector`, `ConnectorError`, `redact_headers` (Task 6); `AIProvider`, `AIRequest`, `AIError` (Task 8); `render`, `eval_condition` (Task 2); `validation_errors` (Task 3); `Settings`; `StepError`.
- Produces: `StepRuntime(run_id, step, attempt, span_id, ctx, connectors, ai, settings, record_call, create_task)` where `record_call(kind: str, data: dict, cost_usd: float = 0.0) -> None` and `create_task(title, assignee, payload) -> str`; `elapsed_ms(started: float) -> float`; executors `run_query, run_http, run_ai, run_validate, run_task` (each `async (StepRuntime) -> Any`, raising `StepError` on failure); `EXECUTORS: dict[str, StepExecutor]` keyed by step type (`query, http, ai, validate, task`).
- Output contracts used by templates: query → row dict (`expect: one`) / row list / `{"rowcount": n}`; http → `{"status", "body", "headers"}`; ai → validated JSON object; validate → `{"passed": True, "checked": n}`; task → `{"task_id", "title", "assignee"}`.

- [ ] **Step 1: Write the failing tests**

`tests/steps/test_steps.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

from cerebellum.ai.base import AIError, AIResult, Usage
from cerebellum.ai.mock import MockProvider
from cerebellum.config import Settings
from cerebellum.connectors import ConnectorError
from cerebellum.connectors.postgres import SqliteSandboxConnector
from cerebellum.connectors.rest import RestConnector
from cerebellum.errors import StepError
from cerebellum.spec.models import (
    AiStep,
    HttpStep,
    MockRule,
    QueryStep,
    RestConnectorSpec,
    TaskStep,
    ValidateStep,
)
from cerebellum.steps import EXECUTORS
from cerebellum.steps.base import StepRuntime

CTX = {
    "input": {"order_id": "A1", "amount": 50, "reason": "broken"},
    "params": {"threshold": 100},
    "steps": {"load": {"output": {"id": "A1", "amount": 80}, "status": "succeeded"}},
    "run": {"id": "r_1", "cost_usd": 0.0},
}
SCHEMA = {
    "type": "object",
    "required": ["ok"],
    "additionalProperties": False,
    "properties": {"ok": {"type": "boolean"}},
}


class FakePool:
    def __init__(self, **connectors):
        self.connectors = connectors

    async def get(self, name):
        return self.connectors[name]


class Recorder:
    def __init__(self):
        self.calls = []
        self.tasks = []

    def record(self, kind, data, cost_usd=0.0):
        self.calls.append((kind, data, cost_usd))

    def create_task(self, title, assignee, payload):
        self.tasks.append((title, assignee, payload))
        return "tk_0001"


def runtime(step, *, pool=None, ai=None, recorder=None):
    recorder = recorder or Recorder()
    return StepRuntime(
        run_id="r_1",
        step=step,
        attempt=1,
        span_id=f"{step.id}#1",
        ctx=CTX,
        connectors=pool or FakePool(),
        ai=ai or MockProvider(latency=(0, 0)),
        settings=Settings.from_env({}),
        record_call=recorder.record,
        create_task=recorder.create_task,
    ), recorder


@pytest.fixture
async def orders(tmp_path):
    seed = tmp_path / "seed.sql"
    seed.write_text(
        "CREATE TABLE orders (id TEXT PRIMARY KEY, amount NUMERIC NOT NULL);"
        "INSERT INTO orders VALUES ('A1', 80), ('A2', 20);"
    )
    connector = SqliteSandboxConnector("db", tmp_path / "db.sqlite", seed)
    yield connector
    await connector.close()


def test_registry_covers_executable_step_types():
    assert set(EXECUTORS) == {"query", "http", "ai", "validate", "task"}


async def test_query_expect_one_returns_row_and_records_call(orders):
    step = QueryStep(id="load", type="query", connector="db", expect="one",
                     sql="SELECT id, amount FROM orders WHERE id = :id",
                     params={"id": "{{ input.order_id }}"})
    rt, rec = runtime(step, pool=FakePool(db=orders))
    assert await EXECUTORS["query"](rt) == {"id": "A1", "amount": 80}
    kind, data, _ = rec.calls[0]
    assert kind == "connector"
    assert data["operation"] == "query" and data["params"] == {"id": "A1"}
    assert data["ok"] is True and data["rows"] == 1


async def test_query_expect_one_with_no_rows_is_not_retryable(orders):
    step = QueryStep(id="load", type="query", connector="db", expect="one",
                     sql="SELECT id FROM orders WHERE id = :id", params={"id": "ZZ"})
    rt, _ = runtime(step, pool=FakePool(db=orders))
    with pytest.raises(StepError, match="expected exactly one row, got 0") as info:
        await EXECUTORS["query"](rt)
    assert info.value.retryable is False and info.value.kind == "expect"


async def test_query_expect_many_and_none(orders):
    many = QueryStep(id="q", type="query", connector="db", expect="many", sql="SELECT id FROM orders")
    rt, _ = runtime(many, pool=FakePool(db=orders))
    assert len(await EXECUTORS["query"](rt)) == 2
    none = QueryStep(id="q", type="query", connector="db", expect="none", sql="SELECT id FROM orders")
    rt, _ = runtime(none, pool=FakePool(db=orders))
    with pytest.raises(StepError, match="expected no rows"):
        await EXECUTORS["query"](rt)


async def test_query_write_returns_rowcount(orders):
    step = QueryStep(id="mark", type="query", connector="db",
                     sql="UPDATE orders SET amount = 0 WHERE id = :id", params={"id": "A2"})
    rt, rec = runtime(step, pool=FakePool(db=orders))
    assert await EXECUTORS["query"](rt) == {"rowcount": 1}
    assert rec.calls[0][1]["operation"] == "execute" and rec.calls[0][1]["rowcount"] == 1


async def test_query_connector_error_is_recorded_and_raised(orders):
    step = QueryStep(id="q", type="query", connector="db", sql="SELECT * FROM missing_table")
    rt, rec = runtime(step, pool=FakePool(db=orders))
    with pytest.raises(ConnectorError):
        await EXECUTORS["query"](rt)
    assert rec.calls[0][1]["ok"] is False and "missing_table" in rec.calls[0][1]["error"]


def rest(handler):
    spec = RestConnectorSpec(type="rest", base_url="http://api.test",
                             headers={"Authorization": "Bearer secret"})
    return RestConnector("api", spec, transport=httpx.MockTransport(handler))


async def test_http_success_records_redacted_request():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["idempotency-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "rf_9"})

    step = HttpStep(id="pay", type="http", connector="api", method="POST", path="/refunds",
                    body={"order_id": "{{ input.order_id }}", "amount": "{{ input.amount }}"})
    rt, rec = runtime(step, pool=FakePool(api=rest(handler)))
    assert await EXECUTORS["http"](rt) == {
        "status": 201, "body": {"id": "rf_9"}, "headers": {"content-type": "application/json"},
    }
    assert seen == {"key": "r_1:pay", "body": {"order_id": "A1", "amount": 50}}
    data = rec.calls[0][1]
    assert data["request"]["headers"]["Authorization"] == "***"
    assert data["request"]["headers"]["Idempotency-Key"] == "r_1:pay"
    assert data["status"] == 201 and data["ok"] is True


async def test_http_failure_records_status_and_body():
    step = HttpStep(id="pay", type="http", connector="api", method="POST", path="/refunds")
    rt, rec = runtime(step, pool=FakePool(api=rest(lambda r: httpx.Response(503, json={"d": 1}))))
    with pytest.raises(ConnectorError) as info:
        await EXECUTORS["http"](rt)
    assert info.value.retryable is True
    data = rec.calls[0][1]
    assert data["ok"] is False and data["status"] == 503 and data["response"]["body"] == {"d": 1}


async def test_ai_with_mock_rule_returns_validated_output():
    step = AiStep(id="judge", type="ai", prompt="Judge {{ input.order_id }}", output_schema=SCHEMA,
                  mock=[MockRule(when="input.amount < params.threshold", output={"ok": True})])
    rt, rec = runtime(step)
    assert await EXECUTORS["ai"](rt) == {"ok": True}
    kind, data, cost = rec.calls[0]
    assert kind == "llm" and data["mock"] is True and data["ok"] is True
    assert data["prompt"] == "Judge A1" and data["model"] == "claude-opus-5-5" and cost == 0.0


class ScriptedAI:
    name = "scripted"
    mock = False

    def __init__(self, *texts):
        self.texts = list(texts)
        self.seen = []

    async def generate(self, request, messages):
        self.seen.append(list(messages))
        text = self.texts.pop(0)
        return AIResult(text=text, model=request.model, usage=Usage(10, 5), cost_usd=0.01,
                        mock=False, stop_reason="end_turn", latency_ms=3.0)


async def test_ai_repairs_invalid_output_with_feedback():
    ai = ScriptedAI('{"ok": "yes"}', "not json", '{"ok": false}')
    step = AiStep(id="judge", type="ai", prompt="Judge", output_schema=SCHEMA, max_repairs=2)
    rt, rec = runtime(step, ai=ai)
    assert await EXECUTORS["ai"](rt) == {"ok": False}
    assert [len(m) for m in ai.seen] == [1, 3, 5]
    assert "failed JSON Schema validation" in ai.seen[1][-1]["content"]
    assert "ok: 'yes' is not of type 'boolean'" in ai.seen[1][-1]["content"]
    assert "not valid JSON" in ai.seen[2][-1]["content"]
    assert [c[1]["ok"] for c in rec.calls] == [False, False, True]
    assert sum(c[2] for c in rec.calls) == pytest.approx(0.03)


async def test_ai_gives_up_after_repairs_with_retryable_schema_error():
    ai = ScriptedAI('{"ok": 1}', '{"ok": 2}')
    step = AiStep(id="judge", type="ai", prompt="Judge", output_schema=SCHEMA, max_repairs=1)
    rt, _ = runtime(step, ai=ai)
    with pytest.raises(StepError, match="after 2 attempt") as info:
        await EXECUTORS["ai"](rt)
    assert info.value.retryable is True and info.value.kind == "schema"


async def test_ai_provider_error_is_recorded_and_raised():
    class Failing:
        name = "failing"
        mock = False

        async def generate(self, request, messages):
            raise AIError("declined", retryable=False, kind="refusal")

    step = AiStep(id="judge", type="ai", prompt="Judge", output_schema=SCHEMA)
    rt, rec = runtime(step, ai=Failing())
    with pytest.raises(AIError):
        await EXECUTORS["ai"](rt)
    assert rec.calls[0][1]["ok"] is False and rec.calls[0][1]["kind"] == "refusal"


async def test_validate_passes_and_lists_every_failed_rule():
    rules = [
        {"expr": "input.amount <= steps.load.output.amount", "message": "too much"},
        {"expr": "input.amount > 0", "message": "must be positive"},
    ]
    ok = ValidateStep(id="v", type="validate", rules=rules)
    assert await EXECUTORS["validate"](runtime(ok)[0]) == {"passed": True, "checked": 2}

    bad = ValidateStep(id="v", type="validate", rules=[
        {"expr": "input.amount > 1000", "message": "needs more"},
        {"expr": "steps.missing.output.flag", "message": "flag must be set"},
    ])
    with pytest.raises(StepError, match="needs more; flag must be set") as info:
        await EXECUTORS["validate"](runtime(bad)[0])
    assert info.value.retryable is False and info.value.kind == "validation"
    assert info.value.details == {"failed": ["needs more", "flag must be set"]}


async def test_task_creates_a_manual_task():
    step = TaskStep(id="manual", type="task", title="Handle {{ input.order_id }}", assignee="ops",
                    payload={"amount": "{{ input.amount }}"})
    rt, rec = runtime(step)
    assert await EXECUTORS["task"](rt) == {"task_id": "tk_0001", "title": "Handle A1", "assignee": "ops"}
    assert rec.tasks == [("Handle A1", "ops", {"amount": 50})]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/steps -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebellum.steps'`.

- [ ] **Step 3: Implement**

`src/cerebellum/steps/base.py`:

```python
"""What a step executor receives and the shared helpers they use."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from cerebellum.ai.base import AIProvider
from cerebellum.config import Settings


class CallRecorder(Protocol):
    def __call__(self, kind: str, data: dict[str, Any], cost_usd: float = 0.0) -> None: ...


class TaskCreator(Protocol):
    def __call__(self, title: str, assignee: str, payload: dict[str, Any]) -> str: ...


class ConnectorSource(Protocol):
    async def get(self, name: str) -> Any: ...


@dataclass
class StepRuntime:
    run_id: str
    step: Any  # one of the cerebellum.spec.models step classes
    attempt: int
    span_id: str
    ctx: Mapping[str, Any]
    connectors: ConnectorSource
    ai: AIProvider
    settings: Settings
    record_call: CallRecorder
    create_task: TaskCreator


StepExecutor = Callable[[StepRuntime], Awaitable[Any]]


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
```

`src/cerebellum/steps/query.py`:

```python
"""`query` step: parameterised SQL through a SQL connector."""

from __future__ import annotations

import re
import time
from typing import Any

from cerebellum.connectors.base import ConnectorError, SqlConnector
from cerebellum.errors import StepError
from cerebellum.spec.expressions import render
from cerebellum.steps.base import StepRuntime, elapsed_ms

_READ = re.compile(r"^\s*(select|with|show|explain|values|pragma)\b", re.I)


async def run_query(rt: StepRuntime) -> Any:
    step = rt.step
    params = render(step.params, rt.ctx)
    connector = await rt.connectors.get(step.connector)
    if not isinstance(connector, SqlConnector):
        raise StepError(f"connector {step.connector!r} is not a SQL connector", retryable=False, kind="config")
    reading = bool(_READ.match(step.sql))
    trace = {
        "connector": step.connector,
        "operation": "query" if reading else "execute",
        "sql": step.sql,
        "params": params,
    }
    started = time.perf_counter()
    try:
        if reading:
            result: Any = await connector.query(step.sql, params)
        else:
            result = await connector.execute(step.sql, params)
    except ConnectorError as exc:
        rt.record_call("connector", {**trace, "ok": False, "error": str(exc), "duration_ms": elapsed_ms(started)})
        raise
    count_key = "rows" if reading else "rowcount"
    count = len(result) if reading else result
    rt.record_call("connector", {**trace, "ok": True, count_key: count, "duration_ms": elapsed_ms(started)})
    if not reading:
        return {"rowcount": result}
    return _apply_expect(step.expect, result)


def _apply_expect(expect: str, rows: list[dict[str, Any]]) -> Any:
    count = len(rows)
    if expect == "one":
        if count != 1:
            raise StepError(f"expected exactly one row, got {count}", retryable=False, kind="expect",
                            details={"rows": count})
        return rows[0]
    if expect == "many" and count == 0:
        raise StepError("expected at least one row, got 0", retryable=False, kind="expect")
    if expect == "none" and count != 0:
        raise StepError(f"expected no rows, got {count}", retryable=False, kind="expect",
                        details={"rows": count})
    return rows
```

`src/cerebellum/steps/http.py`:

```python
"""`http` step: REST call with an idempotency key that is stable across retries and resumes."""

from __future__ import annotations

import time
from typing import Any

from cerebellum.connectors.base import ConnectorError, HttpConnector
from cerebellum.connectors.rest import redact_headers
from cerebellum.errors import StepError
from cerebellum.spec.expressions import render
from cerebellum.steps.base import StepRuntime, elapsed_ms


async def run_http(rt: StepRuntime) -> Any:
    step = rt.step
    path = render(step.path, rt.ctx)
    body = render(step.body, rt.ctx)
    headers = render(step.headers, rt.ctx)
    query = render(step.query, rt.ctx)
    connector = await rt.connectors.get(step.connector)
    if not isinstance(connector, HttpConnector):
        raise StepError(f"connector {step.connector!r} is not an HTTP connector", retryable=False, kind="config")
    key = f"{rt.run_id}:{step.id}"
    sent_headers = {**connector.default_headers, **headers, "Idempotency-Key": key}
    trace: dict[str, Any] = {
        "connector": step.connector,
        "method": step.method,
        "path": path,
        "request": {"headers": redact_headers(sent_headers), "query": query, "body": body},
    }
    started = time.perf_counter()
    try:
        response = await connector.request(
            step.method, path, json=body, headers=headers, query=query, idempotency_key=key
        )
    except ConnectorError as exc:
        rt.record_call("connector", {
            **trace, "ok": False, "status": exc.details.get("status"),
            "response": {"body": exc.details.get("body")}, "error": str(exc),
            "duration_ms": elapsed_ms(started),
        })
        raise
    rt.record_call("connector", {
        **trace, "ok": True, "status": response.status,
        "response": {"headers": response.headers, "body": response.body},
        "duration_ms": elapsed_ms(started),
    })
    return {"status": response.status, "body": response.body, "headers": response.headers}
```

`src/cerebellum/steps/ai.py`:

```python
"""`ai` step: structured output validated against the step's JSON Schema, with repair turns."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from cerebellum.ai.base import AIError, AIRequest
from cerebellum.errors import StepError
from cerebellum.spec.expressions import render
from cerebellum.spec.schemas import validation_errors
from cerebellum.steps.base import StepRuntime


async def run_ai(rt: StepRuntime) -> Any:
    step = rt.step
    prompt = render(step.prompt, rt.ctx)
    system = render(step.system, rt.ctx) if step.system else None
    request = AIRequest(
        model=step.model or rt.settings.model,
        prompt=prompt,
        schema=step.output_schema,
        system=system,
        effort=step.effort,
        max_tokens=step.max_tokens,
        mock_rules=tuple(step.mock),
        context=rt.ctx,
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    errors: list[str] = []
    for repair in range(step.max_repairs + 1):
        try:
            result = await rt.ai.generate(request, messages)
        except AIError as exc:
            rt.record_call("llm", {
                "provider": rt.ai.name, "model": request.model, "mock": rt.ai.mock,
                "system": system, "prompt": prompt, "repair": repair, "ok": False,
                "error": str(exc), "kind": exc.kind,
            })
            raise
        output, errors = _parse(result.text, step.output_schema)
        rt.record_call("llm", {
            "provider": rt.ai.name, "model": result.model, "mock": result.mock, "system": system,
            "prompt": prompt, "repair": repair, "ok": not errors, "response": result.text,
            "errors": errors, "usage": asdict(result.usage), "stop_reason": result.stop_reason,
            "duration_ms": round(result.latency_ms, 2),
        }, result.cost_usd)
        if not errors:
            return output
        messages = [
            *messages,
            {"role": "assistant", "content": result.text},
            {"role": "user", "content": _repair_prompt(errors)},
        ]
    raise StepError(
        f"AI output failed schema validation after {step.max_repairs + 1} attempt(s): {errors[0]}",
        retryable=True,
        kind="schema",
        details={"errors": errors},
    )


def _parse(text: str, schema: dict[str, Any]) -> tuple[Any, list[str]]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"<root>: response is not valid JSON ({exc.msg})"]
    return value, validation_errors(schema, value)


def _repair_prompt(errors: list[str]) -> str:
    listed = "\n".join(f"- {error}" for error in errors[:10])
    return (
        "Your previous response failed JSON Schema validation:\n"
        f"{listed}\nReturn only the corrected JSON object."
    )
```

`src/cerebellum/steps/validate.py`:

```python
"""`validate` step: deterministic business rules. Any failing rule fails the step."""

from __future__ import annotations

from typing import Any

from cerebellum.errors import StepError
from cerebellum.spec.expressions import eval_condition
from cerebellum.steps.base import StepRuntime


async def run_validate(rt: StepRuntime) -> Any:
    rules = rt.step.rules
    failed = [rule.message for rule in rules if not eval_condition(rule.expr, rt.ctx)]
    if failed:
        raise StepError("; ".join(failed), retryable=False, kind="validation", details={"failed": failed})
    return {"passed": True, "checked": len(rules)}
```

`src/cerebellum/steps/task.py`:

```python
"""`task` step: open a manual task in the Cerebellum inbox."""

from __future__ import annotations

from typing import Any

from cerebellum.spec.expressions import render
from cerebellum.steps.base import StepRuntime


async def run_task(rt: StepRuntime) -> Any:
    step = rt.step
    title = render(step.title, rt.ctx)
    payload = render(step.payload, rt.ctx)
    task_id = rt.create_task(title, step.assignee, payload)
    return {"task_id": task_id, "title": title, "assignee": step.assignee}
```

`src/cerebellum/steps/__init__.py`:

```python
"""Step executors keyed by step type. `approval` steps are handled by the engine itself."""

from cerebellum.steps.ai import run_ai
from cerebellum.steps.base import StepExecutor, StepRuntime
from cerebellum.steps.http import run_http
from cerebellum.steps.query import run_query
from cerebellum.steps.task import run_task
from cerebellum.steps.validate import run_validate

EXECUTORS: dict[str, StepExecutor] = {
    "query": run_query,
    "http": run_http,
    "ai": run_ai,
    "validate": run_validate,
    "task": run_task,
}

__all__ = ["EXECUTORS", "StepExecutor", "StepRuntime"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/steps -q`
Expected: all PASS.

- [ ] **Step 5: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 10: Engine core — scheduling, retries, fallback, budget

**Files:**
- Create: `src/cerebellum/runtime/context.py`, `src/cerebellum/runtime/retry.py`, `src/cerebellum/runtime/engine.py`
- Test: `tests/runtime/test_retry.py`, `tests/runtime/test_engine.py`

**Interfaces:**
- Consumes: everything from Tasks 1–9.
- Produces:
  - `build_context(run, steps, extra=None) -> dict` (keys `input, params, run{id,cost_usd}, steps{<id>: {output,status,error,attempts}}` plus extras such as `failure`).
  - `backoff_delay(policy, attempt, *, jitter=0.0, rng=None) -> float`.
  - `new_run_id() -> str`, `load_run_workflow(store, run) -> Workflow`.
  - `Engine(store, settings, ai, *, clock=None, http_transports=None, owner=None, jitter=0.1)` with
    `async start(workflow, input=None, params=None, *, run_id=None, eval_run_id=None) -> RunRecord`,
    `async resume(run_id) -> RunRecord`,
    `async decide(run_id, step_id=None, *, approved, by, comment="", resume=True) -> RunRecord`,
    `async expire_due_approvals() -> list[str]`,
    `load_workflow(run) -> Workflow`.
  - Event vocabulary written by the engine: `step.started` (data `attempt`, `type`, optional `fallback_for`), `step.retrying` (`attempt, kind, delay_s, details`), `step.succeeded`, `step.failed` (`attempt, kind, retryable, details`), `step.skipped` (`reason, when`), `step.cancelled` (`reason`), `step.recovered` (`fallback`), `step.reset` (`reason`), `step.waiting`, `run.resumed`, `run.suspended` (`waiting`), `run.completed` (`failed, rejected, recovered`). Span id of an attempt: `<step_id>#<attempt>`; call spans: `<attempt span>:<kind>:<hex>`.

- [ ] **Step 1: Write the failing tests**

`tests/runtime/test_retry.py`:

```python
import random

import pytest

from cerebellum.runtime.retry import backoff_delay
from cerebellum.spec.models import RetryPolicy


def test_exponential_backoff_is_capped():
    policy = RetryPolicy(max=5, base=1.0, max_delay=5.0)
    assert [backoff_delay(policy, n) for n in range(1, 6)] == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_fixed_backoff():
    policy = RetryPolicy(max=3, backoff="fixed", base=0.5)
    assert [backoff_delay(policy, n) for n in (1, 2, 3)] == [0.5, 0.5, 0.5]


def test_jitter_stays_within_bounds():
    policy = RetryPolicy(max=1, base=2.0)
    rng = random.Random(1)
    delays = [backoff_delay(policy, 1, jitter=0.1, rng=rng) for _ in range(50)]
    assert all(1.8 <= d <= 2.2 for d in delays)
    assert len(set(delays)) > 1
    assert backoff_delay(policy, 1, jitter=0.0) == pytest.approx(2.0)
```

`tests/runtime/test_engine.py`:

```python
import asyncio

import httpx
import pytest

from cerebellum.ai.base import AIResult, Usage
from cerebellum.ai.mock import MockProvider
from cerebellum.errors import SpecError
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus, StepStatus
from cerebellum.spec import parse_workflow

API_YAML = """
name: api_flow
input:
  order_id: {type: string, required: true}
connectors:
  api: {type: rest, base_url: "http://api.test"}
steps:
  - id: call
    type: http
    connector: api
    method: POST
    path: /refunds
    body: {order_id: "{{ input.order_id }}"}
    retry: {max: 3, base: 1s}
    on_failure: {fallback: manual}
  - id: after
    type: validate
    needs: [call]
    rules:
      - {expr: "steps.call.status in ['succeeded', 'recovered']", message: call must finish}
fallbacks:
  - id: manual
    type: task
    title: "Manual handling for {{ input.order_id }} after {{ failure.step }}"
    assignee: ops
output:
  status_code: "{{ steps.call.output.status }}"
"""
NO_FALLBACK_YAML = API_YAML.replace("    on_failure: {fallback: manual}\n", "")


def wf(text, tmp_path):
    return parse_workflow(text, base_dir=tmp_path, env={})


def engine_for(store, settings, transports=None, ai=None):
    return Engine(store, settings, ai or MockProvider(latency=(0, 0)),
                  http_transports=transports or {}, jitter=0)


class Responses:
    """Scripted handler: returns the queued responses in order, repeating the last one."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        status, body = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return httpx.Response(status, json=body)


def event_types(store, run_id, step_id=None):
    return [e.type for e in store.get_events(run_id) if step_id is None or e.step_id == step_id]


async def test_retry_then_success(store, settings, clock, tmp_path):
    handler = Responses((503, {"e": 1}), (503, {"e": 2}), (201, {"id": "rf_1"}))
    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(API_YAML, tmp_path), {"order_id": "A1"})

    assert run.status is RunStatus.SUCCEEDED
    assert run.output == {"status_code": 201}
    call = store.get_step(run.run_id, "call")
    assert call.attempts == 3 and call.output["body"] == {"id": "rf_1"}
    assert clock.sleeps == [1.0, 2.0]
    assert event_types(store, run.run_id, "call").count("step.retrying") == 2
    assert {r.headers["Idempotency-Key"] for r in handler.requests} == {f"{run.run_id}:call"}
    assert store.get_step(run.run_id, "manual").status is StepStatus.PENDING
    assert run.lease_owner is None
    assert event_types(store, run.run_id)[0] == "run.started"
    assert event_types(store, run.run_id)[-1] == "run.completed"


async def test_non_retryable_failure_cancels_downstream(store, settings, clock, tmp_path):
    handler = Responses((404, {"detail": "nope"}))
    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(NO_FALLBACK_YAML, tmp_path), {"order_id": "A1"})

    assert run.status is RunStatus.FAILED
    assert "HTTP 404" in run.error
    assert store.get_step(run.run_id, "call").attempts == 1
    assert store.get_step(run.run_id, "after").status is StepStatus.CANCELLED
    assert clock.sleeps == []
    failed = [e for e in store.get_events(run.run_id) if e.type == "step.failed"][0]
    assert failed.data["kind"] == "http_status" and failed.data["retryable"] is False


async def test_fallback_recovers_and_marks_needs_attention(store, settings, clock, tmp_path):
    handler = Responses((503, {"detail": "down"}))
    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(API_YAML, tmp_path), {"order_id": "A1"})

    assert run.status is RunStatus.NEEDS_ATTENTION
    call = store.get_step(run.run_id, "call")
    assert call.status is StepStatus.RECOVERED
    assert call.output["task_id"].startswith("tk_")
    assert store.get_step(run.run_id, "manual").status is StepStatus.SUCCEEDED
    assert store.get_step(run.run_id, "after").status is StepStatus.SUCCEEDED
    tasks = store.list_tasks(run_id=run.run_id)
    assert [t.title for t in tasks] == ["Manual handling for A1 after call"]
    assert len(handler.requests) == 4 and clock.sleeps == [1.0, 2.0, 4.0]
    assert run.output == {"status_code": None}
    started = [e for e in store.get_events(run.run_id) if e.type == "step.started" and e.step_id == "manual"]
    assert started[0].data["fallback_for"] == "call"


async def test_failing_fallback_leaves_run_failed(store, settings, tmp_path):
    text = """
name: double_fail
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: call, type: http, connector: api, path: /a, on_failure: {fallback: backup}}
fallbacks:
  - {id: backup, type: http, connector: api, path: /b}
"""
    engine = engine_for(store, settings, {"api": httpx.MockTransport(Responses((500, {})))})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.FAILED
    assert store.get_step(run.run_id, "call").status is StepStatus.FAILED
    assert store.get_step(run.run_id, "backup").status is StepStatus.FAILED


async def test_when_false_skips_and_downstream_still_runs(store, settings, tmp_path):
    text = """
name: branching
input: {vip: {type: boolean, required: true}}
steps:
  - {id: vip_only, type: task, when: "input.vip", title: VIP follow-up}
  - id: always
    type: validate
    needs: [vip_only]
    rules: [{expr: "steps.vip_only.output is none", message: skipped output is null}]
"""
    run = await engine_for(store, settings).start(wf(text, tmp_path), {"vip": False})
    assert run.status is RunStatus.SUCCEEDED
    assert store.get_step(run.run_id, "vip_only").status is StepStatus.SKIPPED
    assert store.get_step(run.run_id, "always").status is StepStatus.SUCCEEDED
    skipped = [e for e in store.get_events(run.run_id) if e.type == "step.skipped"][0]
    assert skipped.data["reason"] == "condition is false"


async def test_step_timeout_is_retryable(store, settings, tmp_path):
    text = """
name: slow
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: call, type: http, connector: api, path: /slow, timeout: 50ms, retry: {max: 1, base: 1s}}
"""

    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    engine = engine_for(store, settings, {"api": httpx.MockTransport(slow)})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.FAILED
    assert store.get_step(run.run_id, "call").attempts == 2
    failed = [e for e in store.get_events(run.run_id) if e.type == "step.failed"][0]
    assert failed.data["kind"] == "timeout"


async def test_parallel_steps_respect_max_parallel(store, settings, tmp_path):
    text = """
name: fanout
limits: {max_parallel: 2}
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: a, type: http, connector: api, path: /a}
  - {id: b, type: http, connector: api, path: /b}
  - {id: c, type: http, connector: api, path: /c}
  - {id: d, type: http, connector: api, path: /d}
  - id: join
    type: validate
    needs: [a, b, c, d]
    rules: [{expr: "true", message: ok}]
"""
    state = {"active": 0, "peak": 0}

    async def handler(request):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        return httpx.Response(200, json={"path": request.url.path})

    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.SUCCEEDED
    assert state["peak"] == 2


class CostlyAI:
    name = "costly"
    mock = False

    def __init__(self, cost):
        self.cost = cost
        self.calls = 0

    async def generate(self, request, messages):
        self.calls += 1
        return AIResult(text='{"ok": true}', model=request.model, usage=Usage(), cost_usd=self.cost,
                        mock=False, stop_reason="end_turn", latency_ms=1.0)


async def test_budget_exceeded_fails_without_retry_and_cancels_the_rest(store, settings, tmp_path):
    text = """
name: spendy
limits: {budget_usd: 0.5}
steps:
  - id: think
    type: ai
    prompt: Think
    output_schema: {type: object, required: [ok], properties: {ok: {type: boolean}}}
  - id: think_more
    type: ai
    needs: [think]
    prompt: More
    retry: {max: 2}
    output_schema: {type: object, required: [ok], properties: {ok: {type: boolean}}}
  - {id: final, type: task, needs: [think_more], title: done}
"""
    ai = CostlyAI(0.3)
    run = await engine_for(store, settings, ai=ai).start(wf(text, tmp_path))
    assert run.status is RunStatus.FAILED
    assert "budget" in run.error
    assert ai.calls == 2
    assert run.cost_usd == pytest.approx(0.6)
    assert store.get_step(run.run_id, "think_more").attempts == 1
    assert store.get_step(run.run_id, "final").status is StepStatus.CANCELLED


async def test_template_error_fails_without_retry(store, settings, tmp_path):
    """Review focus: a template that references a missing value fails clearly, once."""
    text = """
name: broken_template
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: call, type: http, connector: api, path: /x, body: {v: "{{ input.nope }}"}, retry: {max: 3}}
"""
    handler = Responses((200, {}))
    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.FAILED
    call = store.get_step(run.run_id, "call")
    assert call.attempts == 1 and "input.nope" in call.error
    assert handler.requests == []


async def test_bad_input_is_rejected_before_run_creation(store, settings, tmp_path):
    with pytest.raises(SpecError, match="input.order_id: expected string"):
        await engine_for(store, settings).start(wf(API_YAML, tmp_path), {"order_id": 123})
    assert store.list_runs() == []


async def test_unknown_param_is_rejected(store, settings, tmp_path):
    with pytest.raises(SpecError, match="params.nope"):
        await engine_for(store, settings).start(wf(API_YAML, tmp_path), {"order_id": "A1"}, {"nope": 1})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/runtime/test_retry.py tests/runtime/test_engine.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebellum.runtime.retry'` / `cerebellum.runtime.engine`.

- [ ] **Step 3: Implement `context.py` and `retry.py`**

`src/cerebellum/runtime/context.py`:

```python
"""The expression context steps see: input, params, run and every step's projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cerebellum.runtime.store import RunRecord, StepRecord


def build_context(
    run: RunRecord,
    steps: Mapping[str, StepRecord],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "input": run.input,
        "params": run.params,
        "run": {"id": run.run_id, "cost_usd": run.cost_usd},
        "steps": {
            step_id: {
                "output": record.output,
                "status": record.status.value,
                "error": record.error,
                "attempts": record.attempts,
            }
            for step_id, record in steps.items()
        },
    }
    if extra:
        context.update(extra)
    return context
```

`src/cerebellum/runtime/retry.py`:

```python
"""Backoff schedule for step retries."""

from __future__ import annotations

import random

from cerebellum.spec.models import RetryPolicy


def backoff_delay(
    policy: RetryPolicy,
    attempt: int,
    *,
    jitter: float = 0.0,
    rng: random.Random | None = None,
) -> float:
    """Delay before retry number `attempt` (1-based). Jitter is a ± fraction of the delay."""
    if policy.backoff == "fixed":
        delay = policy.base
    else:
        delay = policy.base * (2 ** (attempt - 1))
    delay = min(delay, policy.max_delay)
    if jitter:
        delay *= 1 + (rng or random).uniform(-jitter, jitter)
    return max(delay, 0.0)
```

- [ ] **Step 4: Implement `engine.py`**

`src/cerebellum/runtime/engine.py`:

```python
"""Workflow engine: drives runs, applies retry/fallback, suspends for approvals, resumes."""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from cerebellum.ai.base import AIProvider
from cerebellum.config import Settings
from cerebellum.connectors import ConnectorEnv, ConnectorPool
from cerebellum.errors import (
    BudgetExceeded,
    CerebellumError,
    LeaseUnavailable,
    StepError,
    TemplateError,
)
from cerebellum.runtime.clock import Clock
from cerebellum.runtime.context import build_context
from cerebellum.runtime.retry import backoff_delay
from cerebellum.runtime.states import (
    RUN_RESUMABLE,
    STEP_ACTIVE,
    STEP_BLOCKING,
    STEP_DONE_OK,
    RunStatus,
    StepStatus,
)
from cerebellum.runtime.store import ApprovalRecord, RunRecord, StepRecord, Store
from cerebellum.spec.expressions import eval_condition, render
from cerebellum.spec.inputs import resolve_params, validate_input
from cerebellum.spec.loader import parse_workflow
from cerebellum.spec.models import ApprovalStep, Workflow
from cerebellum.steps import EXECUTORS
from cerebellum.steps.base import StepRuntime

S = StepStatus
R = RunStatus


def new_run_id() -> str:
    return "r_" + secrets.token_hex(4)


def _default_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"


def _span(step_id: str, attempt: int) -> str:
    return f"{step_id}#{attempt}"


def load_run_workflow(store: Store, run: RunRecord) -> Workflow:
    """Re-parse the workflow snapshot pinned to the run (connector env vars resolve now)."""
    source, base_dir = store.get_workflow_source(run.workflow_digest)
    return parse_workflow(source, base_dir=base_dir)


class Engine:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        ai: AIProvider,
        *,
        clock: Clock | None = None,
        http_transports: Mapping[str, httpx.AsyncBaseTransport] | None = None,
        owner: str | None = None,
        jitter: float = 0.1,
    ):
        self.store = store
        self.settings = settings
        self.ai = ai
        self.clock: Clock = clock or store.clock
        self.http_transports = dict(http_transports or {})
        self.owner = owner or _default_owner()
        self.jitter = jitter

    # ── public API ───────────────────────────────────────────────────────────

    async def start(
        self,
        workflow: Workflow,
        input: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        *,
        run_id: str | None = None,
        eval_run_id: str | None = None,
    ) -> RunRecord:
        data = validate_input(workflow, dict(input or {}))
        resolved = resolve_params(workflow, params)
        run_id = run_id or new_run_id()
        self.store.save_workflow(workflow)
        self.store.create_run(
            run_id, workflow, data, resolved, mock=self.ai.mock, eval_run_id=eval_run_id
        )
        return await self._drive(run_id, workflow)

    async def resume(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.status not in RUN_RESUMABLE:
            raise CerebellumError(f"run {run_id} is {run.status.value} and cannot be resumed")
        return await self._drive(run_id, self.load_workflow(run))

    def load_workflow(self, run: RunRecord) -> Workflow:
        return load_run_workflow(self.store, run)

    async def decide(
        self,
        run_id: str,
        step_id: str | None = None,
        *,
        approved: bool,
        by: str,
        comment: str = "",
        resume: bool = True,
    ) -> RunRecord:
        pending = self.store.list_approvals(run_id=run_id, status="pending")
        matches = [a for a in pending if step_id is None or a.step_id == step_id]
        if not matches:
            target = f" for step {step_id}" if step_id else ""
            raise CerebellumError(f"run {run_id} has no pending approval{target}")
        if len(matches) > 1:
            names = ", ".join(a.step_id for a in matches)
            raise CerebellumError(
                f"run {run_id} has several pending approvals ({names}); specify the step"
            )
        self.store.decide_approval(matches[0].id, approved=approved, by=by, comment=comment)
        if not resume:
            return self.store.get_run(run_id)
        try:
            return await self.resume(run_id)
        except LeaseUnavailable:
            # The process that owns the run picks the decision up before it suspends.
            return self.store.get_run(run_id)

    async def expire_due_approvals(self) -> list[str]:
        """Apply on_timeout to overdue approvals and resume their runs."""
        now = self.clock.now()
        resumed: list[str] = []
        for approval in self.store.list_approvals(status="pending"):
            if approval.expires_at is None or approval.expires_at > now:
                continue
            self.store.decide_approval(
                approval.id,
                approved=approval.on_timeout == "approve",
                by="system",
                comment="approval timed out",
                expired=True,
            )
            if approval.run_id in resumed:
                continue
            try:
                await self.resume(approval.run_id)
                resumed.append(approval.run_id)
            except LeaseUnavailable:
                pass
        return resumed

    # ── driving a run ────────────────────────────────────────────────────────

    async def _drive(self, run_id: str, workflow: Workflow) -> RunRecord:
        if not self.store.acquire_lease(run_id, self.owner, self.settings.lease_seconds):
            raise LeaseUnavailable(f"run {run_id} is being executed by another process")
        heartbeat = asyncio.create_task(self._heartbeat(run_id))
        pool = ConnectorPool(
            workflow.connectors,
            ConnectorEnv(
                home=self.settings.home,
                base_dir=Path(workflow.base_dir),
                http_transports=self.http_transports,
            ),
        )
        try:
            run = self.store.get_run(run_id)
            if run.status is not R.RUNNING:
                self.store.set_run_status(
                    run_id, R.RUNNING, event="resumed", data={"from": run.status.value}
                )
            self._reset_for_resume(run_id, run.status)
            await _Execution(self, run_id, workflow, pool).run()
            return self.store.get_run(run_id)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            await pool.close()
            self.store.release_lease(run_id, self.owner)

    async def _heartbeat(self, run_id: str) -> None:
        interval = max(self.settings.lease_seconds / 3, 0.05)
        while True:
            await asyncio.sleep(interval)
            self.store.renew_lease(run_id, self.owner, self.settings.lease_seconds)

    def _reset_for_resume(self, run_id: str, previous: RunStatus) -> None:
        for record in self.store.get_steps(run_id).values():
            if record.status in STEP_ACTIVE:
                reason = "interrupted"
            elif previous is R.FAILED and record.status in STEP_BLOCKING:
                reason = "resume after failure"
            else:
                continue
            self.store.step_transition(
                run_id, record.step_id, S.PENDING, event="reset", output=None, error=None,
                data={"reason": reason},
            )


@dataclass
class _Outcome:
    ok: bool
    output: Any = None
    error: StepError | None = None


class _Execution:
    """One drive of one run: schedules ready steps until nothing more can run."""

    def __init__(self, engine: Engine, run_id: str, workflow: Workflow, pool: ConnectorPool):
        self.engine = engine
        self.store = engine.store
        self.run_id = run_id
        self.wf = workflow
        self.pool = pool
        self.semaphore = asyncio.Semaphore(workflow.limits.max_parallel)
        self.halt_reason: str | None = None

    async def run(self) -> None:
        running: dict[str, asyncio.Task[None]] = {}
        while True:
            self._apply_approval_states()
            self._cancel_blocked()
            steps = self.store.get_steps(self.run_id)
            if self.halt_reason is None:
                for step_id in self._ready(steps):
                    if step_id not in running:
                        running[step_id] = asyncio.create_task(
                            self._run_step(step_id), name=f"step:{step_id}"
                        )
            if not running:
                if self._apply_approval_states():
                    continue
                break
            done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                running.pop(task.get_name().removeprefix("step:"))
                task.result()  # unexpected engine errors must surface, not vanish
        self._finish()

    # ── scheduling helpers ───────────────────────────────────────────────────

    def _ready(self, steps: Mapping[str, StepRecord]) -> list[str]:
        return [
            step.id
            for step in self.wf.steps
            if steps[step.id].status is S.PENDING
            and all(steps[dep].status in STEP_DONE_OK for dep in step.needs)
        ]

    def _cancel_blocked(self) -> None:
        statuses = {sid: rec.status for sid, rec in self.store.get_steps(self.run_id).items()}
        changed = True
        while changed:
            changed = False
            for step in self.wf.steps:
                if statuses[step.id] is not S.PENDING:
                    continue
                reason = self.halt_reason
                if reason is None:
                    blocked = [dep for dep in step.needs if statuses[dep] in STEP_BLOCKING]
                    if blocked:
                        reason = f"upstream step '{blocked[0]}' {statuses[blocked[0]].value}"
                if reason:
                    self.store.step_transition(
                        self.run_id, step.id, S.CANCELLED, event="cancelled", data={"reason": reason}
                    )
                    statuses[step.id] = S.CANCELLED
                    changed = True

    def _apply_approval_states(self) -> bool:
        """Turn decided (or expired) approvals into step results. Returns True if any changed."""
        changed = False
        now = self.engine.clock.now()
        for step_id, record in self.store.get_steps(self.run_id).items():
            if record.status is not S.WAITING:
                continue
            approval = self.store.get_approval_for_step(self.run_id, step_id)
            if approval is None:
                continue
            if approval.status == "pending" and approval.expires_at is not None and approval.expires_at <= now:
                approval = self.store.decide_approval(
                    approval.id,
                    approved=approval.on_timeout == "approve",
                    by="system",
                    comment="approval timed out",
                    expired=True,
                )
            if approval.status == "pending":
                continue
            span = _span(step_id, record.attempts)
            output = _approval_output(approval)
            if approval.status == "approved":
                self.store.step_transition(
                    self.run_id, step_id, S.SUCCEEDED, event="succeeded", span_id=span,
                    output=output, ended_at=now,
                )
            else:
                reason = f"rejected by {approval.decided_by}"
                if approval.comment:
                    reason += f": {approval.comment}"
                self.store.step_transition(
                    self.run_id, step_id, S.FAILED, event="failed", span_id=span, output=output,
                    error=reason, ended_at=now, data={"kind": "rejected", "retryable": False},
                )
            changed = True
        return changed

    def _context(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return build_context(
            self.store.get_run(self.run_id), self.store.get_steps(self.run_id), extra
        )

    # ── running one step ─────────────────────────────────────────────────────

    async def _run_step(self, step_id: str) -> None:
        async with self.semaphore:
            if self.halt_reason is not None:
                return  # the scheduler cancels it on the next pass
            step = self.wf.step(step_id)
            ctx = self._context()
            try:
                skip = bool(step.when) and not eval_condition(step.when, ctx)
            except TemplateError as exc:
                self._fail_immediately(step_id, StepError(str(exc), retryable=False, kind="template"))
                return
            if skip:
                self.store.step_transition(
                    self.run_id, step_id, S.SKIPPED, event="skipped",
                    data={"reason": "condition is false", "when": step.when},
                )
                return
            if isinstance(step, ApprovalStep):
                self._request_approval(step, ctx)
                return
            outcome = await self._execute(step)
            if outcome.ok or outcome.error is None:
                return
            if step.on_failure and outcome.error.kind != "budget":
                await self._run_fallback(step_id, step.on_failure.fallback, outcome.error)

    async def _execute(
        self,
        step: Any,
        extra: Mapping[str, Any] | None = None,
        *,
        fallback_for: str | None = None,
    ) -> _Outcome:
        record = self.store.get_step(self.run_id, step.id)
        executor = EXECUTORS[step.type]
        max_attempts = step.retry.max + 1
        for n in range(1, max_attempts + 1):
            attempt = record.attempts + n
            span = _span(step.id, attempt)
            started: dict[str, Any] = {"attempt": attempt, "type": step.type}
            if fallback_for:
                started["fallback_for"] = fallback_for
            self.store.step_transition(
                self.run_id, step.id, S.RUNNING, event="started", span_id=span, attempts=attempt,
                started_at=self.engine.clock.now() if n == 1 else None, data=started,
            )
            runtime = StepRuntime(
                run_id=self.run_id,
                step=step,
                attempt=attempt,
                span_id=span,
                ctx=self._context(extra),
                connectors=self.pool,
                ai=self.engine.ai,
                settings=self.engine.settings,
                record_call=self._recorder(step.id, span),
                create_task=self._task_creator(step.id, span),
            )
            try:
                output = await asyncio.wait_for(executor(runtime), timeout=step.timeout)
            except TimeoutError:
                error = StepError(f"timed out after {step.timeout:g}s", retryable=True, kind="timeout")
            except StepError as exc:
                error = exc
            except TemplateError as exc:
                error = StepError(str(exc), retryable=False, kind="template")
            except Exception as exc:  # unexpected library/executor error: fail the step cleanly
                error = StepError(f"{type(exc).__name__}: {exc}", retryable=False, kind="internal")
            else:
                self.store.step_transition(
                    self.run_id, step.id, S.SUCCEEDED, event="succeeded", span_id=span,
                    output=output, error=None, ended_at=self.engine.clock.now(),
                    data={"attempt": attempt},
                )
                return _Outcome(True, output)

            if isinstance(error, BudgetExceeded):
                self.halt_reason = str(error)
            if error.retryable and n < max_attempts and self.halt_reason is None:
                delay = backoff_delay(step.retry, n, jitter=self.engine.jitter)
                self.store.step_transition(
                    self.run_id, step.id, S.RETRYING, event="retrying", span_id=span,
                    error=str(error),
                    data={"attempt": attempt, "kind": error.kind, "delay_s": round(delay, 3),
                          "details": error.details},
                )
                await self.engine.clock.sleep(delay)
                continue
            self.store.step_transition(
                self.run_id, step.id, S.FAILED, event="failed", span_id=span, error=str(error),
                ended_at=self.engine.clock.now(),
                data={"attempt": attempt, "kind": error.kind, "retryable": error.retryable,
                      "details": error.details},
            )
            return _Outcome(False, error=error)
        raise AssertionError("unreachable: the loop always returns")

    def _fail_immediately(self, step_id: str, error: StepError) -> None:
        record = self.store.get_step(self.run_id, step_id)
        attempt = record.attempts + 1
        span = _span(step_id, attempt)
        now = self.engine.clock.now()
        self.store.step_transition(
            self.run_id, step_id, S.RUNNING, event="started", span_id=span, attempts=attempt,
            started_at=now, data={"attempt": attempt},
        )
        self.store.step_transition(
            self.run_id, step_id, S.FAILED, event="failed", span_id=span, error=str(error),
            ended_at=now, data={"attempt": attempt, "kind": error.kind, "retryable": False},
        )

    async def _run_fallback(self, failed_step: str, fallback_id: str, error: StepError) -> None:
        fallback = self.wf.step(fallback_id)
        extra = {"failure": {"step": failed_step, "error": str(error), "kind": error.kind}}
        outcome = await self._execute(fallback, extra, fallback_for=failed_step)
        if outcome.ok:
            self.store.step_transition(
                self.run_id, failed_step, S.RECOVERED, event="recovered", output=outcome.output,
                data={"fallback": fallback_id},
            )

    def _request_approval(self, step: ApprovalStep, ctx: Mapping[str, Any]) -> None:
        record = self.store.get_step(self.run_id, step.id)
        attempt = record.attempts + 1
        span = _span(step.id, attempt)
        now = self.engine.clock.now()
        self.store.step_transition(
            self.run_id, step.id, S.RUNNING, event="started", span_id=span, attempts=attempt,
            started_at=now, data={"attempt": attempt, "type": "approval"},
        )
        try:
            title = render(step.title, ctx)
        except TemplateError as exc:
            self.store.step_transition(
                self.run_id, step.id, S.FAILED, event="failed", span_id=span, error=str(exc),
                ended_at=now, data={"kind": "template", "retryable": False},
            )
            return
        context = {
            "input": ctx["input"],
            "steps": {ref: ctx["steps"][ref]["output"] for ref in step.show},
        }
        expires_at = now + step.timeout if step.timeout is not None else None
        self.store.request_approval(
            self.run_id, step.id, title=title, context=context, expires_at=expires_at,
            on_timeout=step.on_timeout, span_id=span,
        )

    # ── recording helpers ────────────────────────────────────────────────────

    def _recorder(self, step_id: str, span: str) -> Any:
        def record(kind: str, data: dict[str, Any], cost_usd: float = 0.0) -> None:
            self.store.record_call(
                self.run_id, step_id, kind, span_id=f"{span}:{kind}:{secrets.token_hex(3)}",
                parent_span_id=span, data=data, cost_usd=cost_usd,
            )
            budget = self.wf.limits.budget_usd
            if cost_usd and budget is not None:
                spent = self.store.get_run(self.run_id).cost_usd
                if spent > budget:
                    raise BudgetExceeded(budget, spent)

        return record

    def _task_creator(self, step_id: str, span: str) -> Any:
        def create(title: str, assignee: str, payload: dict[str, Any]) -> str:
            task = self.store.create_task(
                self.run_id, step_id, title=title, assignee=assignee, payload=payload, span_id=span
            )
            return task.id

        return create

    # ── completion ───────────────────────────────────────────────────────────

    def _finish(self) -> None:
        steps = self.store.get_steps(self.run_id)
        main = [steps[step.id] for step in self.wf.steps]
        waiting = [s.step_id for s in main if s.status is S.WAITING]
        if waiting:
            self.store.set_run_status(
                self.run_id, R.WAITING_APPROVAL, event="suspended", data={"waiting": waiting}
            )
            return
        rejected = [
            s.step_id for s in main
            if s.status is S.FAILED and isinstance(self.wf.step(s.step_id), ApprovalStep)
        ]
        failed = [s.step_id for s in main if s.status is S.FAILED and s.step_id not in rejected]
        recovered = [s.step_id for s in main if s.status is S.RECOVERED]
        if rejected:
            status = R.REJECTED
        elif failed:
            status = R.FAILED
        elif recovered:
            status = R.NEEDS_ATTENTION
        else:
            status = R.SUCCEEDED
        error = "; ".join(f"{sid}: {steps[sid].error}" for sid in failed) or None
        output = render(self.wf.output, self._context(), strict=False) if self.wf.output else None
        self.store.set_run_status(
            self.run_id, status, event="completed", output=output, error=error,
            data={"failed": failed, "rejected": rejected, "recovered": recovered},
        )


def _approval_output(approval: ApprovalRecord) -> dict[str, Any]:
    return {
        "approved": approval.status == "approved",
        "by": approval.decided_by,
        "comment": approval.comment or "",
        "decided_at": approval.decided_at,
        "auto": approval.decided_by == "system",
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/runtime -q`
Expected: all PASS.

- [ ] **Step 6: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 11: Human-in-the-loop, resume and crash recovery

**Files:**
- Test: `tests/runtime/test_engine_hitl.py`
- Modify (only if a test exposes a defect): `src/cerebellum/runtime/engine.py`

**Interfaces:**
- Consumes: `Engine` (Task 10), `PaymentsState`, `create_payments_app` (Task 7), store approvals/leases (Task 5).
- Produces: verified behaviour — suspension with released lease, approve/reject/expiry, decisions applied mid-drive, lease exclusivity, crash resume without duplicate side effects, resume after failure.

- [ ] **Step 1: Write the tests**

`tests/runtime/test_engine_hitl.py`:

```python
import asyncio

import httpx
import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.errors import CerebellumError, LeaseUnavailable
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus, StepStatus
from cerebellum.sandbox.payments import PaymentsState, create_payments_app
from cerebellum.spec import parse_workflow

APPROVAL_YAML = """
name: approval_flow
input:
  amount: {type: number, required: true}
params:
  threshold: 500
steps:
  - id: assess
    type: validate
    rules: [{expr: "input.amount > 0", message: positive}]
  - id: sign_off
    type: approval
    needs: [assess]
    when: "input.amount > params.threshold"
    title: "Approve {{ input.amount }}"
    show: [assess]
    timeout: 1h
    on_timeout: reject
  - {id: pay, type: task, needs: [sign_off], title: "Pay {{ input.amount }}"}
  - {id: audit, type: task, title: Audit log entry}
output:
  approved_by: "{{ steps.sign_off.output.by }}"
"""


def wf(text, tmp_path):
    return parse_workflow(text, base_dir=tmp_path, env={})


def engine_for(store, settings, transports=None):
    return Engine(store, settings, MockProvider(latency=(0, 0)), http_transports=transports or {}, jitter=0)


async def test_suspends_for_approval_and_releases_lease(store, settings, clock, tmp_path):
    run = await engine_for(store, settings).start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    assert run.status is RunStatus.WAITING_APPROVAL
    assert run.lease_owner is None
    steps = store.get_steps(run.run_id)
    assert steps["sign_off"].status is StepStatus.WAITING
    assert steps["pay"].status is StepStatus.PENDING
    assert steps["audit"].status is StepStatus.SUCCEEDED  # independent branch kept going
    [approval] = store.list_approvals(run_id=run.run_id)
    assert approval.title == "Approve 900"
    assert approval.context == {"input": {"amount": 900}, "steps": {"assess": {"passed": True, "checked": 1}}}
    assert approval.expires_at == pytest.approx(clock.now() + 3600)
    suspended = [e for e in store.get_events(run.run_id) if e.type == "run.suspended"]
    assert suspended[0].data["waiting"] == ["sign_off"]


async def test_small_amount_skips_approval(store, settings, tmp_path):
    run = await engine_for(store, settings).start(wf(APPROVAL_YAML, tmp_path), {"amount": 100})
    assert run.status is RunStatus.SUCCEEDED
    assert store.get_step(run.run_id, "sign_off").status is StepStatus.SKIPPED
    assert run.output == {"approved_by": None}


async def test_approve_resumes_to_success(store, settings, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    run = await engine.decide(run.run_id, approved=True, by="alice", comment="fine")
    assert run.status is RunStatus.SUCCEEDED
    sign_off = store.get_step(run.run_id, "sign_off")
    assert sign_off.output["approved"] is True and sign_off.output["by"] == "alice"
    assert sign_off.output["auto"] is False
    assert run.output == {"approved_by": "alice"}
    assert "run.resumed" in [e.type for e in store.get_events(run.run_id)]


async def test_reject_marks_run_rejected_and_cancels_downstream(store, settings, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    run = await engine.decide(run.run_id, "sign_off", approved=False, by="bob", comment="too much")
    assert run.status is RunStatus.REJECTED
    assert store.get_step(run.run_id, "sign_off").error == "rejected by bob: too much"
    assert store.get_step(run.run_id, "pay").status is StepStatus.CANCELLED


async def test_approval_timeout_applies_on_timeout(store, settings, clock, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    assert await engine.expire_due_approvals() == []
    clock.advance(3601)
    assert await engine.expire_due_approvals() == [run.run_id]
    run = store.get_run(run.run_id)
    assert run.status is RunStatus.REJECTED
    [approval] = store.list_approvals(run_id=run.run_id)
    assert approval.decided_by == "system" and approval.status == "rejected"
    assert "approval.expired" in [e.type for e in store.get_events(run.run_id)]
    assert store.get_step(run.run_id, "sign_off").output["auto"] is True


async def test_resume_applies_expiry_by_itself(store, settings, clock, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    clock.advance(3601)
    assert (await engine.resume(run.run_id)).status is RunStatus.REJECTED


async def test_decision_during_execution_is_not_lost(store, settings, tmp_path):
    """Review focus: a human approves while another branch of the same run is still running."""
    text = """
name: concurrent_decision
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: gate, type: approval, title: Gate}
  - {id: slow, type: http, connector: api, path: /slow}
  - {id: after_gate, type: task, needs: [gate], title: after gate}
"""

    async def handler(request):
        for _ in range(200):
            pending = store.list_approvals(status="pending")
            if pending:
                break
            await asyncio.sleep(0.01)
        store.decide_approval(pending[0].id, approved=True, by="carol")
        return httpx.Response(200, json={})

    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.SUCCEEDED
    assert store.get_step(run.run_id, "after_gate").status is StepStatus.SUCCEEDED
    assert "run.suspended" not in [e.type for e in store.get_events(run.run_id)]


async def test_resume_is_refused_while_another_process_holds_the_lease(store, settings, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    assert store.acquire_lease(run.run_id, "someone-else", 60)
    with pytest.raises(LeaseUnavailable):
        await engine.resume(run.run_id)


async def test_decide_while_another_process_owns_the_run_records_the_decision(store, settings, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 900})
    store.acquire_lease(run.run_id, "someone-else", 60)
    record = await engine.decide(run.run_id, approved=True, by="dana")
    assert record.status is RunStatus.WAITING_APPROVAL
    assert store.list_approvals(run_id=run.run_id)[0].status == "approved"


CRASH_YAML = """
name: crashy
input: {order_id: {type: string, required: true}}
connectors:
  payments: {type: rest, base_url: "http://payments.test"}
steps:
  - id: prepare
    type: validate
    rules: [{expr: "input.order_id", message: need an order}]
  - id: refund
    type: http
    needs: [prepare]
    connector: payments
    method: POST
    path: /refunds
    body: {order_id: "{{ input.order_id }}", amount: 10}
"""


async def test_crash_mid_refund_resumes_without_double_refund(store, settings, tmp_path):
    state = PaymentsState()
    transport = httpx.ASGITransport(app=create_payments_app(state))
    workflow = wf(CRASH_YAML, tmp_path)
    run_id = "r_crash001"
    # A process that died right after the payments API accepted the refund:
    store.save_workflow(workflow)
    store.create_run(run_id, workflow, {"order_id": "A9"}, {}, mock=True)
    store.step_transition(run_id, "prepare", StepStatus.RUNNING, event="started", attempts=1)
    store.step_transition(run_id, "prepare", StepStatus.SUCCEEDED, event="succeeded", output={"passed": True})
    store.step_transition(run_id, "refund", StepStatus.RUNNING, event="started", attempts=1)
    original = state.create(f"{run_id}:refund", "A9", 10.0, "USD", None)
    assert store.is_stale(store.get_run(run_id))

    run = await engine_for(store, settings, {"payments": transport}).resume(run_id)

    assert run.status is RunStatus.SUCCEEDED
    refund = store.get_step(run_id, "refund")
    assert refund.attempts == 2
    assert refund.output["status"] == 200  # replayed by the idempotency key, not created again
    assert refund.output["body"]["id"] == original["id"]
    assert len(state.refunds) == 1
    resets = [e for e in store.get_events(run_id) if e.type == "step.reset"]
    assert resets[0].step_id == "refund" and resets[0].data["reason"] == "interrupted"


async def test_failed_run_can_be_resumed_after_the_dependency_recovers(store, settings, tmp_path):
    text = """
name: flaky_dependency
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: call, type: http, connector: api, path: /x}
  - {id: after, type: task, needs: [call], title: after}
"""
    healthy = {"value": False}

    def handler(request):
        return httpx.Response(201 if healthy["value"] else 503, json={})

    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.FAILED
    assert store.get_step(run.run_id, "after").status is StepStatus.CANCELLED
    healthy["value"] = True
    run = await engine.resume(run.run_id)
    assert run.status is RunStatus.SUCCEEDED
    assert store.get_step(run.run_id, "call").attempts == 2
    assert run.error is None or run.error == ""


async def test_finished_runs_cannot_be_resumed(store, settings, tmp_path):
    engine = engine_for(store, settings)
    run = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 100})
    with pytest.raises(CerebellumError, match="succeeded and cannot be resumed"):
        await engine.resume(run.run_id)


async def test_decide_requires_exactly_one_pending_approval(store, settings, tmp_path):
    engine = engine_for(store, settings)
    done = await engine.start(wf(APPROVAL_YAML, tmp_path), {"amount": 100})
    with pytest.raises(CerebellumError, match="no pending approval"):
        await engine.decide(done.run_id, approved=True, by="x")
    text = """
name: two_gates
steps:
  - {id: gate_a, type: approval, title: A}
  - {id: gate_b, type: approval, title: B}
"""
    run = await engine.start(wf(text, tmp_path))
    with pytest.raises(CerebellumError, match="several pending approvals"):
        await engine.decide(run.run_id, approved=True, by="x")
    await engine.decide(run.run_id, "gate_a", approved=True, by="x")
    run = await engine.decide(run.run_id, "gate_b", approved=True, by="x")
    assert run.status is RunStatus.SUCCEEDED
```

- [ ] **Step 2: Run the tests**

Run: `.venv/bin/pytest tests/runtime/test_engine_hitl.py -q`
Expected: all PASS against the Task 10 engine. In `test_failed_run_can_be_resumed_after_the_dependency_recovers`, `run.error` after a successful resume is set by `_finish` to `None` (no failed steps) — the assertion accepts `None` or empty. If any test fails, use superpowers:systematic-debugging: identify the root cause in `engine.py`, fix minimally, and re-run `tests/runtime -q`.

- [ ] **Step 3: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 12: Refund template, seed data, docker-compose, end-to-end scenarios

**Files:**
- Create: `src/cerebellum/templates/__init__.py`, `src/cerebellum/templates/refund/workflow.yaml`, `src/cerebellum/templates/refund/seed.sql`, `src/cerebellum/templates/refund/inputs/{small,large,flaky,outage,fraud}.json`, `docker-compose.yml`
- Test: `tests/integration/test_refund_template.py`

**Interfaces:**
- Consumes: `Engine`, `load_workflow`, `PaymentsState`, `FailMode`, `create_payments_app`, `MockProvider`.
- Produces: `template_path(name) -> Path` (raises `FileNotFoundError` for unknown names); the `refund_request` workflow with steps `fetch_order, policy_check, assess_request, manager_approval, issue_refund, mark_refunded`, fallback `open_manual_case`, connectors `orders_db` (postgres, `${ORDERS_DSN:-sandbox}`, seed `seed.sql`) and `payments` (rest, `${PAYMENTS_URL:-http://127.0.0.1:8787}`), param `approval_threshold: 500`, output keys `decision ∈ {refunded, manual, rejected, denied, failed}`, `refund_id`, `rationale`. Demo inputs: small → A1001/120, large → A1002/899, flaky → A1004/45, outage → A1005/75, fraud → A1003/60 with a chargeback threat.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_refund_template.py`:

```python
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
    return Engine(store, settings, MockProvider(latency=(0, 0)),
                  http_transports={"payments": transport}, jitter=0)


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
        "fetch_order", "policy_check", "assess_request", "manager_approval", "issue_refund",
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
        "refund_status": "refunded", "refund_id": run.output["refund_id"],
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
    assert task.payload["failed_step"] == "issue_refund" and task.payload["order_id"] == "A1005"
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/integration -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cerebellum.templates'`.

- [ ] **Step 3: Create the template package**

`src/cerebellum/templates/__init__.py`:

```python
"""Example workflows shipped with the package."""

from __future__ import annotations

from pathlib import Path


def template_path(name: str) -> Path:
    path = Path(__file__).parent / name
    if not path.is_dir():
        raise FileNotFoundError(f"no packaged template named {name!r}")
    return path
```

`src/cerebellum/templates/refund/workflow.yaml`:

```yaml
name: refund_request
version: 1
description: >
  A customer asks for a refund. Look the order up in PostgreSQL, check eligibility with
  deterministic policy rules and an AI assessment, ask a human when the refund is large or
  risky, call the payments API, and open a manual case if the API keeps failing.

params:
  approval_threshold: 500

input:
  order_id: {type: string, required: true, description: Order identifier}
  amount: {type: number, required: true, description: Requested refund amount}
  reason: {type: string, description: The customer's reason for the refund}

connectors:
  orders_db:
    type: postgres
    dsn: "${ORDERS_DSN:-sandbox}"
    seed: seed.sql
  payments:
    type: rest
    base_url: "${PAYMENTS_URL:-http://127.0.0.1:8787}"
    headers:
      Authorization: "Bearer ${PAYMENTS_TOKEN:-sandbox-token}"
    timeout: 5s

limits:
  budget_usd: 0.50

steps:
  - id: fetch_order
    type: query
    description: Load the order from PostgreSQL
    connector: orders_db
    sql: >
      SELECT id, customer_email, amount, currency, status, delivered_at, refund_status
      FROM orders WHERE id = :order_id
    params:
      order_id: "{{ input.order_id }}"
    expect: one
    retry: {max: 2, base: 200ms}

  - id: policy_check
    type: validate
    description: Deterministic refund policy
    needs: [fetch_order]
    rules:
      - expr: "input.amount > 0 and input.amount <= steps.fetch_order.output.amount"
        message: Refund amount must be positive and not exceed the order amount
      - expr: "steps.fetch_order.output.status == 'delivered'"
        message: Only delivered orders can be refunded
      - expr: "steps.fetch_order.output.refund_status == 'none'"
        message: Order has already been refunded

  - id: assess_request
    type: ai
    description: Structured AI eligibility and risk assessment
    needs: [policy_check]
    system: You are a meticulous refund analyst. Answer only with the requested JSON.
    prompt: |
      Assess this refund request against the policy:
      - Delivered orders may be refunded within 30 days of delivery.
      - Reasons that contradict delivery records, threaten chargebacks, or look like abuse are
        high risk and not eligible.

      Order: {{ steps.fetch_order.output }}
      Requested amount: {{ input.amount }}
      Customer reason: {{ input.reason or "(none given)" }}
    output_schema:
      type: object
      required: [eligible, risk, rationale]
      properties:
        eligible: {type: boolean}
        risk: {type: string, enum: [low, medium, high]}
        rationale: {type: string}
    effort: low
    retry: {max: 2, base: 1s}
    mock:
      - when: "'fraud' in (input.reason or '') | lower or 'chargeback' in (input.reason or '') | lower"
        output: {eligible: false, risk: high, rationale: "The reason contradicts the delivery record and threatens a chargeback."}
      - when: "input.amount > params.approval_threshold"
        output: {eligible: true, risk: medium, rationale: "Within policy, but the amount is large."}
      - output: {eligible: true, risk: low, rationale: "Delivered recently and the reason is plausible."}

  - id: manager_approval
    type: approval
    description: Human approval for large or risky refunds
    needs: [assess_request]
    when: >
      steps.assess_request.output.eligible and
      (input.amount > params.approval_threshold or steps.assess_request.output.risk == 'high')
    title: "Refund {{ input.amount }} for order {{ input.order_id }}"
    show: [fetch_order, assess_request]
    timeout: 24h
    on_timeout: reject

  - id: issue_refund
    type: http
    description: Call the payments API (idempotent, retried)
    needs: [manager_approval]
    when: "steps.assess_request.output.eligible"
    connector: payments
    method: POST
    path: /refunds
    body:
      order_id: "{{ input.order_id }}"
      amount: "{{ input.amount }}"
      currency: "{{ steps.fetch_order.output.currency }}"
      reason: "{{ input.reason or '' }}"
    retry: {max: 3, backoff: exponential, base: 500ms}
    on_failure: {fallback: open_manual_case}

  - id: mark_refunded
    type: query
    description: Record the refund on the order
    needs: [issue_refund]
    when: "steps.issue_refund.status == 'succeeded'"
    connector: orders_db
    sql: UPDATE orders SET refund_status = 'refunded', refund_id = :refund_id WHERE id = :order_id
    params:
      refund_id: "{{ steps.issue_refund.output.body.id }}"
      order_id: "{{ input.order_id }}"

fallbacks:
  - id: open_manual_case
    type: task
    title: "Refund for {{ input.order_id }} needs manual processing"
    assignee: finance-ops
    payload:
      order_id: "{{ input.order_id }}"
      amount: "{{ input.amount }}"
      failed_step: "{{ failure.step }}"
      error: "{{ failure.error }}"

output:
  decision: >-
    {{ 'refunded' if steps.issue_refund.status == 'succeeded'
       else 'manual' if steps.issue_refund.status == 'recovered'
       else 'rejected' if steps.manager_approval.status == 'failed'
       else 'denied' if steps.assess_request.output.eligible == false
       else 'failed' }}
  refund_id: "{{ steps.issue_refund.output.body.id }}"
  rationale: "{{ steps.assess_request.output.rationale }}"
```

`src/cerebellum/templates/refund/seed.sql`:

```sql
-- Sample orders for the refund workflow. Portable across PostgreSQL and SQLite.
CREATE TABLE IF NOT EXISTS orders (
    id             TEXT PRIMARY KEY,
    customer_email TEXT NOT NULL,
    amount         NUMERIC(10, 2) NOT NULL,
    currency       TEXT NOT NULL DEFAULT 'USD',
    status         TEXT NOT NULL,
    delivered_at   TEXT,
    refund_status  TEXT NOT NULL DEFAULT 'none',
    refund_id      TEXT
);

INSERT INTO orders (id, customer_email, amount, currency, status, delivered_at, refund_status) VALUES
    ('A1001', 'mia.chen@example.com',    120.00, 'USD', 'delivered', '2026-09-24', 'none'),
    ('A1002', 'leo.martin@example.com',  899.00, 'USD', 'delivered', '2026-09-21', 'none'),
    ('A1003', 'ava.patel@example.com',    60.00, 'USD', 'delivered', '2026-09-27', 'none'),
    ('A1004', 'noah.kim@example.com',     45.00, 'USD', 'delivered', '2026-09-25', 'none'),
    ('A1005', 'emma.silva@example.com',   75.00, 'USD', 'delivered', '2026-09-22', 'none'),
    ('A1006', 'liam.wong@example.com',  1500.00, 'USD', 'shipped',   NULL,         'none'),
    ('A1007', 'zoe.garcia@example.com',  210.00, 'USD', 'delivered', '2026-09-18', 'refunded'),
    ('A1008', 'ethan.ito@example.com',   640.00, 'USD', 'delivered', '2026-09-26', 'none'),
    ('A1009', 'chloe.novak@example.com',  89.90, 'USD', 'delivered', '2026-09-28', 'none'),
    ('A1010', 'omar.haddad@example.com', 499.00, 'USD', 'delivered', '2026-09-23', 'none'),
    ('A1011', 'ivy.larsen@example.com',  501.00, 'USD', 'delivered', '2026-09-23', 'none'),
    ('A1012', 'sam.okafor@example.com',   35.50, 'USD', 'delivered', '2026-09-29', 'none')
ON CONFLICT (id) DO NOTHING;
```

Inputs (`src/cerebellum/templates/refund/inputs/`):

`small.json`:
```json
{"order_id": "A1001", "amount": 120, "reason": "The jacket arrived with a torn sleeve."}
```

`large.json`:
```json
{"order_id": "A1002", "amount": 899, "reason": "The laptop screen flickers constantly."}
```

`flaky.json`:
```json
{"order_id": "A1004", "amount": 45, "reason": "Wrong colour was delivered."}
```

`outage.json`:
```json
{"order_id": "A1005", "amount": 75, "reason": "The package arrived empty."}
```

`fraud.json`:
```json
{"order_id": "A1003", "amount": 60, "reason": "Tracking says delivered but I never got it. Refund now or I will file a chargeback."}
```

`docker-compose.yml` (repository root; local-only credentials for a disposable database):

```yaml
# Optional: a real PostgreSQL seeded with the refund example data.
#   docker compose up -d
#   export ORDERS_DSN=postgresql://cerebellum:cerebellum@localhost:5432/cerebellum
#   pip install -e ".[postgres]"
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: cerebellum
      POSTGRES_PASSWORD: cerebellum
      POSTGRES_DB: cerebellum
    ports:
      - "5432:5432"
    volumes:
      - ./src/cerebellum/templates/refund/seed.sql:/docker-entrypoint-initdb.d/seed.sql:ro
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration -q`
Expected: all PASS. If the folded `decision` expression fails to parse, the loader reports it at `output.decision`; fix the YAML expression (not the loader).

- [ ] **Step 5: Run the whole suite, lint and checkpoint**

Run: `.venv/bin/pytest -q && make fmt && make lint && git status --short`
Expected: all PASS.

---

### Task 13: CLI — rendering, trace spans and commands

**Files:**
- Create: `src/cerebellum/runtime/trace.py`, `src/cerebellum/cli/render.py`, `src/cerebellum/cli/app.py`
- Test: `tests/runtime/test_trace.py`, `tests/cli/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `Span(span_id, parent_id, step_id, kind, label, start, end, status, detail)`, `build_spans(events) -> list[Span]`, `call_label(kind, data) -> str`; render helpers `ACCENT, MUTED, STEP_GLYPHS, RUN_GLYPHS, fmt_duration, fmt_cost, fmt_age, clip, header, run_status, step_table, run_view, output_view, runs_table, approvals_table, tasks_table, connectors_table, issues_view, dag_view, trace_table`; Typer `app` with commands `init, validate, show, run, runs, status, trace, approvals, approve, reject, resume, tasks [resolve], connectors check, sandbox` and `main()`; module globals `console`, `err_console` (tests replace them), exit-code constants `EXIT_OK=0, EXIT_FAILED=1, EXIT_INVALID=2, EXIT_WAITING=3`.

- [ ] **Step 1: Write the failing tests**

`tests/runtime/test_trace.py`:

```python
import pytest

from cerebellum.runtime.store import EventRecord
from cerebellum.runtime.trace import build_spans


def ev(seq, type, ts, step_id=None, span_id=None, parent=None, **data):
    return EventRecord(seq, "r_1", step_id, span_id, parent, type, ts, data)


def test_build_spans_pairs_attempts_and_nests_calls():
    events = [
        ev(1, "run.started", 0.0),
        ev(2, "step.started", 1.0, "pay", "pay#1", attempt=1, type="http"),
        ev(3, "connector.call", 1.5, "pay", "pay#1:connector:a", "pay#1", method="POST",
           path="/refunds", status=503, ok=False, duration_ms=400),
        ev(4, "step.retrying", 1.6, "pay", "pay#1", attempt=1),
        ev(5, "step.started", 2.0, "pay", "pay#2", attempt=2, type="http"),
        ev(6, "connector.call", 2.2, "pay", "pay#2:connector:b", "pay#2", method="POST",
           path="/refunds", status=201, ok=True, duration_ms=100),
        ev(7, "step.succeeded", 2.3, "pay", "pay#2"),
        ev(8, "step.started", 3.0, "gate", "gate#1", attempt=1, type="approval"),
        ev(9, "approval.requested", 3.0, "gate", "gate#1", approval_id="ap_1"),
        ev(10, "step.waiting", 3.0, "gate", "gate#1"),
        ev(11, "llm.call", 3.5, "judge", "judge#1:llm:c", "judge#1", model="claude-opus-5-5",
           mock=True, ok=True, duration_ms=250),
        ev(12, "connector.call", 4.0, "load", "load#1:connector:d", "load#1", operation="query",
           ok=True, duration_ms=0),
    ]
    spans = build_spans(events)
    assert [s.label for s in spans] == [
        "pay #1", "POST /refunds → 503", "pay #2", "POST /refunds → 201", "gate #1",
        "llm claude-opus-5-5 (mock)", "sql query",
    ]
    first, call1, second, call2, gate, llm, sql = spans
    assert (first.status, first.end) == ("retrying", 1.6)
    assert call1.parent_id == "pay#1" and call1.status == "failed"
    assert call1.start == pytest.approx(1.1)
    assert (second.status, second.end) == ("succeeded", 2.3)
    assert gate.status == "waiting" and gate.end is None
    assert llm.kind == "llm" and llm.start == pytest.approx(3.25)
    assert sql.kind == "connector" and sql.start == sql.end == 4.0
```

`tests/cli/test_cli.py`:

```python
import re

import pytest
from rich.console import Console
from typer.testing import CliRunner

from cerebellum.cli import app as cli
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
    for name in ("fetch_order", "assess_request", "manager_approval", "issue_refund", "open_manual_case"):
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
    result = invoke(runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'outage.json'}", "--sandbox",
                    "--sandbox-fail", "always")
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


def test_param_override_changes_the_approval_threshold(runner):
    result = invoke(runner, "run", WORKFLOW, "-i", f"@{INPUTS / 'large.json'}",
                    "-p", "approval_threshold=1000", "--sandbox")
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
    second = invoke(runner, "init", str(target))
    assert "exists, kept" in second.text
    assert invoke(runner, "validate", str(target / "workflows" / "refund" / "workflow.yaml")).exit_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/runtime/test_trace.py tests/cli -q`
Expected: FAIL with `ModuleNotFoundError` for `cerebellum.runtime.trace` and `cerebellum.cli.app`.

- [ ] **Step 3: Implement `trace.py`**

`src/cerebellum/runtime/trace.py`:

```python
"""Turn the event log into spans: one per step attempt, children for LLM / connector calls."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from cerebellum.runtime.store import EventRecord

_STEP_END = {"step.succeeded", "step.failed", "step.retrying"}


@dataclass(frozen=True)
class Span:
    span_id: str
    parent_id: str | None
    step_id: str | None
    kind: str  # "step" | "llm" | "connector"
    label: str
    start: float
    end: float | None
    status: str  # running | waiting | retrying | succeeded | failed
    detail: dict[str, Any]


def call_label(kind: str, data: dict[str, Any]) -> str:
    if kind == "llm":
        label = f"llm {data.get('model', '')}".strip()
        if data.get("mock"):
            label += " (mock)"
        if data.get("repair"):
            label += f" · repair {data['repair']}"
        return label
    if "method" in data:
        return f"{data['method']} {data.get('path', '')} → {data.get('status') or 'error'}"
    return f"sql {data.get('operation', 'query')}"


def build_spans(events: Iterable[EventRecord]) -> list[Span]:
    spans: dict[str, Span] = {}
    for event in events:
        if event.type == "step.started" and event.span_id:
            spans[event.span_id] = Span(
                span_id=event.span_id,
                parent_id=None,
                step_id=event.step_id,
                kind="step",
                label=f"{event.step_id} #{event.data.get('attempt', 1)}",
                start=event.ts,
                end=None,
                status="running",
                detail={"type": event.data.get("type"), "fallback_for": event.data.get("fallback_for")},
            )
        elif event.span_id in spans and spans[event.span_id].kind == "step":
            span = spans[event.span_id]
            if event.type in _STEP_END:
                spans[event.span_id] = replace(span, end=event.ts, status=event.type.split(".", 1)[1])
            elif event.type == "step.waiting":
                spans[event.span_id] = replace(span, status="waiting")
        elif event.type in ("llm.call", "connector.call") and event.span_id:
            kind = event.type.split(".", 1)[0]
            duration = float(event.data.get("duration_ms") or 0.0) / 1000
            spans[event.span_id] = Span(
                span_id=event.span_id,
                parent_id=event.parent_span_id,
                step_id=event.step_id,
                kind=kind,
                label=call_label(kind, event.data),
                start=event.ts - duration,
                end=event.ts,
                status="succeeded" if event.data.get("ok", True) else "failed",
                detail=event.data,
            )
    return list(spans.values())
```

- [ ] **Step 4: Implement `render.py`**

`src/cerebellum/cli/render.py`:

```python
"""Terminal rendering — tech-minimal: monochrome, one cyan accent, geometric status glyphs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from rich.console import Group
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from cerebellum.connectors import HealthStatus
from cerebellum.errors import SpecIssue
from cerebellum.runtime.store import ApprovalRecord, RunRecord, StepRecord, TaskRecord
from cerebellum.runtime.trace import Span
from cerebellum.spec.models import Workflow

ACCENT = "cyan"
MUTED = "grey50"

STEP_GLYPHS: dict[str, tuple[str, str]] = {
    "pending": ("○", MUTED),
    "running": ("◐", ACCENT),
    "retrying": ("↻", "yellow"),
    "waiting": ("⏸", "yellow"),
    "succeeded": ("●", "green"),
    "failed": ("✕", "red"),
    "skipped": ("⊘", MUTED),
    "cancelled": ("⊗", MUTED),
    "recovered": ("⤳", "magenta"),
}
RUN_GLYPHS: dict[str, tuple[str, str]] = {
    "running": ("◐", ACCENT),
    "waiting_approval": ("⏸", "yellow"),
    "succeeded": ("●", "green"),
    "failed": ("✕", "red"),
    "rejected": ("✕", "red"),
    "needs_attention": ("⤳", "magenta"),
}
SPAN_STYLES = {
    "succeeded": "green", "failed": "red", "retrying": "yellow", "waiting": "yellow",
    "running": ACCENT,
}


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def fmt_cost(usd: float) -> str:
    return f"${usd:.4f}" if usd else ""


def fmt_age(ts: float, now: float) -> str:
    delta = max(now - ts, 0.0)
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def clip(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def header(title: str, subtitle: str = "") -> Text:
    text = Text()
    text.append(" CEREBELLUM ", style=f"bold black on {ACCENT}")
    text.append(f"  {title}", style="bold")
    if subtitle:
        text.append(f"  {subtitle}", style=MUTED)
    return text


def run_status(status: str, *, stale: bool = False) -> Text:
    glyph, style = RUN_GLYPHS.get(status, ("·", ""))
    text = Text(f"{glyph} {status.replace('_', ' ')}", style=style)
    if stale:
        text.append("  stale", style="bold red")
    return text


def _status_label(status: str) -> str:
    return "awaiting approval" if status == "waiting" else status


def _note(record: StepRecord) -> str:
    parts: list[str] = []
    if record.attempts > 1:
        parts.append(f"{record.attempts} attempts")
    if record.status.value == "recovered":
        parts.append("via fallback")
    if record.status.value == "failed" and record.error:
        parts.append(clip(record.error, 64))
    return " · ".join(parts)


def step_table(workflow: Workflow, steps: Mapping[str, StepRecord]) -> Table:
    table = Table.grid(padding=(0, 2))
    for _ in range(7):
        table.add_column()
    for step in (*workflow.steps, *workflow.fallbacks):
        record = steps.get(step.id)
        if record is None:
            continue
        status = record.status.value
        if workflow.is_fallback(step.id) and status == "pending":
            table.add_row(
                Text("·", MUTED), Text(step.id, MUTED), Text(step.type, MUTED),
                Text("fallback · not triggered", MUTED), Text(""), Text(""), Text(""),
            )
            continue
        glyph, style = STEP_GLYPHS[status]
        dim = status in ("skipped", "cancelled", "pending")
        duration = "" if status in ("pending", "waiting", "running") else fmt_duration(record.duration)
        table.add_row(
            Text(glyph, style=style),
            Text(step.id, style=MUTED if dim else "bold"),
            Text(step.type, style=MUTED),
            Text(_status_label(status), style="" if status == "succeeded" else style),
            Text(duration, style=MUTED),
            Text(fmt_cost(record.cost_usd), style=MUTED),
            Text(_note(record), style=MUTED),
        )
    return table


def run_view(
    run: RunRecord,
    workflow: Workflow,
    steps: Mapping[str, StepRecord],
    *,
    mode: str,
    stale: bool = False,
) -> Group:
    end = run.ended_at or run.updated_at
    footer = run_status(run.status.value, stale=stale)
    footer.append(f"   {fmt_duration(end - run.created_at)}", style=MUTED)
    if run.cost_usd:
        footer.append(f"   {fmt_cost(run.cost_usd)}", style=MUTED)
    return Group(
        header(f"{workflow.name} v{workflow.version}", f"run {run.run_id} · {mode}"),
        Rule(style=MUTED),
        step_table(workflow, steps),
        Rule(style=MUTED),
        footer,
    )


def output_view(output: Any) -> Group:
    body = json.dumps(output, indent=2, ensure_ascii=False, default=str)
    return Group(
        Text("output", style=f"bold {MUTED}"),
        Syntax(body, "json", theme="ansi_dark", background_color="default", word_wrap=True),
    )


def _table(*columns: str) -> Table:
    table = Table(box=None, header_style=f"bold {MUTED}", pad_edge=False, padding=(0, 2, 0, 0))
    for column in columns:
        table.add_column(column)
    return table


def runs_table(runs: Sequence[RunRecord], *, now: float, stale: set[str]) -> Table:
    table = _table("RUN", "WORKFLOW", "STATUS", "COST", "AGE", "AI")
    for run in runs:
        table.add_row(
            Text(run.run_id, style=ACCENT),
            run.workflow_name,
            run_status(run.status.value, stale=run.run_id in stale),
            Text(fmt_cost(run.cost_usd), style=MUTED),
            Text(fmt_age(run.created_at, now), style=MUTED),
            Text("mock" if run.mock else "claude", style=MUTED),
        )
    if not runs:
        table.add_row(Text("no runs yet", style=MUTED), "", "", "", "", "")
    return table


def approvals_table(approvals: Sequence[ApprovalRecord], *, now: float) -> Table:
    table = _table("APPROVAL", "RUN", "STEP", "TITLE", "STATUS", "REQUESTED", "EXPIRES")
    for approval in approvals:
        style = {"pending": "yellow", "approved": "green", "rejected": "red"}.get(approval.status, "")
        decided = f" · {approval.decided_by}" if approval.decided_by else ""
        if approval.expires_at is None or approval.status != "pending":
            expires = ""
        elif approval.expires_at < now:
            expires = "overdue"
        else:
            expires = f"in {fmt_duration(approval.expires_at - now)}"
        table.add_row(
            Text(approval.id, style=MUTED),
            Text(approval.run_id, style=ACCENT),
            approval.step_id,
            clip(approval.title, 48),
            Text(approval.status + decided, style=style),
            Text(fmt_age(approval.requested_at, now), style=MUTED),
            Text(expires, style=MUTED),
        )
    if not approvals:
        table.add_row(Text("no pending approvals", style=MUTED), "", "", "", "", "", "")
    return table


def tasks_table(tasks: Sequence[TaskRecord], *, now: float) -> Table:
    table = _table("TASK", "RUN", "TITLE", "ASSIGNEE", "STATUS", "AGE")
    for task in tasks:
        table.add_row(
            Text(task.id, style=MUTED),
            Text(task.run_id, style=ACCENT),
            clip(task.title, 56),
            task.assignee,
            Text(task.status, style="magenta" if task.status == "open" else "green"),
            Text(fmt_age(task.created_at, now), style=MUTED),
        )
    if not tasks:
        table.add_row(Text("no open tasks", style=MUTED), "", "", "", "", "")
    return table


def connectors_table(results: Iterable[tuple[str, str, HealthStatus]]) -> Table:
    table = _table("", "CONNECTOR", "TYPE", "DETAIL", "LATENCY")
    for name, kind, status in results:
        table.add_row(
            Text("●", style="green") if status.ok else Text("✕", style="red"),
            Text(name, style="bold"),
            Text(kind, style=MUTED),
            clip(status.detail, 80),
            Text(fmt_duration(status.latency_ms / 1000) if status.ok else "", style=MUTED),
        )
    return table


def issues_view(title: str, issues: Sequence[SpecIssue]) -> Group:
    lines = [Text("✕ ", style="red") + Text(title, style="bold")]
    for issue in issues:
        line = Text("  ")
        line.append(issue.path, style=ACCENT)
        line.append(f"  {issue.message}")
        lines.append(line)
    return Group(*lines)


def _step_label(step: Any, index: int) -> Text:
    label = Text()
    label.append(f"{index:>2}  ", style=MUTED)
    label.append(step.id, style="bold")
    label.append(f"  {step.type}", style=MUTED)
    if step.needs:
        label.append(f"  ← {', '.join(step.needs)}", style=MUTED)
    if step.when:
        label.append(f"  when {' '.join(step.when.split())}", style="yellow")
    if step.retry.max:
        label.append(f"  ↻ {step.retry.max}", style=MUTED)
    if step.on_failure:
        label.append(f"  ⤳ {step.on_failure.fallback}", style="magenta")
    return label


def dag_view(workflow: Workflow) -> Tree:
    title = Text(f"{workflow.name} v{workflow.version}", style="bold")
    if workflow.description:
        title.append(f"  {clip(workflow.description, 90)}", style=MUTED)
    tree = Tree(title, guide_style=MUTED)
    for index, step in enumerate(workflow.steps, start=1):
        tree.add(_step_label(step, index))
    if workflow.fallbacks:
        users = {s.on_failure.fallback: s.id for s in workflow.steps if s.on_failure}
        branch = tree.add(Text("fallbacks", style="magenta"))
        for step in workflow.fallbacks:
            label = Text()
            label.append(step.id, style="bold")
            label.append(f"  {step.type}", style=MUTED)
            if step.id in users:
                label.append(f"  ⤳ for {users[step.id]}", style="magenta")
            branch.add(label)
    return tree


def trace_table(spans: Sequence[Span], *, now: float, width: int = 36) -> Table:
    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2, 0, 0))
    table.add_column()
    table.add_column()
    table.add_column(justify="right")
    if not spans:
        table.add_row(Text("no spans recorded", style=MUTED), "", "")
        return table
    t0 = min(span.start for span in spans)
    t1 = max(span.end if span.end is not None else now for span in spans)
    total = max(t1 - t0, 1e-9)
    for span in spans:
        end = span.end if span.end is not None else now
        offset = min(int((span.start - t0) / total * width), width - 1)
        length = min(max(1, round((end - span.start) / total * width)), width - offset)
        bar = Text(" " * offset + "━" * length + " " * (width - offset - length),
                   style=SPAN_STYLES.get(span.status, ACCENT))
        label = Text(("  └ " if span.parent_id else "") + span.label,
                     style=MUTED if span.parent_id else "bold")
        table.add_row(label, bar, Text(fmt_duration(end - span.start), style=MUTED))
    return table
```

- [ ] **Step 5: Implement `app.py`**

`src/cerebellum/cli/app.py`:

```python
"""Cerebellum command-line interface."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
import uvicorn
import yaml
from dotenv import load_dotenv
from rich.console import Console, RenderableType
from rich.live import Live
from rich.text import Text

from cerebellum import __version__
from cerebellum.ai import select_provider
from cerebellum.cli import render
from cerebellum.config import Settings
from cerebellum.connectors import ConnectorEnv, HealthStatus, create_connector
from cerebellum.errors import CerebellumError, LeaseUnavailable, SpecError, StepError
from cerebellum.runtime.engine import Engine, load_run_workflow
from cerebellum.runtime.states import RunStatus
from cerebellum.runtime.store import EventRecord, RunRecord, Store
from cerebellum.runtime.trace import build_spans
from cerebellum.sandbox.payments import FailMode, PaymentsState, create_payments_app
from cerebellum.sandbox.server import SandboxHandle, start_sandbox
from cerebellum.spec import load_workflow
from cerebellum.spec.models import Workflow
from cerebellum.templates import template_path

app = typer.Typer(
    name="cerebellum",
    help="Reliable, observable, recoverable business workflows.",
    no_args_is_help=True,
    add_completion=False,
)
tasks_app = typer.Typer(help="Manual task inbox (fallback hand-offs).")
connectors_app = typer.Typer(help="Connector utilities.", no_args_is_help=True)
app.add_typer(tasks_app, name="tasks")
app.add_typer(connectors_app, name="connectors")

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

EXIT_OK, EXIT_FAILED, EXIT_INVALID, EXIT_WAITING = 0, 1, 2, 3
DEFAULT_USER = os.environ.get("USER") or os.environ.get("USERNAME") or "cli"
REFUND_TEMPLATE_FILES = (
    "workflow.yaml",
    "seed.sql",
    "inputs/small.json",
    "inputs/large.json",
    "inputs/flaky.json",
    "inputs/outage.json",
    "inputs/fraud.json",
)
ENV_EXAMPLE = """\
# Cerebellum configuration. Copy to .env and adjust.
# Real Claude calls (otherwise the offline mock AI is used):
# ANTHROPIC_API_KEY=
# CEREBELLUM_MODEL=claude-opus-5-5
# CEREBELLUM_HOME=.cerebellum
# Real PostgreSQL (default: SQLite sandbox seeded from seed.sql):
# ORDERS_DSN=postgresql://cerebellum:cerebellum@localhost:5432/cerebellum
# Payments API (default: the local sandbox started with --sandbox):
# PAYMENTS_URL=http://127.0.0.1:8787
# PAYMENTS_TOKEN=sandbox-token
"""


def main() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    app()


# ── helpers ──────────────────────────────────────────────────────────────────


def _settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_home()
    return settings


def _fail(message: str, code: int = EXIT_FAILED) -> NoReturn:
    err_console.print(Text("✕ ", style="red") + Text(message))
    raise typer.Exit(code)


def _invalid(title: str, exc: SpecError) -> NoReturn:
    err_console.print(render.issues_view(title, exc.issues))
    raise typer.Exit(EXIT_INVALID)


def _load(path: Path) -> Workflow:
    try:
        return load_workflow(path)
    except SpecError as exc:
        _invalid(f"{path} is invalid", exc)


def _get_run(store: Store, run_id: str) -> RunRecord:
    try:
        return store.get_run(run_id)
    except CerebellumError as exc:
        _fail(str(exc))


def _run_workflow(store: Store, run: RunRecord) -> Workflow:
    try:
        return load_run_workflow(store, run)
    except SpecError as exc:
        _invalid("the run's workflow snapshot is invalid in this environment", exc)


def _mode(run: RunRecord) -> str:
    return "mock AI" if run.mock else "Claude API"


def _exit_code(status: RunStatus) -> int:
    if status is RunStatus.WAITING_APPROVAL:
        return EXIT_WAITING
    if status in (RunStatus.FAILED, RunStatus.REJECTED):
        return EXIT_FAILED
    return EXIT_OK


def _start_sandbox(settings: Settings, fail: str) -> SandboxHandle:
    try:
        return start_sandbox(settings.sandbox_host, settings.sandbox_port, fail)
    except ValueError as exc:
        _fail(str(exc), EXIT_INVALID)
    except CerebellumError as exc:
        _fail(str(exc))


def _parse_input(raw: str) -> dict[str, Any]:
    text = raw
    if raw.startswith("@"):
        path = Path(raw[1:])
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            _fail(f"cannot read input file {path}: {exc.strerror}", EXIT_INVALID)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail(f"input is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})", EXIT_INVALID)
    if not isinstance(data, dict):
        _fail("input must be a JSON object", EXIT_INVALID)
    return data


def _parse_params(items: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            _fail(f"invalid --param {item!r}; use key=value", EXIT_INVALID)
        params[key.strip()] = yaml.safe_load(value) if value else ""
    return params


def _drive_live(
    store: Store,
    workflow: Workflow,
    mode: str,
    action: Callable[[], Awaitable[RunRecord]],
    *,
    run_id: str | None = None,
) -> RunRecord:
    """Run `action` while redrawing the step table in place (terminal only)."""
    if not console.is_terminal:
        return asyncio.run(action())
    current: dict[str, str | None] = {"run_id": run_id}

    def on_event(event: EventRecord) -> None:
        if current["run_id"] is None:
            current["run_id"] = event.run_id

    def view() -> RenderableType:
        rid = current["run_id"]
        if rid is None:
            return Text("  starting…", style=render.MUTED)
        return render.run_view(store.get_run(rid), workflow, store.get_steps(rid), mode=mode)

    unsubscribe = store.add_listener(on_event)
    try:
        with Live(get_renderable=view, console=console, refresh_per_second=12, transient=True):
            return asyncio.run(action())
    finally:
        unsubscribe()


def _show_run(store: Store, run: RunRecord, workflow: Workflow, mode: str) -> None:
    steps = store.get_steps(run.run_id)
    console.print(render.run_view(run, workflow, steps, mode=mode, stale=store.is_stale(run)))
    _print_outcome(store, run)


def _print_outcome(store: Store, run: RunRecord) -> None:
    rid = run.run_id
    if run.status is RunStatus.WAITING_APPROVAL:
        for approval in store.list_approvals(run_id=rid, status="pending"):
            console.print(Text("⏸ ", style="yellow") + Text(f"awaiting approval · {approval.title}"))
            console.print(Text(f"  cerebellum approve {rid} {approval.step_id} --by <you>", style=render.ACCENT))
            console.print(
                Text(f"  cerebellum reject {rid} {approval.step_id} --by <you> -m <why>", style=render.MUTED)
            )
    elif run.status is RunStatus.FAILED:
        console.print(Text("✕ ", style="red") + Text(run.error or "run failed"))
        console.print(Text(f"  fix the cause, then: cerebellum resume {rid}", style=render.MUTED))
    elif run.status is RunStatus.REJECTED:
        console.print(Text("✕ rejected by a human approver", style="red"))
    elif run.status is RunStatus.NEEDS_ATTENTION:
        open_tasks = store.list_tasks(run_id=rid, status="open")
        console.print(
            Text("⤳ ", style="magenta")
            + Text(f"recovered via fallback · {len(open_tasks)} manual task(s) open")
        )
        console.print(Text("  cerebellum tasks", style=render.ACCENT))
    elif run.status is RunStatus.RUNNING and store.is_stale(run):
        console.print(Text("◐ ", style="red") + Text("the process driving this run stopped"))
        console.print(Text(f"  cerebellum resume {rid}", style=render.ACCENT))
    if run.output is not None:
        console.print(render.output_view(run.output))


# ── commands ─────────────────────────────────────────────────────────────────


def _version(value: bool) -> None:
    if value:
        console.print(f"cerebellum {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version and exit."),
    ] = False,
) -> None:
    """Reliable, observable, recoverable business workflows."""


@app.command()
def init(
    directory: Annotated[Path, typer.Argument(help="Project directory.")] = Path("."),
) -> None:
    """Scaffold the refund example workflow and a .env.example."""
    source = template_path("refund")
    target = directory / "workflows" / "refund"
    for relative in REFUND_TEMPLATE_FILES:
        destination = target / relative
        if destination.exists():
            console.print(Text(f"  = {destination} (exists, kept)", style=render.MUTED))
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
        console.print(Text(f"  + {destination}", style="green"))
    env_file = directory / ".env.example"
    if env_file.exists():
        console.print(Text(f"  = {env_file} (exists, kept)", style=render.MUTED))
    else:
        env_file.write_text(ENV_EXAMPLE, encoding="utf-8")
        console.print(Text(f"  + {env_file}", style="green"))
    workflow = target / "workflow.yaml"
    console.print()
    console.print(Text("next", style="bold"))
    console.print(Text(f"  cerebellum validate {workflow}", style=render.ACCENT))
    console.print(
        Text(f"  cerebellum run {workflow} -i @{target / 'inputs' / 'small.json'} --sandbox", style=render.ACCENT)
    )


@app.command()
def validate(workflow: Annotated[Path, typer.Argument(help="Workflow YAML file.")]) -> None:
    """Validate a workflow definition."""
    wf = _load(workflow)
    line = Text("● ", style="green") + Text(f"{wf.name} v{wf.version} is valid", style="bold")
    line.append(
        f"  {len(wf.steps)} steps · {len(wf.fallbacks)} fallbacks · {len(wf.connectors)} connectors",
        style=render.MUTED,
    )
    console.print(line)


@app.command()
def show(workflow: Annotated[Path, typer.Argument(help="Workflow YAML file.")]) -> None:
    """Print a workflow's steps, dependencies, conditions and fallbacks."""
    console.print(render.dag_view(_load(workflow)))


@app.command()
def run(
    workflow: Annotated[Path, typer.Argument(help="Workflow YAML file.")],
    input: Annotated[
        str, typer.Option("--input", "-i", help="Run input: JSON text or @path/to/file.json.")
    ] = "{}",
    param: Annotated[
        list[str] | None, typer.Option("--param", "-p", help="Override a workflow param (key=value).")
    ] = None,
    mock: Annotated[bool, typer.Option("--mock", help="Use the offline mock AI provider.")] = False,
    sandbox: Annotated[
        bool, typer.Option("--sandbox", help="Start the local sandbox payments API for this command.")
    ] = False,
    sandbox_fail: Annotated[
        str, typer.Option("--sandbox-fail", help="Sandbox fault injection: never | always | first:N | rate:P.")
    ] = "never",
) -> None:
    """Start a workflow run."""
    settings = _settings()
    wf = _load(workflow)
    data = _parse_input(input)
    params = _parse_params(param or [])
    choice = select_provider(settings, force_mock=mock)
    handle = _start_sandbox(settings, sandbox_fail) if sandbox else None
    try:
        with Store(settings.db_path) as store:
            engine = Engine(store, settings, choice.provider)
            try:
                record = _drive_live(store, wf, choice.reason, lambda: engine.start(wf, data, params))
            except SpecError as exc:
                _invalid("run input is invalid", exc)
            except CerebellumError as exc:
                _fail(str(exc))
            _show_run(store, record, wf, choice.reason)
    finally:
        if handle is not None:
            handle.stop()
    raise typer.Exit(_exit_code(record.status))


@app.command()
def runs(
    status: Annotated[str | None, typer.Option("--status", "-s", help="Filter by run status.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum number of runs.")] = 20,
) -> None:
    """List recent runs."""
    wanted: RunStatus | None = None
    if status:
        try:
            wanted = RunStatus(status)
        except ValueError:
            _fail(
                f"unknown status {status!r}; use one of: {', '.join(s.value for s in RunStatus)}",
                EXIT_INVALID,
            )
    settings = _settings()
    with Store(settings.db_path) as store:
        records = store.list_runs(status=wanted, limit=limit)
        stale = {r.run_id for r in records if store.is_stale(r)}
        console.print(render.runs_table(records, now=time.time(), stale=stale))


@app.command()
def status(run_id: Annotated[str, typer.Argument(help="Run id.")]) -> None:
    """Show a run: steps, approvals, manual tasks and output."""
    settings = _settings()
    with Store(settings.db_path) as store:
        run = _get_run(store, run_id)
        workflow = _run_workflow(store, run)
        steps = store.get_steps(run_id)
        console.print(render.run_view(run, workflow, steps, mode=_mode(run), stale=store.is_stale(run)))
        now = time.time()
        approvals = store.list_approvals(run_id=run_id)
        if approvals:
            console.print()
            console.print(render.approvals_table(approvals, now=now))
        tasks = store.list_tasks(run_id=run_id)
        if tasks:
            console.print()
            console.print(render.tasks_table(tasks, now=now))
        _print_outcome(store, run)


@app.command()
def trace(run_id: Annotated[str, typer.Argument(help="Run id.")]) -> None:
    """Show a run's trace as a span waterfall."""
    settings = _settings()
    with Store(settings.db_path) as store:
        run = _get_run(store, run_id)
        console.print(render.header(f"trace · {run.workflow_name}", f"run {run_id}"))
        console.print(render.trace_table(build_spans(store.get_events(run_id)), now=time.time()))


@app.command()
def approvals(
    show_all: Annotated[bool, typer.Option("--all", help="Include decided approvals.")] = False,
) -> None:
    """List pending human approvals."""
    settings = _settings()
    with Store(settings.db_path) as store:
        items = store.list_approvals(status=None if show_all else "pending")
        console.print(render.approvals_table(items, now=time.time()))


def _decide(
    run_id: str,
    step: str | None,
    *,
    approved: bool,
    by: str,
    comment: str,
    resume: bool,
    sandbox: bool,
) -> None:
    settings = _settings()
    handle = _start_sandbox(settings, "never") if sandbox and resume else None
    try:
        with Store(settings.db_path) as store:
            run = _get_run(store, run_id)
            workflow = _run_workflow(store, run)
            engine = Engine(store, settings, select_provider(settings, force_mock=run.mock).provider)
            try:
                record = _drive_live(
                    store,
                    workflow,
                    _mode(run),
                    lambda: engine.decide(
                        run_id, step, approved=approved, by=by, comment=comment, resume=resume
                    ),
                    run_id=run_id,
                )
            except CerebellumError as exc:
                _fail(str(exc))
            verb = "approved" if approved else "rejected"
            line = Text("● " if approved else "✕ ", style="green" if approved else "red")
            line.append(f"{verb} by {by}")
            if comment:
                line.append(f" · {comment}", style=render.MUTED)
            console.print(line)
            _show_run(store, record, workflow, _mode(run))
    finally:
        if handle is not None:
            handle.stop()
    raise typer.Exit(_exit_code(record.status))


StepArg = Annotated[
    str | None, typer.Argument(help="Approval step id (optional when only one is pending).")
]
ByOption = Annotated[str, typer.Option("--by", help="Who is deciding.")]
CommentOption = Annotated[str, typer.Option("--comment", "-m", help="Decision note.")]
NoResumeOption = Annotated[
    bool, typer.Option("--no-resume", help="Record the decision without continuing the run.")
]
SandboxOption = Annotated[
    bool, typer.Option("--sandbox", help="Start the local sandbox payments API while resuming.")
]


@app.command()
def approve(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    step: StepArg = None,
    by: ByOption = DEFAULT_USER,
    comment: CommentOption = "",
    no_resume: NoResumeOption = False,
    sandbox: SandboxOption = False,
) -> None:
    """Approve a pending human approval and resume the run."""
    _decide(run_id, step, approved=True, by=by, comment=comment, resume=not no_resume, sandbox=sandbox)


@app.command()
def reject(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    step: StepArg = None,
    by: ByOption = DEFAULT_USER,
    comment: CommentOption = "",
    no_resume: NoResumeOption = False,
    sandbox: SandboxOption = False,
) -> None:
    """Reject a pending human approval and finish the run."""
    _decide(run_id, step, approved=False, by=by, comment=comment, resume=not no_resume, sandbox=sandbox)


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    sandbox: SandboxOption = False,
    sandbox_fail: Annotated[str, typer.Option("--sandbox-fail", help="Sandbox fault injection.")] = "never",
) -> None:
    """Resume a run after an approval, a crash or a failure."""
    settings = _settings()
    handle = _start_sandbox(settings, sandbox_fail) if sandbox else None
    try:
        with Store(settings.db_path) as store:
            run = _get_run(store, run_id)
            workflow = _run_workflow(store, run)
            engine = Engine(store, settings, select_provider(settings, force_mock=run.mock).provider)
            try:
                record = _drive_live(store, workflow, _mode(run), lambda: engine.resume(run_id), run_id=run_id)
            except LeaseUnavailable as exc:
                _fail(f"{exc}; try again once it finishes")
            except CerebellumError as exc:
                _fail(str(exc))
            _show_run(store, record, workflow, _mode(run))
    finally:
        if handle is not None:
            handle.stop()
    raise typer.Exit(_exit_code(record.status))


@tasks_app.callback(invoke_without_command=True)
def tasks_list(
    ctx: typer.Context,
    show_all: Annotated[bool, typer.Option("--all", help="Include resolved tasks.")] = False,
) -> None:
    """List manual tasks (open ones by default)."""
    if ctx.invoked_subcommand is not None:
        return
    settings = _settings()
    with Store(settings.db_path) as store:
        items = store.list_tasks(status=None if show_all else "open")
        console.print(render.tasks_table(items, now=time.time()))


@tasks_app.command("resolve")
def tasks_resolve(
    task_id: Annotated[str, typer.Argument(help="Task id.")],
    by: ByOption = DEFAULT_USER,
    note: Annotated[str, typer.Option("--note", "-m", help="Resolution note.")] = "",
) -> None:
    """Mark a manual task as resolved."""
    settings = _settings()
    with Store(settings.db_path) as store:
        try:
            task = store.resolve_task(task_id, by=by, note=note)
        except CerebellumError as exc:
            _fail(str(exc))
    console.print(Text("● ", style="green") + Text(f"{task.id} resolved by {by}"))


@connectors_app.command("check")
def connectors_check(workflow: Annotated[Path, typer.Argument(help="Workflow YAML file.")]) -> None:
    """Open every connector of a workflow and run its health check."""
    settings = _settings()
    wf = _load(workflow)

    async def check_all() -> list[tuple[str, str, HealthStatus]]:
        env = ConnectorEnv(home=settings.home, base_dir=Path(wf.base_dir))
        results = []
        for name, spec in wf.connectors.items():
            connector = create_connector(name, spec, env)
            try:
                await connector.open()
                health = await connector.health()
            except StepError as exc:
                health = HealthStatus(False, str(exc))
            finally:
                await connector.close()
            results.append((name, spec.type, health))
        return results

    results = asyncio.run(check_all())
    console.print(render.connectors_table(results))
    raise typer.Exit(EXIT_OK if all(health.ok for _, _, health in results) else EXIT_FAILED)


@app.command()
def sandbox(
    host: Annotated[str | None, typer.Option(help="Bind host.")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port.")] = None,
    fail: Annotated[str, typer.Option(help="Fault injection: never | always | first:N | rate:P.")] = "never",
) -> None:
    """Run the sandbox payments API in the foreground (Ctrl-C to stop)."""
    settings = _settings()
    try:
        mode = FailMode.parse(fail)
    except ValueError as exc:
        _fail(str(exc), EXIT_INVALID)
    bind_host = host or settings.sandbox_host
    bind_port = port or settings.sandbox_port
    console.print(render.header("sandbox payments API", f"http://{bind_host}:{bind_port} · fail mode {mode}"))
    uvicorn.run(create_payments_app(PaymentsState(mode)), host=bind_host, port=bind_port, log_level="warning")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/runtime/test_trace.py tests/cli -q`
Expected: all PASS (≈10 s: the outage test waits for real retry backoff). If Click's `CliRunner` returns `stderr` as `None`/raises on access in this Click version, change only the `invoke` helper to use `result.output`.

- [ ] **Step 7: Try it in a real terminal**

Run: `.venv/bin/cerebellum run src/cerebellum/templates/refund/workflow.yaml -i @src/cerebellum/templates/refund/inputs/large.json --sandbox --mock; echo "exit=$?"`
Expected: step table ending in `⏸ waiting approval`, approve/reject hints, `exit=3`.

- [ ] **Step 8: Lint and checkpoint**

Run: `make fmt && make lint && git status --short`

---

### Task 14: `cerebellum demo`, README, final verification

**Files:**
- Create: `src/cerebellum/cli/demo.py`
- Modify: `src/cerebellum/cli/app.py` (add `demo` command), `src/cerebellum/cli/render.py` (add `scenario_line`), `README.md` (rewrite)
- Test: `tests/cli/test_demo.py`

**Interfaces:**
- Consumes: CLI helpers (Task 13), `Engine`, `SandboxHandle`, `template_path`, `sandbox_db_path`.
- Produces: `Scenario(key, title, input_file, fail_mode)`, `SCENARIOS` (small, large, flaky, outage, fraud), `scenario_input(scenario) -> dict`, `async run_scenarios(engine, workflow, sandbox, on_result) -> list[tuple[Scenario, RunRecord]]`; `render.scenario_line(title, run) -> Text`; CLI `cerebellum demo [--live]`.

- [ ] **Step 1: Write the failing test**

`tests/cli/test_demo.py`:

```python
import re

import pytest
from rich.console import Console
from typer.testing import CliRunner

from cerebellum.cli import app as cli
from cerebellum.config import Settings
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
    for title in ("Small refund", "Large refund", "Payments API flaky", "Payments API down", "Suspicious reason"):
        assert title in text
    run_ids = re.findall(r"r_[0-9a-f]{8}", text)
    assert len(set(run_ids)) == 5
    assert "cerebellum approve" in text

    settings = Settings.from_env({"CEREBELLUM_HOME": str(tmp_path / "home")})
    with Store(settings.db_path) as store:
        decisions = sorted(
            (r.status.value, (r.output or {}).get("decision")) for r in store.list_runs()
        )
    assert decisions == sorted([
        ("succeeded", "refunded"),
        ("waiting_approval", None),
        ("succeeded", "refunded"),
        ("needs_attention", "manual"),
        ("succeeded", "denied"),
    ])

    again = runner.invoke(cli.app, ["demo"])  # the sandbox database is rebuilt each time
    assert again.exit_code == 0, again.stdout
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/cli/test_demo.py -q`
Expected: FAIL — `No such command 'demo'` (exit code 2).

- [ ] **Step 3: Implement**

`src/cerebellum/cli/demo.py`:

```python
"""`cerebellum demo`: the refund workflow end to end against the local sandbox."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cerebellum.runtime.engine import Engine
from cerebellum.runtime.store import RunRecord
from cerebellum.sandbox.server import SandboxHandle
from cerebellum.spec.models import Workflow
from cerebellum.templates import template_path


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    input_file: str
    fail_mode: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("small", "Small refund · auto-approved", "small.json", "never"),
    Scenario("large", "Large refund · waits for a human", "large.json", "never"),
    Scenario("flaky", "Payments API flaky · retried", "flaky.json", "first:2"),
    Scenario("outage", "Payments API down · manual fallback", "outage.json", "always"),
    Scenario("fraud", "Suspicious reason · denied by AI", "fraud.json", "never"),
)


def scenario_input(scenario: Scenario) -> dict[str, Any]:
    path = template_path("refund") / "inputs" / scenario.input_file
    return json.loads(path.read_text(encoding="utf-8"))


async def run_scenarios(
    engine: Engine,
    workflow: Workflow,
    sandbox: SandboxHandle,
    on_result: Callable[[Scenario, RunRecord], None],
) -> list[tuple[Scenario, RunRecord]]:
    results: list[tuple[Scenario, RunRecord]] = []
    try:
        for scenario in SCENARIOS:
            await asyncio.to_thread(sandbox.set_fail_mode, scenario.fail_mode)
            record = await engine.start(workflow, scenario_input(scenario))
            on_result(scenario, record)
            results.append((scenario, record))
    finally:
        await asyncio.to_thread(sandbox.set_fail_mode, "never")
    return results
```

Append to `src/cerebellum/cli/render.py`:

```python
def scenario_line(title: str, run: RunRecord) -> Text:
    glyph, style = RUN_GLYPHS.get(run.status.value, ("·", ""))
    decision = run.output.get("decision") if isinstance(run.output, dict) else None
    text = Text()
    text.append(f" {glyph} ", style=style)
    text.append(f"{title:<40}")
    text.append(f"{run.run_id}  ", style=ACCENT)
    text.append(f"{run.status.value.replace('_', ' '):<18}", style=style)
    if decision:
        text.append(str(decision), style="bold")
    return text
```

In `src/cerebellum/cli/app.py` add imports and the command:

```python
from rich.rule import Rule

from cerebellum.cli.demo import run_scenarios
from cerebellum.connectors.postgres import sandbox_db_path
```

```python
@app.command()
def demo(
    live: Annotated[
        bool, typer.Option("--live", help="Use the real Claude API instead of the offline mock AI.")
    ] = False,
) -> None:
    """Run five refund scenarios end to end against the local sandbox."""
    settings = _settings()
    # The demo always starts from the seed data; this file is the demo's own sandbox database.
    sandbox_db_path(settings.home, "orders_db").unlink(missing_ok=True)
    handle = _start_sandbox(settings, "never")
    try:
        with Store(settings.db_path) as store:
            choice = select_provider(settings, force_mock=not live)
            env = {**os.environ, "ORDERS_DSN": "sandbox", "PAYMENTS_URL": handle.url}
            workflow = load_workflow(template_path("refund") / "workflow.yaml", env=env)
            engine = Engine(store, settings, choice.provider)
            console.print(render.header("demo · refund_request", choice.reason))
            console.print(Rule(style=render.MUTED))
            results = asyncio.run(
                run_scenarios(
                    engine, workflow, handle,
                    lambda scenario, record: console.print(render.scenario_line(scenario.title, record)),
                )
            )
            console.print(Rule(style=render.MUTED))
            for _, record in results:
                if record.status is RunStatus.WAITING_APPROVAL:
                    console.print(Text("⏸ ", style="yellow") + Text("a human decision is pending"))
                    console.print(
                        Text(f"  cerebellum approve {record.run_id} manager_approval --by <you> --sandbox",
                             style=render.ACCENT)
                    )
            retried = next((r for s, r in results if s.key == "flaky"), None)
            if retried is not None:
                console.print(Text(f"  cerebellum trace {retried.run_id}", style=render.ACCENT)
                              + Text("   see the retries", style=render.MUTED))
            console.print(Text("  cerebellum tasks", style=render.ACCENT)
                          + Text("   the manual case opened by the fallback", style=render.MUTED))
            console.print(Text("  cerebellum runs", style=render.ACCENT)
                          + Text("   everything that just happened", style=render.MUTED))
    finally:
        handle.stop()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/cli/test_demo.py -q`
Expected: PASS (≈10 s for two demo runs with real retry backoff).

- [ ] **Step 5: Rewrite README.md**

Replace the whole file with:

````markdown
<div align="center">

# Cerebellum

**Describe the business process. Cerebellum makes it reliable, observable and recoverable.**

[![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square)](pyproject.toml)
[![Version](https://img.shields.io/badge/version-0.2.0-purple?style=flat-square)](src/cerebellum/_version.py)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

Cerebellum runs business workflows that combine **data** (PostgreSQL), **LLMs** (Claude with
JSON-Schema outputs), **SaaS / REST APIs** and **people**. You declare the process in YAML.
Cerebellum schedules it, validates every step, pauses for human approval, retries and falls back
when systems misbehave, records a full trace, and resumes exactly where it stopped — even after
the process is killed.

```text
customer asks for a refund
 1  fetch_order        query      PostgreSQL
 2  policy_check       validate   deterministic rules
 3  assess_request     ai         Claude · structured output · repaired if invalid
 4  manager_approval   approval   only when amount > $500 or risk is high
 5  issue_refund       http       payments API · retry ×3 · idempotency key
       ⤳ open_manual_case  task   when the API keeps failing
 6  mark_refunded      query      write back to the order
```

## Quickstart

```bash
make install            # python3 -m venv .venv && pip install -e ".[dev]"
.venv/bin/cerebellum demo
```

No API key, database or Docker is needed: the demo uses a SQLite sandbox seeded with orders, a
local mock payments API with fault injection, and an offline mock AI. It runs five scenarios —
automatic refund, human approval, flaky API retried, API outage handed to a person, suspicious
request denied by the AI — and leaves one run waiting for you:

```bash
cerebellum approve <run-id> manager_approval --by you --sandbox
cerebellum trace <run-id>
cerebellum tasks
```

Use Claude instead of the mock by setting `ANTHROPIC_API_KEY` (or `ant auth login`) and running
`cerebellum demo --live`.

## Define a workflow

```yaml
name: refund_request
params: {approval_threshold: 500}
input:
  order_id: {type: string, required: true}
  amount: {type: number, required: true}
connectors:
  orders_db: {type: postgres, dsn: "${ORDERS_DSN:-sandbox}", seed: seed.sql}
  payments:  {type: rest, base_url: "${PAYMENTS_URL:-http://127.0.0.1:8787}"}
steps:
  - id: fetch_order
    type: query
    connector: orders_db
    sql: SELECT id, amount, status FROM orders WHERE id = :order_id
    params: {order_id: "{{ input.order_id }}"}
    expect: one
  - id: manager_approval
    type: approval
    needs: [fetch_order]
    when: "input.amount > params.approval_threshold"
    title: "Refund {{ input.amount }} for {{ input.order_id }}"
  - id: issue_refund
    type: http
    needs: [manager_approval]
    connector: payments
    method: POST
    path: /refunds
    body: {order_id: "{{ input.order_id }}", amount: "{{ input.amount }}"}
    retry: {max: 3, base: 500ms}
    on_failure: {fallback: open_manual_case}
fallbacks:
  - {id: open_manual_case, type: task, title: "Refund {{ input.order_id }} by hand", assignee: finance-ops}
```

The full example lives in [`src/cerebellum/templates/refund/workflow.yaml`](src/cerebellum/templates/refund/workflow.yaml);
`cerebellum init` copies it into your project.

| Step type  | Does                                                    | Output                          |
|------------|---------------------------------------------------------|---------------------------------|
| `query`    | Parameterised SQL (`:name` binds only, never templated) | row / rows / `{rowcount}`       |
| `http`     | REST call with `Idempotency-Key: <run>:<step>`          | `{status, body, headers}`       |
| `ai`       | Claude structured output validated by JSON Schema       | the validated object            |
| `validate` | Deterministic business rules                            | `{passed, checked}`             |
| `approval` | Pauses the run until a human decides                    | `{approved, by, comment, ...}`  |
| `task`     | Opens a manual task in the inbox                        | `{task_id, title, assignee}`    |

Every step accepts `needs`, `when`, `timeout`, `retry` and `on_failure`. Expressions are
sandboxed Jinja over `input`, `params`, `steps.<id>.{output,status,error}` and `run`.

## Reliability semantics

- **Event-sourced state.** Every state change is appended to an event log in SQLite together with
  its projection, in one transaction. Traces, resume and the CLI all read the same source.
- **Failure classification.** Timeouts, connection errors, 5xx, 429 and invalid AI output are
  retried with exponential backoff; 4xx, rule violations, missing rows, template errors, refusals
  and budget overruns fail immediately.
- **Fallbacks.** When retries run out, the step's fallback runs; the step becomes `recovered`, its
  output is the fallback's, downstream continues, and the run ends `needs_attention`.
- **Human approval without a waiting process.** The run suspends (`waiting_approval`), the process
  exits, and `approve` / `reject` resumes it later. Overdue approvals apply `on_timeout`.
- **Crash-safe resume.** A lease marks the process driving a run. If it dies, `cerebellum resume`
  re-runs only unfinished steps; HTTP side effects are deduplicated by the idempotency key.
- **Budget cap.** `limits.budget_usd` stops the run when AI spend crosses the limit.

## CLI

| Command | Purpose |
|---|---|
| `cerebellum init [dir]` | Scaffold the refund example and `.env.example` |
| `cerebellum validate <wf>` / `show <wf>` | Check a definition / print its steps |
| `cerebellum run <wf> -i @input.json [--param k=v] [--sandbox] [--mock]` | Start a run (exit 3 while waiting for approval) |
| `cerebellum runs` / `status <run>` / `trace <run>` | Observe runs, steps and span waterfalls |
| `cerebellum approvals` / `approve <run>` / `reject <run>` | Human-in-the-loop decisions |
| `cerebellum resume <run>` | Continue after a crash, a failure or an approval |
| `cerebellum tasks [resolve <id>]` | Manual tasks opened by fallbacks |
| `cerebellum connectors check <wf>` | Health-check every connector |
| `cerebellum sandbox [--fail first:2]` | Run the mock payments API in the foreground |
| `cerebellum demo [--live]` | Five end-to-end refund scenarios |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables the Claude provider (otherwise mock AI) |
| `CEREBELLUM_MODEL` | `claude-opus-5-5` | Default model for `ai` steps |
| `CEREBELLUM_MOCK` | unset | `1` forces the mock AI |
| `CEREBELLUM_HOME` | `./.cerebellum` | Run history and sandbox databases |
| `CEREBELLUM_SANDBOX_PORT` | `8787` | Port of the local payments sandbox |
| `CEREBELLUM_PRICING_FILE` | unset | JSON overriding model prices |
| `ORDERS_DSN` | `sandbox` | Real PostgreSQL DSN for the example (`pip install -e ".[postgres]"`, `docker compose up -d`) |
| `PAYMENTS_URL` / `PAYMENTS_TOKEN` | sandbox | Payments API used by the example |

## Architecture

```
 CLI (Typer + Rich) ──┐
                      ▼
 spec/      YAML → validated Workflow (pydantic, sandboxed Jinja, JSON Schema)
 runtime/   Engine · scheduler · retry/fallback · approvals · leases · event store (SQLite)
 steps/     query · http · ai · validate · task
 connectors/ postgres (psycopg | SQLite sandbox) · rest (httpx)      ai/ Claude · mock
 sandbox/   mock payments API with fault injection and idempotency
```

## Development

```bash
make install
make test        # ruff format --check, ruff check, pytest
make demo
```

Optional suites: `CEREBELLUM_TEST_PG_DSN=postgresql://… pytest -m postgres` and
`pytest -m live` (real Claude calls).

## Roadmap

- `cerebellum ui` — local dashboard: DAG, trace waterfall, approvals inbox, retries and fallbacks
- Evals — `cerebellum eval` suites with pass-rate trends, plus `cerebellum new "<process description>"`

## License

MIT
````

- [ ] **Step 6: Full verification**

Run each and read the output:

```bash
make test
.venv/bin/cerebellum demo
.venv/bin/cerebellum runs
```

Then, with the waiting run id printed by the demo:

```bash
.venv/bin/cerebellum approve <run-id> manager_approval --by reviewer --sandbox
.venv/bin/cerebellum trace <flaky-run-id>
.venv/bin/cerebellum tasks
```

Expected: `make test` passes; the demo prints five scenario lines (refunded, waiting approval,
refunded, needs attention/manual, denied); approve ends `● succeeded` with `"decision": "refunded"`;
the trace shows `issue_refund #1..#3` with `→ 503` children before `→ 201`; tasks lists one open task.

- [ ] **Step 7: Checkpoint**

Run: `git status --short`
Report the changed files to the user; do not commit (user rule).

---

## Self-Review Notes

- Spec coverage (phase 1 scope): §3 YAML definition → Tasks 2–3; §4 runtime, events, HITL, leases → Tasks 4–5, 10–11; §5 connectors + sandbox API + docker-compose → Tasks 6, 7, 12; §6 structured AI → Tasks 8–9; §7 CLI (`init, validate, show, run, runs, status, trace, approvals, approve, reject, resume, tasks, connectors check, sandbox, demo`) → Tasks 13–14; §10 tests → every task; §1.5 old-code removal → Task 1. Deferred by the spec's phasing: `cerebellum ui` (phase 2), `cerebellum eval` / `cerebellum new` (phase 3).
- Type consistency checked: `StepRuntime.record_call(kind, data, cost_usd)` (Task 9) matches `_Execution._recorder` (Task 10); `Store.step_transition(..., output=UNSET, error=UNSET)` used with keyword arguments everywhere; `Engine.decide(run_id, step_id=None, *, approved, by, comment, resume)` matches CLI `_decide`; `start_sandbox(host, port, fail)` matches CLI/demo; `build_spans` labels match CLI trace assertions.
