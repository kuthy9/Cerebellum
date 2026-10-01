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
        (S.PENDING, S.RUNNING),
        (S.PENDING, S.SKIPPED),
        (S.PENDING, S.CANCELLED),
        (S.RUNNING, S.SUCCEEDED),
        (S.RUNNING, S.RETRYING),
        (S.RETRYING, S.RUNNING),
        (S.RUNNING, S.WAITING),
        (S.WAITING, S.SUCCEEDED),
        (S.WAITING, S.FAILED),
        (S.FAILED, S.RECOVERED),
        (S.RUNNING, S.PENDING),
        (S.FAILED, S.PENDING),
        (S.CANCELLED, S.PENDING),
    ],
)
def test_allowed_step_transitions(current, target):
    check_step_transition("s", current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.PENDING, S.SUCCEEDED),
        (S.SUCCEEDED, S.RUNNING),
        (S.SKIPPED, S.PENDING),
        (S.RECOVERED, S.FAILED),
        (S.WAITING, S.RETRYING),
    ],
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
