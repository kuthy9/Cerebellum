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
DEFAULT_UI_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 7400
DEFAULT_LEASE_SECONDS = 30.0
DEFAULT_STEP_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_PARALLEL = 8
# Adaptive thinking (always on for Claude Opus 5.5) counts toward max_tokens; leave room for it
# while staying under the SDK's non-streaming request limit.
DEFAULT_AI_MAX_TOKENS = 16000
# Dashboard server: how often the worker sweeps for overdue approvals, and how often the live
# event stream polls the event table (it also sees events written by other processes).
DEFAULT_WORKER_INTERVAL_SECONDS = 30.0
DEFAULT_STREAM_POLL_SECONDS = 0.5
# On Ctrl-C the dashboard waits this long for open connections. Browser tabs keep the event
# stream open indefinitely, so without a limit the server would wait until every tab closed.
DEFAULT_UI_SHUTDOWN_GRACE_SECONDS = 2.0
# `cerebellum eval`: the share of cases that must pass for exit code 0 (spec: --min-pass 0.9).
DEFAULT_EVAL_MIN_PASS = 0.9
# The file in CEREBELLUM_HOME that `cerebellum eval` holds a lock on: one eval at a time per home.
EVAL_LOCK_FILE = "eval.lock"
# `cerebellum evals prune`: how many of each suite's newest eval runs keep their sandbox directory.
DEFAULT_EVAL_KEEP = 10
# `cerebellum new`: model attempts per draft — the first reply plus repairs from loader issues.
DEFAULT_NEW_ATTEMPTS = 3
# SQLite sandbox: after a caller stops waiting (a step timeout), its statement is interrupted
# and the interrupt repeated at this interval until the statement's thread ends; SQLite drops an
# interrupt that arrives before the statement starts.
SANDBOX_INTERRUPT_RETRY_SECONDS = 0.05
# CEREBELLUM_PRICING_FILE: the highest price accepted, in USD per million tokens ($1 per token).
# Far above any real model, low enough that costs stay finite numbers a budget can compare.
MAX_PRICE_PER_MILLION_TOKENS = 1_000_000.0

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
    ui_host: str = DEFAULT_UI_HOST
    ui_port: int = DEFAULT_UI_PORT
    worker_interval: float = DEFAULT_WORKER_INTERVAL_SECONDS
    stream_poll: float = DEFAULT_STREAM_POLL_SECONDS

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
            ui_host=env.get("CEREBELLUM_UI_HOST", DEFAULT_UI_HOST),
            ui_port=int(env.get("CEREBELLUM_UI_PORT", DEFAULT_UI_PORT)),
            worker_interval=float(
                env.get("CEREBELLUM_WORKER_INTERVAL", DEFAULT_WORKER_INTERVAL_SECONDS)
            ),
            stream_poll=float(env.get("CEREBELLUM_STREAM_POLL", DEFAULT_STREAM_POLL_SECONDS)),
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
