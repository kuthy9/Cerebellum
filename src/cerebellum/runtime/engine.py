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


def refuse_eval_run(run: RunRecord) -> None:
    """Eval case runs are records of one eval: driving them again from outside the eval runner
    would leave its isolated sandbox and change a result nobody re-checks."""
    if run.eval_run_id is not None:
        raise CerebellumError(
            f"run {run.run_id} belongs to eval {run.eval_run_id}; eval runs are records and "
            "cannot be resumed or decided — run the suite again instead"
        )


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
        eval_runs: bool = False,
    ):
        self.store = store
        self.settings = settings
        self.ai = ai
        self.clock: Clock = clock or store.clock
        self.http_transports = dict(http_transports or {})
        self.owner = owner or _default_owner()
        self.jitter = jitter
        # Only the eval runner's engine may drive eval case runs past their first drive.
        self.eval_runs = eval_runs

    # ── public API ───────────────────────────────────────────────────────────

    def prepare(
        self,
        workflow: Workflow,
        input: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        *,
        run_id: str | None = None,
        eval_run_id: str | None = None,
    ) -> RunRecord:
        """Validate the input and record a new run without driving it (the dashboard drives it
        in the background so the request can return the run id at once)."""
        data = validate_input(workflow, dict(input or {}))
        resolved = resolve_params(workflow, params)
        self.store.save_workflow(workflow)
        return self.store.create_run(
            run_id or new_run_id(),
            workflow,
            data,
            resolved,
            mock=self.ai.mock,
            eval_run_id=eval_run_id,
        )

    async def start(
        self,
        workflow: Workflow,
        input: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        *,
        run_id: str | None = None,
        eval_run_id: str | None = None,
    ) -> RunRecord:
        record = self.prepare(workflow, input, params, run_id=run_id, eval_run_id=eval_run_id)
        return await self._drive(record.run_id, workflow)

    async def resume(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        if not self.eval_runs:
            refuse_eval_run(run)
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
        if not self.eval_runs:
            refuse_eval_run(self.store.get_run(run_id))
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
        # Decide them all before resuming anything: a resume would itself expire the run's other
        # overdue approvals, and deciding those again afterwards fails.
        due: dict[str, None] = {}  # run ids, in order, without duplicates
        for approval in self.store.list_approvals(status="pending"):
            if approval.expires_at is None or approval.expires_at > now:
                continue
            if self.store.get_run(approval.run_id).eval_run_id is not None:
                continue  # left by an interrupted eval; only that eval's runner may decide it
            self.store.decide_approval(
                approval.id,
                approved=approval.on_timeout == "approve",
                by="system",
                comment="approval timed out",
                expired=True,
            )
            due[approval.run_id] = None
        resumed: list[str] = []
        for run_id in due:
            try:
                await self.resume(run_id)
                resumed.append(run_id)
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
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            await pool.close()
            self.store.release_lease(run_id, self.owner)
        return self.store.get_run(run_id)  # read after the lease is released

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
                run_id,
                record.step_id,
                S.PENDING,
                event="reset",
                output=None,
                error=None,
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
        # Failed steps whose fallback is in flight: they block nothing until it has decided.
        self.recovering: set[str] = set()

    async def run(self) -> None:
        running: dict[str, asyncio.Task[None]] = {}
        for failed_step, fallback_id, error in self._interrupted_fallbacks():
            self.recovering.add(failed_step)
            running[failed_step] = asyncio.create_task(
                self._run_fallback(failed_step, fallback_id, error), name=f"step:{failed_step}"
            )
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
                # Last look before suspending: a decision written by another process between the
                # top-of-loop check and here must not leave the run waiting.
                if self._apply_approval_states() or not self._finish():
                    continue
                return
            done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                running.pop(task.get_name().removeprefix("step:"))
                task.result()  # unexpected engine errors must surface, not vanish

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
                    blocked = [
                        dep
                        for dep in step.needs
                        if statuses[dep] in STEP_BLOCKING and dep not in self.recovering
                    ]
                    if blocked:
                        reason = f"upstream step '{blocked[0]}' {statuses[blocked[0]].value}"
                if reason:
                    self.store.step_transition(
                        self.run_id,
                        step.id,
                        S.CANCELLED,
                        event="cancelled",
                        data={"reason": reason},
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
            overdue = approval.expires_at is not None and approval.expires_at <= now
            if approval.status == "pending" and overdue:
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
                    self.run_id,
                    step_id,
                    S.SUCCEEDED,
                    event="succeeded",
                    span_id=span,
                    output=output,
                    ended_at=now,
                )
            else:
                reason = f"rejected by {approval.decided_by}"
                if approval.comment:
                    reason += f": {approval.comment}"
                self.store.step_transition(
                    self.run_id,
                    step_id,
                    S.FAILED,
                    event="failed",
                    span_id=span,
                    output=output,
                    error=reason,
                    ended_at=now,
                    data={"kind": "rejected", "retryable": False},
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
                self._fail_immediately(
                    step_id, StepError(str(exc), retryable=False, kind="template")
                )
                return
            if skip:
                self.store.step_transition(
                    self.run_id,
                    step_id,
                    S.SKIPPED,
                    event="skipped",
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
                self.recovering.add(step_id)  # before any await: the scheduler must not cancel
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
                self.run_id,
                step.id,
                S.RUNNING,
                event="started",
                span_id=span,
                attempts=attempt,
                started_at=self.engine.clock.now() if n == 1 else None,
                data=started,
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
                error = StepError(
                    f"timed out after {step.timeout:g}s", retryable=True, kind="timeout"
                )
            except StepError as exc:
                error = exc
            except TemplateError as exc:
                error = StepError(str(exc), retryable=False, kind="template")
            except Exception as exc:  # unexpected library/executor error: fail the step cleanly
                error = StepError(f"{type(exc).__name__}: {exc}", retryable=False, kind="internal")
            else:
                self.store.step_transition(
                    self.run_id,
                    step.id,
                    S.SUCCEEDED,
                    event="succeeded",
                    span_id=span,
                    output=output,
                    error=None,
                    ended_at=self.engine.clock.now(),
                    data={"attempt": attempt},
                )
                return _Outcome(True, output)

            if isinstance(error, BudgetExceeded):
                self.halt_reason = str(error)
            if error.retryable and n < max_attempts and self.halt_reason is None:
                delay = backoff_delay(step.retry, n, jitter=self.engine.jitter)
                self.store.step_transition(
                    self.run_id,
                    step.id,
                    S.RETRYING,
                    event="retrying",
                    span_id=span,
                    error=str(error),
                    data={
                        "attempt": attempt,
                        "kind": error.kind,
                        "delay_s": round(delay, 3),
                        "details": error.details,
                    },
                )
                await self.engine.clock.sleep(delay)
                continue
            self.store.step_transition(
                self.run_id,
                step.id,
                S.FAILED,
                event="failed",
                span_id=span,
                error=str(error),
                ended_at=self.engine.clock.now(),
                data={
                    "attempt": attempt,
                    "kind": error.kind,
                    "retryable": error.retryable,
                    "details": error.details,
                },
            )
            return _Outcome(False, error=error)
        raise AssertionError("unreachable: the loop always returns")

    def _fail_immediately(self, step_id: str, error: StepError) -> None:
        record = self.store.get_step(self.run_id, step_id)
        attempt = record.attempts + 1
        span = _span(step_id, attempt)
        now = self.engine.clock.now()
        self.store.step_transition(
            self.run_id,
            step_id,
            S.RUNNING,
            event="started",
            span_id=span,
            attempts=attempt,
            started_at=now,
            data={"attempt": attempt},
        )
        self.store.step_transition(
            self.run_id,
            step_id,
            S.FAILED,
            event="failed",
            span_id=span,
            error=str(error),
            ended_at=now,
            data={"attempt": attempt, "kind": error.kind, "retryable": False},
        )

    async def _run_fallback(self, failed_step: str, fallback_id: str, error: StepError) -> None:
        fallback = self.wf.step(fallback_id)
        extra = {"failure": {"step": failed_step, "error": str(error), "kind": error.kind}}
        try:
            outcome = await self._execute(fallback, extra, fallback_for=failed_step)
            if outcome.ok:
                self._mark_recovered(failed_step, fallback_id, outcome.output)
        finally:
            self.recovering.discard(failed_step)

    def _mark_recovered(self, failed_step: str, fallback_id: str, output: Any) -> None:
        self.store.step_transition(
            self.run_id,
            failed_step,
            S.RECOVERED,
            event="recovered",
            output=output,
            data={"fallback": fallback_id},
        )

    def _interrupted_fallbacks(self) -> list[tuple[str, str, StepError]]:
        """After a crash, finish recovering failed steps whose fallback had already started rather
        than re-running the failed step. Returns the fallbacks that still have to run."""
        steps = self.store.get_steps(self.run_id)
        unfinished: list[tuple[str, str, StepError]] = []
        for step in self.wf.steps:
            if steps[step.id].status is not S.FAILED or not step.on_failure:
                continue
            fallback_id = step.on_failure.fallback
            fallback = steps[fallback_id]
            if fallback.status is S.SUCCEEDED:
                self._mark_recovered(step.id, fallback_id, fallback.output)
            elif fallback.status is S.PENDING and fallback.attempts > 0:
                unfinished.append((step.id, fallback_id, self._recorded_failure(step.id)))
        return unfinished

    def _recorded_failure(self, step_id: str) -> StepError:
        kind = "error"
        for event in reversed(self.store.get_events(self.run_id)):
            if event.type == "step.failed" and event.step_id == step_id:
                kind = event.data.get("kind", kind)
                break
        error = self.store.get_step(self.run_id, step_id).error or "failed"
        return StepError(error, retryable=False, kind=kind)

    def _request_approval(self, step: ApprovalStep, ctx: Mapping[str, Any]) -> None:
        record = self.store.get_step(self.run_id, step.id)
        attempt = record.attempts + 1
        span = _span(step.id, attempt)
        now = self.engine.clock.now()
        self.store.step_transition(
            self.run_id,
            step.id,
            S.RUNNING,
            event="started",
            span_id=span,
            attempts=attempt,
            started_at=now,
            data={"attempt": attempt, "type": "approval"},
        )
        try:
            title = render(step.title, ctx)
        except TemplateError as exc:
            self.store.step_transition(
                self.run_id,
                step.id,
                S.FAILED,
                event="failed",
                span_id=span,
                error=str(exc),
                ended_at=now,
                data={"kind": "template", "retryable": False},
            )
            return
        context = {
            "input": ctx["input"],
            "steps": {ref: ctx["steps"][ref]["output"] for ref in step.show},
        }
        expires_at = now + step.timeout if step.timeout is not None else None
        self.store.request_approval(
            self.run_id,
            step.id,
            title=title,
            context=context,
            expires_at=expires_at,
            on_timeout=step.on_timeout,
            span_id=span,
        )

    # ── recording helpers ────────────────────────────────────────────────────

    def _recorder(self, step_id: str, span: str) -> Any:
        def record(kind: str, data: dict[str, Any], cost_usd: float = 0.0) -> None:
            self.store.record_call(
                self.run_id,
                step_id,
                kind,
                span_id=f"{span}:{kind}:{secrets.token_hex(3)}",
                parent_span_id=span,
                data=data,
                cost_usd=cost_usd,
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
                self.run_id,
                step_id,
                title=title,
                assignee=assignee,
                payload=payload,
                span_id=span,
            )
            return task.id

        return create

    # ── completion ───────────────────────────────────────────────────────────

    def _finish(self) -> bool:
        """Record the run's outcome. False when a suspend was refused because a decision arrived:
        the caller keeps driving."""
        steps = self.store.get_steps(self.run_id)
        main = [steps[step.id] for step in self.wf.steps]
        waiting = [s.step_id for s in main if s.status is S.WAITING]
        if waiting:
            return self.store.suspend_run(self.run_id, self.engine.owner, waiting)
        rejected = [
            s.step_id
            for s in main
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
            self.run_id,
            status,
            event="completed",
            output=output,
            error=error,
            data={"failed": failed, "rejected": rejected, "recovered": recovered},
        )
        return True


def _approval_output(approval: ApprovalRecord) -> dict[str, Any]:
    return {
        "approved": approval.status == "approved",
        "by": approval.decided_by,
        "comment": approval.comment or "",
        "decided_at": approval.decided_at,
        "auto": approval.decided_by == "system",
    }
