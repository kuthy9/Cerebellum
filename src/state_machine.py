"""
Cerebellum — State Machine + Rollback
Three layers:
  1. NodeStateMachine  — valid transitions, guard conditions
  2. CheckpointStore   — SQLite-backed snapshots after every node completion
  3. RollbackManager   — resume a run from any checkpoint
"""

import asyncio
import json
import pickle
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


# ─── 1. NODE STATE MACHINE ────────────────────────────────────────────────────
#
#  Valid transitions (only these are allowed — anything else raises):
#
#   PENDING ──► RUNNING ──► SUCCESS
#                    │
#                    ├──► FAILED ──► RETRYING ──► RUNNING  (loop back)
#                    │                  │
#                    │                  └──► FAILED  (exhausted)
#                    │
#                    ├──► SKIPPED   (policy: skip)
#                    ├──► CANCELLED (upstream abort)
#                    └──► TIMED_OUT ──► RETRYING or FAILED

class S(Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    SUCCESS   = "success"
    FAILED    = "failed"
    RETRYING  = "retrying"
    SKIPPED   = "skipped"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


# Adjacency list of legal transitions
_TRANSITIONS: dict[S, set[S]] = {
    S.PENDING:   {S.RUNNING, S.CANCELLED},
    S.RUNNING:   {S.SUCCESS, S.FAILED, S.TIMED_OUT, S.CANCELLED},
    S.FAILED:    {S.RETRYING, S.SKIPPED, S.CANCELLED},
    S.TIMED_OUT: {S.RETRYING, S.FAILED,  S.CANCELLED},
    S.RETRYING:  {S.RUNNING},
    S.SUCCESS:   set(),       # terminal
    S.SKIPPED:   set(),       # terminal
    S.CANCELLED: set(),       # terminal
}

_TERMINAL = {S.SUCCESS, S.SKIPPED, S.CANCELLED}


class InvalidTransition(Exception):
    pass


@dataclass
class NodeState:
    node_name:  str
    status:     S         = S.PENDING
    attempts:   int       = 0
    output:     Any       = None
    error:      str       = ""        # str so it's serialisable
    started_at: float     = 0.0
    ended_at:   float     = 0.0
    cost_usd:   float     = 0.0
    history:    list[tuple[S, float]] = field(default_factory=list)

    def transition(self, to: S) -> None:
        allowed = _TRANSITIONS.get(self.status, set())
        if to not in allowed:
            raise InvalidTransition(
                f"{self.node_name}: {self.status.value} → {to.value} is not allowed. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        self.history.append((self.status, time.time()))
        self.status = to

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def duration(self) -> float:
        if self.ended_at and self.started_at:
            return self.ended_at - self.started_at
        return 0.0

    def to_dict(self) -> dict:
        return {
            "node_name":  self.node_name,
            "status":     self.status.value,
            "attempts":   self.attempts,
            "output":     self.output,
            "error":      self.error,
            "started_at": self.started_at,
            "ended_at":   self.ended_at,
            "cost_usd":   self.cost_usd,
            "history":    [(s.value, t) for s, t in self.history],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NodeState":
        ns = cls(node_name=d["node_name"], status=S(d["status"]))
        ns.attempts   = d["attempts"]
        ns.output     = d["output"]
        ns.error      = d["error"]
        ns.started_at = d["started_at"]
        ns.ended_at   = d["ended_at"]
        ns.cost_usd   = d["cost_usd"]
        ns.history    = [(S(s), t) for s, t in d["history"]]
        return ns


# ─── 2. CHECKPOINT STORE ─────────────────────────────────────────────────────
#
#  Schema (SQLite — no external dependencies):
#
#   runs(run_id, graph_def_json, created_at, status)
#   checkpoints(id, run_id, seq, timestamp, node_states_json, completed_json)
#
#  Every time a node reaches a terminal state, a new checkpoint row is written.
#  The store keeps ALL checkpoints so you can roll back to any moment.

class CheckpointStore:

    def __init__(self, db_path: str = "cerebellum_state.db"):
        self.db_path = db_path
        self._conn   = sqlite3.connect(db_path, check_same_thread=False)
        self._lock   = asyncio.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id       TEXT PRIMARY KEY,
                graph_def    TEXT,
                initial_input TEXT,
                created_at   REAL,
                status       TEXT DEFAULT 'running'
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT,
                seq          INTEGER,
                timestamp    REAL,
                node_states  TEXT,
                completed    TEXT,
                total_cost   REAL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_cp_run ON checkpoints(run_id, seq);
        """)
        self._conn.commit()

    # ── Write ────────────────────────────────────────────────────────────────

    def create_run(
        self,
        run_id:        str,
        graph_def:     dict,
        initial_input: dict,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)",
            (run_id, json.dumps(graph_def), json.dumps(initial_input),
             time.time(), "running"),
        )
        self._conn.commit()

    def save_checkpoint(
        self,
        run_id:      str,
        node_states: dict[str, NodeState],
        completed:   set[str],
        total_cost:  float,
    ) -> int:
        seq = self._next_seq(run_id)
        states_json    = json.dumps({k: v.to_dict() for k, v in node_states.items()})
        completed_json = json.dumps(sorted(completed))
        self._conn.execute(
            "INSERT INTO checkpoints(run_id,seq,timestamp,node_states,completed,total_cost) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, seq, time.time(), states_json, completed_json, total_cost),
        )
        self._conn.commit()
        return seq

    def mark_run_done(self, run_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE runs SET status=? WHERE run_id=?", (status, run_id)
        )
        self._conn.commit()

    # ── Read ─────────────────────────────────────────────────────────────────

    def load_latest_checkpoint(
        self, run_id: str
    ) -> tuple[dict[str, NodeState], set[str], float] | None:
        row = self._conn.execute(
            "SELECT node_states, completed, total_cost "
            "FROM checkpoints WHERE run_id=? ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        return self._parse_checkpoint(row)

    def load_checkpoint_at(
        self, run_id: str, seq: int
    ) -> tuple[dict[str, NodeState], set[str], float] | None:
        row = self._conn.execute(
            "SELECT node_states, completed, total_cost "
            "FROM checkpoints WHERE run_id=? AND seq=?",
            (run_id, seq),
        ).fetchone()
        if not row:
            return None
        return self._parse_checkpoint(row)

    def list_checkpoints(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT seq, timestamp, completed, total_cost "
            "FROM checkpoints WHERE run_id=? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [
            {
                "seq":        r[0],
                "timestamp":  r[1],
                "completed":  json.loads(r[2]),
                "total_cost": r[3],
            }
            for r in rows
        ]

    def list_runs(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT run_id, created_at, status FROM runs ORDER BY created_at DESC"
        ).fetchall()
        return [{"run_id": r[0], "created_at": r[1], "status": r[2]} for r in rows]

    def get_initial_input(self, run_id: str) -> dict:
        row = self._conn.execute(
            "SELECT initial_input FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    # ── Internal ─────────────────────────────────────────────────────────────

    def _next_seq(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM checkpoints WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return row[0]

    def _parse_checkpoint(
        self, row: tuple
    ) -> tuple[dict[str, NodeState], set[str], float]:
        states   = {k: NodeState.from_dict(v) for k, v in json.loads(row[0]).items()}
        completed= set(json.loads(row[1]))
        cost     = row[2]
        return states, completed, cost

    def close(self) -> None:
        self._conn.close()


# ─── 3. ROLLBACK MANAGER ─────────────────────────────────────────────────────
#
#  Given a run_id and an optional target_seq, reconstructs the execution
#  context so the DAG runner can resume without re-running completed nodes.
#
#  Rollback strategy:
#    • nodes in `completed` at the checkpoint → stay SUCCESS, outputs preserved
#    • nodes NOT in `completed`               → reset to PENDING
#    • failed/cancelled nodes                 → reset to PENDING (will retry)

@dataclass
class ResumeContext:
    run_id:        str
    node_states:   dict[str, NodeState]
    completed:     set[str]
    total_cost:    float
    initial_input: dict
    resumed_from:  int          # checkpoint seq we restored from


class RollbackManager:

    def __init__(self, store: CheckpointStore):
        self.store = store

    def resume_latest(self, run_id: str) -> ResumeContext:
        cp = self.store.load_latest_checkpoint(run_id)
        if not cp:
            raise ValueError(f"No checkpoints found for run {run_id!r}")
        return self._build_context(run_id, cp)

    def rollback_to(self, run_id: str, seq: int) -> ResumeContext:
        """Roll back to a specific checkpoint sequence number."""
        cp = self.store.load_checkpoint_at(run_id, seq)
        if not cp:
            raise ValueError(f"Checkpoint seq={seq} not found for run {run_id!r}")
        print(f"[rollback] Restoring run {run_id} to checkpoint seq={seq}")
        return self._build_context(run_id, cp, seq=seq)

    def _build_context(
        self,
        run_id: str,
        cp: tuple[dict[str, NodeState], set[str], float],
        seq: int = -1,
    ) -> ResumeContext:
        node_states, completed, total_cost = cp

        # Reset any non-completed nodes back to PENDING
        for name, ns in node_states.items():
            if name not in completed:
                print(f"[rollback]   reset {name!r}: {ns.status.value} → pending")
                ns.status    = S.PENDING
                ns.error     = ""
                ns.ended_at  = 0.0
                ns.attempts  = 0
                ns.history   = []

        if seq == -1:
            cps = self.store.list_checkpoints(run_id)
            seq = cps[-1]["seq"] if cps else 0

        print(f"[rollback] Resuming from checkpoint {seq}: "
              f"completed={sorted(completed)}, preserved cost=${total_cost:.4f}")

        return ResumeContext(
            run_id        = run_id,
            node_states   = node_states,
            completed     = completed,
            total_cost    = total_cost,
            initial_input = self.store.get_initial_input(run_id),
            resumed_from  = seq,
        )