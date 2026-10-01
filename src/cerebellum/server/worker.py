"""Background engine work for the dashboard: runs started from the UI, resumes after decisions,
and a periodic sweep that applies approval timeouts."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Mapping
from typing import Any

import httpx

from cerebellum.ai.base import AIProvider
from cerebellum.ai.mock import MockProvider
from cerebellum.config import Settings
from cerebellum.errors import CerebellumError, LeaseUnavailable
from cerebellum.runtime.clock import Clock
from cerebellum.runtime.engine import Engine, refuse_eval_run
from cerebellum.runtime.states import RUN_RESUMABLE
from cerebellum.runtime.store import ApprovalRecord, RunRecord, Store
from cerebellum.spec.models import Workflow

log = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        provider: AIProvider,
        *,
        clock: Clock | None = None,
        http_transports: Mapping[str, httpx.AsyncBaseTransport] | None = None,
        interval: float | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.provider = provider
        self.clock: Clock = clock or store.clock
        self.http_transports = dict(http_transports or {})
        self.interval = settings.worker_interval if interval is None else interval
        self._mock: AIProvider | None = provider if provider.mock else None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._sweeper: asyncio.Task[None] | None = None

    def engine(self, *, mock: bool) -> Engine:
        """An engine whose AI provider matches the run: mock runs stay on the mock provider."""
        provider = self.provider
        if mock and not provider.mock:
            self._mock = self._mock or MockProvider()
            provider = self._mock
        return Engine(
            self.store,
            self.settings,
            provider,
            clock=self.clock,
            http_transports=self.http_transports,
        )

    def start_run(
        self,
        workflow: Workflow,
        input: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> RunRecord:
        engine = self.engine(mock=self.provider.mock)
        record = engine.prepare(workflow, input, params)
        self._spawn(engine.resume(record.run_id))
        return record

    def resume(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        refuse_eval_run(run)  # here, so the API answers 409 instead of failing in the background
        if run.status not in RUN_RESUMABLE:
            raise CerebellumError(f"run {run_id} is {run.status.value} and cannot be resumed")
        if run.lease_until is not None and run.lease_until >= self.clock.now():
            raise LeaseUnavailable(f"run {run_id} is being executed by another process")
        self._spawn(self.engine(mock=run.mock).resume(run_id))
        return run

    async def decide(
        self, approval_id: str, *, approved: bool, by: str, comment: str = ""
    ) -> ApprovalRecord:
        approval = self.store.get_approval(approval_id)
        if approval.status != "pending":
            raise CerebellumError(f"approval {approval_id} is already {approval.status}")
        run = self.store.get_run(approval.run_id)
        engine = self.engine(mock=run.mock)
        await engine.decide(
            run.run_id, approval.step_id, approved=approved, by=by, comment=comment, resume=False
        )
        # If another process drives the run, this resume yields (LeaseUnavailable) and that
        # process applies the decision before it would suspend.
        self._spawn(engine.resume(run.run_id))
        return self.store.get_approval(approval_id)

    def sweep(self) -> list[str]:
        """Resume runs whose pending approval is overdue; resuming applies `on_timeout`."""
        now = self.clock.now()
        due = sorted(
            {
                approval.run_id
                for approval in self.store.list_approvals(status="pending")
                if approval.expires_at is not None and approval.expires_at <= now
            }
        )
        resumed: list[str] = []
        for run_id in due:
            try:
                self.resume(run_id)
            except CerebellumError:
                continue  # driven elsewhere (its owner applies the timeout) or finished meanwhile
            resumed.append(run_id)
        return resumed

    def start(self) -> None:
        if self.interval > 0 and self._sweeper is None:
            self._sweeper = asyncio.create_task(self._sweep_forever(), name="approval-sweeper")

    async def stop(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
            self._sweeper = None
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def drain(self) -> None:
        """Wait until all background work has finished."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                self.sweep()
            except Exception:  # the sweeper must outlive one bad pass
                log.exception("approval sweep failed")

    def _spawn(self, work: Awaitable[RunRecord]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None and not isinstance(exc, LeaseUnavailable):
            log.error("background run work failed: %s", exc, exc_info=exc)
