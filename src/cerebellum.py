"""
cerebellum.py — Integrated runtime
Wires engine + state_machine + message_bus into one cohesive system.
"""

import asyncio, time, uuid
from typing import Any, Callable, Awaitable
from dataclasses import dataclass, field

import networkx as nx

from state_machine import (
    NodeState, S, CheckpointStore, RollbackManager, ResumeContext
)
from message_bus import MessageBus, AgentChannel, Message, MsgType
from _version import __version__, __version_info__

from engine import (
    NodeDef, FailPolicy, FailStrategy, NodeResult, NodeStatus,
    RunResult, BudgetExceeded, AgentFn
)


# ─── Integrated runner ────────────────────────────────────────────────────────

class Cerebellum:
    """
    Drop-in replacement for engine.Graph that adds:
      • persistent checkpoints (SQLite)
      • message bus (pub/sub between agents)
      • rollback/resume
      • real-time event stream for observability
    """

    def __init__(self, db_path: str = "cerebellum.db", audit: bool = True):
        self._nodes:  dict[str, NodeDef] = {}
        self._dag     = nx.DiGraph()
        self.store    = CheckpointStore(db_path)
        self.bus      = MessageBus(audit=audit)
        self._rollback= RollbackManager(self.store)

    # ── Builder API (same as engine.Graph) ───────────────────────────────────

    def node(
        self,
        name:        str,
        agent:       AgentFn,
        depends_on:  list[str]  = None,
        fail_policy: FailPolicy = None,
        description: str        = "",
    ) -> "Cerebellum":
        depends_on  = depends_on  or []
        fail_policy = fail_policy or FailPolicy()
        nd = NodeDef(name, agent, depends_on, fail_policy, description)
        self._nodes[name] = nd
        self._dag.add_node(name)
        for dep in depends_on:
            self._dag.add_edge(dep, name)
        if not nx.is_directed_acyclic_graph(self._dag):
            self._dag.remove_node(name)
            del self._nodes[name]
            raise ValueError(f"Adding '{name}' creates a cycle")
        return self

    def on_fail(self, name: str, *, retry=0, skip=False,
                fallback=None, timeout=30.0) -> "Cerebellum":
        nd = self._nodes[name]
        if skip:       strategy = FailStrategy.SKIP
        elif fallback: strategy = FailStrategy.FALLBACK
        elif retry:    strategy = FailStrategy.RETRY
        else:          strategy = FailStrategy.ABORT
        nd.fail_policy = FailPolicy(strategy, retry, fallback, timeout)
        return self

    # ── Run ───────────────────────────────────────────────────────────────────

    async def run(
        self,
        initial_input: dict = None,
        *,
        budget_usd: float | None = None,
        run_id:     str   | None = None,
        resume:     bool         = False,
    ) -> "CerebrumResult":
        run_id = run_id or str(uuid.uuid4())[:8]
        initial_input = initial_input or {}

        # persist the run
        graph_def = {"nodes": list(self._nodes.keys())}
        self.store.create_run(run_id, graph_def, initial_input)

        # optional: resume from last checkpoint
        ctx = None
        if resume:
            try:
                ctx = self._rollback.resume_latest(run_id)
            except ValueError:
                pass  # no checkpoint yet — fresh start

        runner = _IntegratedRunner(
            nodes         = self._nodes,
            dag           = self._dag,
            store         = self.store,
            bus           = self.bus,
            initial_input = ctx.initial_input if ctx else initial_input,
            budget_usd    = budget_usd,
            run_id        = run_id,
            resume_ctx    = ctx,
        )
        return await runner.execute()

    def rollback_to(self, run_id: str, seq: int) -> ResumeContext:
        return self._rollback.rollback_to(run_id, seq)


# ─── Integrated runner internals ─────────────────────────────────────────────

