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

from cerebellum.errors import ApprovalExpired, CerebellumError, NotFound, RunNotFound
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
CREATE TABLE IF NOT EXISTS eval_runs (
    id              TEXT PRIMARY KEY,
    suite           TEXT NOT NULL,
    suite_path      TEXT NOT NULL,
    workflow_name   TEXT NOT NULL,
    workflow_digest TEXT NOT NULL,
    status          TEXT NOT NULL,
    mock            INTEGER NOT NULL,
    total           INTEGER NOT NULL,
    passed          INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    regressions     INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    ai_first_try    INTEGER NOT NULL DEFAULT 0,
    ai_first_ok     INTEGER NOT NULL DEFAULT 0,
    ai_repairs      INTEGER NOT NULL DEFAULT 0,
    baseline_id     TEXT,
    error           TEXT,
    created_at      REAL NOT NULL,
    ended_at        REAL
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_suite ON eval_runs(suite, created_at);
CREATE TABLE IF NOT EXISTS eval_results (
    eval_run_id     TEXT NOT NULL,
    case_id         TEXT NOT NULL,
    position        INTEGER NOT NULL,
    run_id          TEXT,
    passed          INTEGER NOT NULL,
    baseline_passed INTEGER,
    checks          TEXT NOT NULL,
    error           TEXT,
    cost_usd        REAL NOT NULL DEFAULT 0,
    duration_s      REAL,
    PRIMARY KEY (eval_run_id, case_id)
);
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


@dataclass(frozen=True)
class WorkflowSnapshot:
    digest: str
    name: str
    version: int
    source_yaml: str
    base_dir: str
    created_at: float
    runs: int
    last_run_at: float | None


@dataclass(frozen=True)
class EvalRunRecord:
    id: str
    suite: str
    suite_path: str
    workflow_name: str
    workflow_digest: str
    status: str  # running | completed | errored
    mock: bool
    total: int
    passed: int
    failed: int
    regressions: int
    cost_usd: float
    ai_first_try: int
    ai_first_ok: int
    ai_repairs: int
    baseline_id: str | None
    error: str | None
    created_at: float
    ended_at: float | None

    @property
    def pass_rate(self) -> float | None:
        done = self.passed + self.failed
        return self.passed / done if done else None


@dataclass(frozen=True)
class EvalResultRecord:
    eval_run_id: str
    case_id: str
    position: int
    run_id: str | None
    passed: bool
    baseline_passed: bool | None  # None: the baseline run had no such case (or there is none)
    checks: list[dict[str, Any]]
    error: str | None
    cost_usd: float
    duration_s: float | None

    @property
    def regression(self) -> bool:
        return self.baseline_passed is True and not self.passed


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


def _eval_run(row: sqlite3.Row) -> EvalRunRecord:
    data = dict(row)
    data["mock"] = bool(data["mock"])
    return EvalRunRecord(**data)


def _eval_result(row: sqlite3.Row) -> EvalResultRecord:
    data = dict(row)
    data["passed"] = bool(data["passed"])
    baseline = data["baseline_passed"]
    data["baseline_passed"] = None if baseline is None else bool(baseline)
    data["checks"] = json.loads(data["checks"])
    return EvalResultRecord(**data)


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
            raise NotFound(f"workflow snapshot {digest!r} not found")
        return rows[0]["source_yaml"], rows[0]["base_dir"]

    def list_workflows(self) -> list[WorkflowSnapshot]:
        """Every workflow snapshot runs were started from, most recently used first. Eval runs
        are left out: they neither count nor make an eval-only snapshot appear."""
        rows = self._rows(
            "SELECT w.digest, w.name, w.version, w.source_yaml, w.base_dir, w.created_at, "
            "COUNT(r.run_id) AS runs, MAX(r.created_at) AS last_run_at "
            "FROM workflows w LEFT JOIN runs r "
            "ON r.workflow_digest = w.digest AND r.eval_run_id IS NULL "
            "GROUP BY w.digest "
            "HAVING COUNT(r.run_id) > 0 "
            "OR NOT EXISTS (SELECT 1 FROM runs e WHERE e.workflow_digest = w.digest) "
            "ORDER BY COALESCE(last_run_at, w.created_at) DESC"
        )
        return [WorkflowSnapshot(**dict(row)) for row in rows]

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
                    run_id,
                    wf.digest,
                    wf.name,
                    RunStatus.RUNNING.value,
                    to_json(input),
                    to_json(params),
                    int(mock),
                    tx.now,
                    tx.now,
                    eval_run_id,
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
                    "workflow": wf.name,
                    "version": wf.version,
                    "digest": wf.digest,
                    "input": input,
                    "params": params,
                    "mock": mock,
                    "eval_run_id": eval_run_id,
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
        include_evals: bool = True,
    ) -> list[RunRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status=?")
            params.append(status.value)
        if eval_run_id is not None:
            clauses.append("eval_run_id=?")
            params.append(eval_run_id)
        if not include_evals:
            clauses.append("eval_run_id IS NULL")
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
            _apply_run_status(
                tx, run_id, status, event=event, output=output, error=error, data=data
            )
        return self.get_run(run_id)

    def suspend_run(self, run_id: str, owner: str, waiting: list[str]) -> bool:
        """Move a run to waiting_approval and give up `owner`'s lease in one transaction, unless an
        approval it waits for was decided meanwhile. Because deciding is also a write transaction,
        either the suspend sees the decision (returns False: keep driving) or the decider finds the
        lease free and can resume the run itself."""
        with self._tx() as tx:
            marks = ",".join("?" * len(waiting))
            decided = tx.execute(
                f"SELECT 1 FROM approvals WHERE run_id=? AND step_id IN ({marks}) "
                "AND status != 'pending'",
                (run_id, *waiting),
            ).fetchall()
            if decided:
                return False
            _apply_run_status(
                tx,
                run_id,
                RunStatus.WAITING_APPROVAL,
                event="suspended",
                data={"waiting": waiting},
            )
            tx.execute(
                "UPDATE runs SET lease_owner=NULL, lease_until=NULL "
                "WHERE run_id=? AND lease_owner=?",
                (run_id, owner),
            )
        return True

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
                tx,
                run_id,
                step_id,
                target,
                event=event,
                span_id=span_id,
                attempts=attempts,
                output=output,
                error=error,
                started_at=started_at,
                ended_at=ended_at,
                data=data,
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
                        approval_id,
                        run_id,
                        step_id,
                        "pending",
                        title,
                        to_json(context),
                        tx.now,
                        expires_at,
                        on_timeout,
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
                tx,
                run_id,
                step_id,
                StepStatus.WAITING,
                event="waiting",
                span_id=span_id,
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
        """Record a decision. One that arrives after the deadline loses to it: on_timeout is
        applied (as approval.expired, by system), and once that is committed ApprovalExpired tells
        the caller."""
        late = False
        with self._tx() as tx:
            rows = tx.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchall()
            if not rows:
                raise NotFound(f"approval {approval_id!r} not found")
            row = rows[0]
            if row["status"] != "pending":
                raise CerebellumError(f"approval {approval_id} is already {row['status']}")
            if not expired and row["expires_at"] is not None and row["expires_at"] <= tx.now:
                late = expired = True
                approved = row["on_timeout"] == "approve"
                by, comment = "system", "approval timed out"
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
                data={
                    "approval_id": approval_id,
                    "decision": status,
                    "by": by,
                    "comment": comment,
                },
            )
        if late:
            raise ApprovalExpired(
                f"approval {approval_id} expired before this decision; on_timeout "
                f"({row['on_timeout']}) was applied and it is now {status}"
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        rows = self._rows("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if not rows:
            raise NotFound(f"approval {approval_id!r} not found")
        return _approval(rows[0])

    def get_approval_for_step(self, run_id: str, step_id: str) -> ApprovalRecord | None:
        rows = self._rows("SELECT * FROM approvals WHERE run_id=? AND step_id=?", (run_id, step_id))
        return _approval(rows[0]) if rows else None

    def list_approvals(
        self, *, status: str | None = None, run_id: str | None = None
    ) -> list[ApprovalRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._rows(
            f"SELECT * FROM approvals {where} ORDER BY requested_at, id", tuple(params)
        )
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
                raise NotFound(f"task {task_id!r} not found")
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
            raise NotFound(f"task {task_id!r} not found")
        return _task(rows[0])

    def list_tasks(
        self, *, status: str | None = None, run_id: str | None = None
    ) -> list[TaskRecord]:
        clauses: list[str] = []
        params: list[Any] = []
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
                "UPDATE runs SET lease_owner=NULL, lease_until=NULL "
                "WHERE run_id=? AND lease_owner=?",
                (run_id, owner),
            )

    def is_stale(self, run: RunRecord) -> bool:
        """A run that claims to be running but has no live lease (its process died)."""
        if run.status is not RunStatus.RUNNING:
            return False
        return run.lease_until is None or run.lease_until < self.clock.now()

    # ── metrics ───────────────────────────────────────────────────────────────

    def metrics(self, since: float) -> dict[str, Any]:
        """Run outcomes since `since`; inbox sizes (pending approvals, open tasks) are current.
        Eval runs are test traffic and are left out."""
        runs = self._rows(
            "SELECT status, cost_usd, created_at, ended_at FROM runs "
            "WHERE created_at >= ? AND eval_run_id IS NULL",
            (since,),
        )
        by_status = {status.value: 0 for status in RunStatus}
        for row in runs:
            by_status[row["status"]] += 1
        finished = [row for row in runs if RunStatus(row["status"]) in RUN_TERMINAL]
        durations = [r["ended_at"] - r["created_at"] for r in finished if r["ended_at"] is not None]
        succeeded = by_status[RunStatus.SUCCEEDED.value]

        def count(sql: str, params: tuple[Any, ...] = ()) -> int:
            return int(self._rows(sql, params)[0][0])

        def events(kind: str) -> int:
            return count(
                "SELECT COUNT(*) FROM events e JOIN runs r ON r.run_id = e.run_id "
                "WHERE e.type=? AND e.ts >= ? AND r.eval_run_id IS NULL",
                (kind, since),
            )

        return {
            "since": since,
            "runs": len(runs),
            "by_status": by_status,
            "success_rate": succeeded / len(finished) if finished else None,
            "avg_duration_s": sum(durations) / len(durations) if durations else None,
            "cost_usd": sum(row["cost_usd"] for row in runs),
            "pending_approvals": count(
                "SELECT COUNT(*) FROM approvals a JOIN runs r ON r.run_id = a.run_id "
                "WHERE a.status='pending' AND r.eval_run_id IS NULL"
            ),
            "open_tasks": count(
                "SELECT COUNT(*) FROM tasks t JOIN runs r ON r.run_id = t.run_id "
                "WHERE t.status='open' AND r.eval_run_id IS NULL"
            ),
            "retries": events("step.retrying"),
            "fallbacks": events("step.recovered"),
        }

    # ── evals ─────────────────────────────────────────────────────────────────

    def create_eval_run(
        self,
        eval_run_id: str,
        *,
        suite: str,
        suite_path: str,
        workflow_name: str,
        workflow_digest: str,
        mock: bool,
        total: int,
        baseline_id: str | None,
    ) -> EvalRunRecord:
        with self._tx() as tx:
            tx.execute(
                "INSERT INTO eval_runs(id, suite, suite_path, workflow_name, workflow_digest, "
                "status, mock, total, baseline_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    eval_run_id,
                    suite,
                    suite_path,
                    workflow_name,
                    workflow_digest,
                    "running",
                    int(mock),
                    total,
                    baseline_id,
                    tx.now,
                ),
            )
        return self.get_eval_run(eval_run_id)

    def record_eval_result(
        self,
        eval_run_id: str,
        *,
        case_id: str,
        position: int,
        run_id: str | None,
        passed: bool,
        baseline_passed: bool | None,
        checks: list[dict[str, Any]],
        error: str | None,
        cost_usd: float,
        duration_s: float | None,
    ) -> EvalResultRecord:
        regression = baseline_passed is True and not passed
        with self._tx() as tx:
            tx.execute(
                "INSERT INTO eval_results(eval_run_id, case_id, position, run_id, passed, "
                "baseline_passed, checks, error, cost_usd, duration_s) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    eval_run_id,
                    case_id,
                    position,
                    run_id,
                    int(passed),
                    None if baseline_passed is None else int(baseline_passed),
                    to_json(checks),
                    error,
                    cost_usd,
                    duration_s,
                ),
            )
            tx.execute(
                "UPDATE eval_runs SET passed = passed + ?, failed = failed + ?, "
                "regressions = regressions + ?, cost_usd = cost_usd + ? WHERE id=?",
                (int(passed), int(not passed), int(regression), cost_usd, eval_run_id),
            )
        rows = self._rows(
            "SELECT * FROM eval_results WHERE eval_run_id=? AND case_id=?", (eval_run_id, case_id)
        )
        return _eval_result(rows[0])

    def finish_eval_run(
        self,
        eval_run_id: str,
        *,
        status: str,
        error: str | None = None,
        ai_first_try: int = 0,
        ai_first_ok: int = 0,
        ai_repairs: int = 0,
    ) -> EvalRunRecord:
        with self._tx() as tx:
            cursor = tx.execute(
                "UPDATE eval_runs SET status=?, error=?, ai_first_try=?, ai_first_ok=?, "
                "ai_repairs=?, ended_at=? WHERE id=?",
                (status, error, ai_first_try, ai_first_ok, ai_repairs, tx.now, eval_run_id),
            )
            if cursor.rowcount == 0:
                raise NotFound(f"eval run {eval_run_id!r} not found")
        return self.get_eval_run(eval_run_id)

    def get_eval_run(self, eval_run_id: str) -> EvalRunRecord:
        rows = self._rows("SELECT * FROM eval_runs WHERE id=?", (eval_run_id,))
        if not rows:
            raise NotFound(f"eval run {eval_run_id!r} not found")
        return _eval_run(rows[0])

    def list_eval_runs(self, *, suite: str | None = None, limit: int = 50) -> list[EvalRunRecord]:
        where, params = ("WHERE suite=?", (suite,)) if suite is not None else ("", ())
        rows = self._rows(
            f"SELECT * FROM eval_runs {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (*params, limit),
        )
        return [_eval_run(row) for row in rows]

    def latest_eval_run(self, suite: str) -> EvalRunRecord | None:
        """The most recent completed run of `suite`: the baseline the next run is compared with."""
        rows = self._rows(
            "SELECT * FROM eval_runs WHERE suite=? AND status='completed' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (suite,),
        )
        return _eval_run(rows[0]) if rows else None

    def get_eval_results(self, eval_run_id: str) -> list[EvalResultRecord]:
        rows = self._rows(
            "SELECT * FROM eval_results WHERE eval_run_id=? ORDER BY position", (eval_run_id,)
        )
        return [_eval_result(row) for row in rows]

    # ── events ────────────────────────────────────────────────────────────────

    def last_seq(self) -> int:
        return int(self._rows("SELECT COALESCE(MAX(seq), 0) FROM events")[0][0])

    def get_events(self, run_id: str, *, after_seq: int = 0) -> list[EventRecord]:
        rows = self._rows(
            "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after_seq)
        )
        return [_event(row) for row in rows]

    def events_since(self, seq: int, *, limit: int = 500) -> list[EventRecord]:
        rows = self._rows("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?", (seq, limit))
        return [_event(row) for row in rows]


def _apply_run_status(
    tx: _Tx,
    run_id: str,
    status: RunStatus,
    *,
    event: str,
    output: Any = UNSET,
    error: Any = UNSET,
    data: dict[str, Any] | None = None,
) -> None:
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
