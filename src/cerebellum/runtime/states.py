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
