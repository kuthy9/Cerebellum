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


class ConfigError(CerebellumError):
    """A configured file or setting (for example CEREBELLUM_PRICING_FILE) is unusable."""


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


class NotFound(CerebellumError):
    """The requested run, approval, task or workflow does not exist."""


class RunNotFound(NotFound):
    """No run with the given id exists."""


class LeaseUnavailable(CerebellumError):
    """Another process currently owns the run."""


class ApprovalExpired(CerebellumError):
    """A decision arrived after the approval's deadline; its on_timeout was applied instead."""
