"""Run an eval suite: one real run per case, decided and checked automatically, then compared
with the previous completed run of the same suite."""

from __future__ import annotations

import contextlib
import dataclasses
import secrets
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

import httpx

from cerebellum.ai.base import AIProvider
from cerebellum.config import Settings
from cerebellum.errors import CerebellumError
from cerebellum.evals.checks import Check, check_case, run_view
from cerebellum.evals.suite import EvalCase, LoadedSuite
from cerebellum.runtime.clock import Clock
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus
from cerebellum.runtime.store import EvalResultRecord, EvalRunRecord, RunRecord, Store

# Recorded as the approver of eval approvals and the resolver of tasks eval cases open.
EVAL_ACTOR = "eval"
FailModeSetter = Callable[[str], Awaitable[None]]


def new_eval_run_id() -> str:
    return "ev_" + secrets.token_hex(4)


def eval_home(settings: Settings, eval_run_id: str) -> Path:
    """Where an eval run's sandbox databases live: fresh per run, so results are repeatable."""
    return settings.home / "evals" / eval_run_id


class EvalRunner:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        provider: AIProvider,
        *,
        set_fail_mode: FailModeSetter | None = None,
        clock: Clock | None = None,
        http_transports: Mapping[str, httpx.AsyncBaseTransport] | None = None,
        jitter: float = 0.1,
    ):
        self.store = store
        self.settings = settings
        self.provider = provider
        self.set_fail_mode = set_fail_mode
        self.clock = clock
        self.http_transports = http_transports
        self.jitter = jitter

    async def run(
        self,
        loaded: LoadedSuite,
        *,
        on_result: Callable[[EvalCase, EvalResultRecord], None] | None = None,
    ) -> EvalRunRecord:
        suite = loaded.suite
        baseline = self.store.latest_eval_run(suite.suite)
        previous = (
            {result.case_id: result.passed for result in self.store.get_eval_results(baseline.id)}
            if baseline
            else {}
        )
        eval_run_id = new_eval_run_id()
        home = eval_home(self.settings, eval_run_id)
        home.mkdir(parents=True, exist_ok=True)
        engine = Engine(
            self.store,
            dataclasses.replace(self.settings, home=home),
            self.provider,
            clock=self.clock,
            http_transports=self.http_transports,
            jitter=self.jitter,
            eval_runs=True,
        )
        self.store.create_eval_run(
            eval_run_id,
            suite=suite.suite,
            suite_path=str(loaded.path),
            workflow_name=loaded.workflow.name,
            workflow_digest=loaded.workflow.digest,
            mock=self.provider.mock,
            total=len(suite.cases),
            baseline_id=baseline.id if baseline else None,
        )
        run_ids: list[str] = []
        try:
            for position, case in enumerate(suite.cases):
                result = await self._run_case(
                    engine, loaded, eval_run_id, position, case, previous.get(case.id)
                )
                if result.run_id:
                    run_ids.append(result.run_id)
                if on_result is not None:
                    on_result(case, result)
        except BaseException as exc:  # interrupted or broken: never leave the eval "running"
            self.store.finish_eval_run(
                eval_run_id,
                status="errored",
                error=str(exc) or type(exc).__name__,
                **self._ai_stats(run_ids),
            )
            raise
        finally:
            if self.set_fail_mode is not None:
                with contextlib.suppress(Exception):
                    await self.set_fail_mode("never")
        return self.store.finish_eval_run(
            eval_run_id, status="completed", **self._ai_stats(run_ids)
        )

    async def _run_case(
        self,
        engine: Engine,
        loaded: LoadedSuite,
        eval_run_id: str,
        position: int,
        case: EvalCase,
        baseline_passed: bool | None,
    ) -> EvalResultRecord:
        record: RunRecord | None = None
        error: str | None = None
        mode = loaded.fail_mode(case)
        try:
            if self.set_fail_mode is not None:
                await self.set_fail_mode(mode)
            elif mode != "never":
                raise CerebellumError(
                    f"this case needs the sandbox payments API (fail mode {mode}), "
                    "which is not running"
                )
            record = await engine.start(loaded.workflow, case.input, eval_run_id=eval_run_id)
            record = await self._decide(engine, record, loaded, case)
        except CerebellumError as exc:
            error = str(exc)
        checks: list[Check] = []
        cost = 0.0
        duration: float | None = None
        if record is not None:
            run = self.store.get_run(record.run_id)
            view = run_view(
                run,
                self.store.get_steps(run.run_id),
                self.store.list_tasks(run_id=run.run_id),
                self.store.list_approvals(run_id=run.run_id),
            )
            checks = check_case(case.expect, case.asserts, view)
            cost, duration = run.cost_usd, view["run"]["duration_s"]
            self._close_tasks(run.run_id, case.id)
        return self.store.record_eval_result(
            eval_run_id,
            case_id=case.id,
            position=position,
            run_id=record.run_id if record else None,
            passed=error is None and all(check.passed for check in checks),
            baseline_passed=baseline_passed,
            checks=[check.to_json() for check in checks],
            error=error,
            cost_usd=cost,
            duration_s=duration,
        )

    async def _decide(
        self, engine: Engine, record: RunRecord, loaded: LoadedSuite, case: EvalCase
    ) -> RunRecord:
        """Answer every approval the run waits for with the case's decision, as `eval`."""
        approved = loaded.decision(case) == "approved"
        comment = f"decided by eval case {case.id}"
        # An approval step waits at most once per run, so this ends; the bound is a guard.
        for _ in range(len(loaded.workflow.steps) + 1):
            if record.status is not RunStatus.WAITING_APPROVAL:
                break
            pending = self.store.list_approvals(run_id=record.run_id, status="pending")
            if not pending:
                break
            for approval in pending[:-1]:
                await engine.decide(
                    record.run_id,
                    approval.step_id,
                    approved=approved,
                    by=EVAL_ACTOR,
                    comment=comment,
                    resume=False,
                )
            record = await engine.decide(
                record.run_id,
                pending[-1].step_id,
                approved=approved,
                by=EVAL_ACTOR,
                comment=comment,
            )
        return record

    def _close_tasks(self, run_id: str, case_id: str) -> None:
        """Eval traffic must not land in the people's inbox: close the tasks a case opened."""
        for task in self.store.list_tasks(run_id=run_id, status="open"):
            self.store.resolve_task(
                task.id, by=EVAL_ACTOR, note=f"opened by eval case {case_id}; closed automatically"
            )

    def _ai_stats(self, run_ids: list[str]) -> dict[str, int]:
        """How often an AI step's first reply already matched its schema, and how many repair
        turns were needed (from the llm.call events of the case runs)."""
        first_try = first_ok = repairs = 0
        for run_id in run_ids:
            for event in self.store.get_events(run_id):
                if event.type != "llm.call":
                    continue
                if event.data.get("repair", 0) == 0:
                    first_try += 1
                    first_ok += int(bool(event.data.get("ok")))
                else:
                    repairs += 1
        return {"ai_first_try": first_try, "ai_first_ok": first_ok, "ai_repairs": repairs}