class _IntegratedRunner:

    def __init__(self, nodes, dag, store, bus, initial_input,
                 budget_usd, run_id, resume_ctx):
        self.nodes         = nodes
        self.dag           = dag
        self.store         = store
        self.bus           = bus
        self.initial_input = initial_input
        self.budget_usd    = budget_usd
        self.run_id        = run_id
        self.resume_ctx    = resume_ctx

        # initialise state from checkpoint or fresh
        if resume_ctx:
            self.node_states = resume_ctx.node_states
            self.completed   = resume_ctx.completed
            self.total_cost  = resume_ctx.total_cost
        else:
            self.node_states = {n: NodeState(n) for n in nodes}
            self.completed:  set[str] = set()
            self.total_cost: float    = 0.0

        self.failed:    set[str] = set()
        self.cancelled: set[str] = set()
        self._lock = asyncio.Lock()

        # per-node channels for message bus
        self._channels: dict[str, AgentChannel] = {
            n: AgentChannel(n, bus) for n in nodes
        }
        self._channels["scheduler"] = AgentChannel("scheduler", bus)

    async def execute(self) -> "CerebrumResult":
        dag      = self.dag
        order    = list(nx.topological_sort(dag))
        in_deg   = {n: dag.in_degree(n) for n in dag}
        ready_q: asyncio.Queue[str] = asyncio.Queue()

        for name in order:
            if name in self.completed:
                pass  # skip — already done (resume)
            elif all(p in self.completed for p in dag.predecessors(name)):
                # seed: roots AND nodes whose all parents are already complete
                await ready_q.put(name)

        running: dict[str, asyncio.Task] = {}
        started = time.time()

        while True:
            while not ready_q.empty():
                name = await ready_q.get()
                if name in self.cancelled or name in self.completed:
                    continue
                task = asyncio.create_task(
                    self._run_node(name), name=f"cb:{name}"
                )
                running[name] = task

            if not running:
                break

            done, _ = await asyncio.wait(
                running.values(), return_when=asyncio.FIRST_COMPLETED
            )

            for task in done:
                name = task.get_name().replace("cb:", "")
                running.pop(name, None)
                ns = self.node_states[name]

                if ns.status == S.SUCCESS:
                    self.completed.add(name)
                    self.store.save_checkpoint(
                        self.run_id, self.node_states,
                        self.completed, self.total_cost
                    )
                    await self._channels["scheduler"].emit(
                        "node.completed",
                        {"node": name, "cost": ns.cost_usd,
                         "duration": ns.duration}
                    )
                    for child in dag.successors(name):
                        parents = list(dag.predecessors(child))
                        if all(p in self.completed or p in self.failed
                               for p in parents):
                            if child not in self.cancelled:
                                await ready_q.put(child)

                elif ns.status in (S.FAILED, S.SKIPPED, S.CANCELLED):
                    self.failed.add(name)
                    self.store.save_checkpoint(
                        self.run_id, self.node_states,
                        self.completed, self.total_cost
                    )
                    await self._channels["scheduler"].emit(
                        "node.failed",
                        {"node": name, "error": ns.error,
                         "status": ns.status.value}
                    )
                    nd = self.nodes[name]
                    if nd.fail_policy.strategy == FailStrategy.ABORT:
                        await self._cancel_downstream(name)
                    else:
                        for child in dag.successors(name):
                            parents = list(dag.predecessors(child))
                            if all(p in self.completed or p in self.failed
                                   for p in parents):
                                if child not in self.cancelled:
                                    await ready_q.put(child)

        elapsed = time.time() - started
        status  = "success" if not self.failed else "partial"
        self.store.mark_run_done(self.run_id, status)

        return CerebrumResult(
            run_id      = self.run_id,
            node_states = self.node_states,
            completed   = self.completed,
            failed      = self.failed,
            total_cost  = self.total_cost,
            elapsed     = elapsed,
            audit_log   = self.bus.audit_log,
        )

    async def _run_node(self, name: str) -> None:
        nd     = self.nodes[name]
        policy = nd.fail_policy
        ns     = self.node_states[name]
        ch     = self._channels[name]
        max_a  = policy.max_retry + 1

        inputs = {**self.initial_input}
        for dep in nd.depends_on:
            dep_ns = self.node_states.get(dep)
            if dep_ns and dep_ns.output is not None:
                inputs[dep] = dep_ns.output

        ns.transition(S.RUNNING)
        ns.started_at = time.time()
        await ch.emit("node.started", {"node": name, "inputs": list(inputs.keys())})
        self._log(f"▶ {name}")

        for attempt in range(1, max_a + 1):
            try:
                output = await asyncio.wait_for(
                    nd.agent(inputs), timeout=policy.timeout
                )
                cost = 0.0
                if isinstance(output, tuple) and len(output) == 2:
                    output, cost = output

                async with self._lock:
                    self.total_cost += cost
                    if self.budget_usd and self.total_cost > self.budget_usd:
                        raise BudgetExceeded(f"Budget ${self.budget_usd:.2f} exceeded")

                ns.output    = output
                ns.cost_usd  = cost
                ns.attempts  = attempt
                ns.ended_at  = time.time()
                ns.transition(S.SUCCESS)
                await ch.emit("node.success",
                              {"node": name, "cost": cost,
                               "duration": ns.duration})
                self._log(f"✓ {name} ({ns.duration:.2f}s, ${cost:.4f})")
                return

            except BudgetExceeded as e:
                ns.error   = str(e)
                ns.ended_at= time.time()
                ns.transition(S.FAILED)
                await self._cancel_downstream(name)
                return

            except Exception as e:
                ns.error   = str(e)
                ns.attempts= attempt
                if attempt < max_a:
                    ns.transition(S.TIMED_OUT if isinstance(e, asyncio.TimeoutError)
                                  else S.FAILED)
                    ns.transition(S.RETRYING)
                    wait = 2 ** (attempt - 1)
                    self._log(f"↻ {name} retry {attempt}/{policy.max_retry} (wait {wait}s)")
                    await ch.emit("node.retrying",
                                  {"node": name, "attempt": attempt, "error": str(e)})
                    await asyncio.sleep(wait)
                    ns.transition(S.RUNNING)
                else:
                    ns.ended_at = time.time()
                    if policy.strategy == FailStrategy.SKIP:
                        ns.transition(S.FAILED)
                        ns.transition(S.SKIPPED)
                        self._log(f"⊘ {name} skipped")
                    else:
                        ns.transition(S.FAILED)
                        self._log(f"✗ {name}: {e}")

    async def _cancel_downstream(self, failed: str) -> None:
        for node in nx.descendants(self.dag, failed):
            if node not in self.completed:
                self.cancelled.add(node)
                ns = self.node_states[node]
                if not ns.is_terminal:
                    ns.transition(S.CANCELLED)
                self._log(f"⊗ {node} cancelled")

    def _log(self, msg: str) -> None:
        print(f"  [cerebellum:{self.run_id}] {msg}")


# ─── Result ───────────────────────────────────────────────────────────────────

@dataclass
class CerebrumResult:
    run_id:      str
    node_states: dict[str, NodeState]
    completed:   set[str]
    failed:      set[str]
    total_cost:  float
    elapsed:     float
    audit_log:   list[dict]

    @property
    def success(self) -> bool:
        return len(self.failed) == 0

    def output_of(self, node: str) -> Any:
        ns = self.node_states.get(node)
        return ns.output if ns else None

    def summary(self) -> str:
        icon = {"success":"✓","failed":"✗","skipped":"⊘",
                "cancelled":"⊗","running":"▶","pending":"·",
                "retrying":"↻","timed_out":"T"}
        lines = [
            f"\n{'─'*50}",
            f"  Run {self.run_id}  {'OK' if self.success else 'PARTIAL'}  "
            f"{self.elapsed:.2f}s  ${self.total_cost:.4f} total",
            f"{'─'*50}",
        ]
        for name, ns in self.node_states.items():
            i = icon.get(ns.status.value, "?")
            lines.append(
                f"  {i} {name:<20} {ns.status.value:<10} "
                f"{ns.duration:.2f}s  ${ns.cost_usd:.4f}"
            )
        lines.append(f"{'─'*50}")
        return "\n".join(lines)