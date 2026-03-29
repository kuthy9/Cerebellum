"""
Cerebellum — DAG Execution Engine
Core: deterministic scheduling, parallel execution, rollback
No LLM calls in the coordination layer.
"""

import asyncio
import time
import uuid
from enum import Enum
from typing import Any, Callable, Awaitable
from dataclasses import dataclass, field
from collections import defaultdict

import networkx as nx


# ─── Types ────────────────────────────────────────────────────────────────────

class NodeStatus(Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    SUCCESS   = "success"
    FAILED    = "failed"
    SKIPPED   = "skipped"
    CANCELLED = "cancelled"


class FailStrategy(Enum):
    RETRY    = "retry"
    SKIP     = "skip"       # mark as skipped, let downstream decide
    FALLBACK = "fallback"   # run a named fallback node instead
    ABORT    = "abort"      # cancel all downstream nodes


@dataclass
class FailPolicy:
    strategy:  FailStrategy = FailStrategy.ABORT
    max_retry: int          = 0
    fallback:  str | None   = None   # node name to run instead
    timeout:   float        = 30.0   # seconds per attempt


@dataclass
class NodeResult:
    node_name:  str
    status:     NodeStatus
    output:     Any              = None
    error:      Exception | None = None
    attempts:   int              = 0
    started_at: float            = 0.0
    ended_at:   float            = 0.0
    cost_usd:   float            = 0.0  # agent can report token cost

    @property
    def duration(self) -> float:
        return self.ended_at - self.started_at


@dataclass
class Checkpoint:
    """Snapshot of the graph state at a point in time — for rollback."""
    run_id:    str
    timestamp: float
    results:   dict[str, NodeResult]   # node_name → result so far
    completed: set[str]                # set of finished node names


# ─── Node definition ──────────────────────────────────────────────────────────

AgentFn = Callable[[dict[str, Any]], Awaitable[Any]]
"""
An agent is just an async function:
  async def my_agent(inputs: dict) -> Any
inputs keys are the names of upstream nodes whose outputs feed into this one.
"""

@dataclass
class NodeDef:
    name:        str
    agent:       AgentFn
    depends_on:  list[str]   = field(default_factory=list)
    fail_policy: FailPolicy   = field(default_factory=FailPolicy)
    description: str          = ""


# ─── The Graph ────────────────────────────────────────────────────────────────

class Graph:
    """
    Build a DAG of agent nodes, then call .run() to execute.

    Example
    -------
    g = Graph()
    g.node("search",  agent=search_fn)
    g.node("analyze", agent=analyze_fn, depends_on=["search"])
    g.node("write_a", agent=write_fn,   depends_on=["analyze"])
    g.node("write_b", agent=write_fn,   depends_on=["analyze"])
    g.node("merge",   agent=merge_fn,   depends_on=["write_a", "write_b"])

    result = await g.run({"query": "topic X"})
    """

    def __init__(self):
        self._nodes: dict[str, NodeDef] = {}
        self._dag   = nx.DiGraph()

    # ── Builder API ──────────────────────────────────────────────────────────

    def node(
        self,
        name:        str,
        agent:       AgentFn,
        depends_on:  list[str]   = None,
        fail_policy: FailPolicy  = None,
        description: str         = "",
    ) -> "Graph":
        depends_on  = depends_on  or []
        fail_policy = fail_policy or FailPolicy()

        node_def = NodeDef(
            name        = name,
            agent       = agent,
            depends_on  = depends_on,
            fail_policy = fail_policy,
            description = description,
        )
        self._nodes[name] = node_def
        self._dag.add_node(name)
        for dep in depends_on:
            self._dag.add_edge(dep, name)

        if not nx.is_directed_acyclic_graph(self._dag):
            self._dag.remove_node(name)
            del self._nodes[name]
            raise ValueError(f"Adding '{name}' creates a cycle in the DAG")

        return self  # fluent

    def on_fail(
        self,
        node_name:   str,
        *,
        retry:       int  = 0,
        skip:        bool = False,
        fallback:    str  = None,
        timeout:     float = 30.0,
    ) -> "Graph":
        """Shorthand for setting a fail policy after node() is called."""
        node = self._nodes[node_name]
        if skip:
            strategy = FailStrategy.SKIP
        elif fallback:
            strategy = FailStrategy.FALLBACK
        elif retry:
            strategy = FailStrategy.RETRY
        else:
            strategy = FailStrategy.ABORT
        node.fail_policy = FailPolicy(
            strategy  = strategy,
            max_retry = retry,
            fallback  = fallback,
            timeout   = timeout,
        )
        return self

    # ── Execution ─────────────────────────────────────────────────────────────

    async def run(
        self,
        initial_input: dict[str, Any] = None,
        *,
        budget_usd:    float | None   = None,   # abort if total cost exceeds
        run_id:        str | None      = None,
    ) -> "RunResult":
        run_id = run_id or str(uuid.uuid4())[:8]
        runner = _Runner(
            graph        = self,
            initial_input= initial_input or {},
            budget_usd   = budget_usd,
            run_id       = run_id,
        )
        return await runner.execute()


# ─── Runner (internal) ────────────────────────────────────────────────────────

class _Runner:

    def __init__(
        self,
        graph:         Graph,
        initial_input: dict[str, Any],
        budget_usd:    float | None,
        run_id:        str,
    ):
        self.graph          = graph
        self.initial_input  = initial_input
        self.budget_usd     = budget_usd
        self.run_id         = run_id

        self.results:   dict[str, NodeResult] = {}
        self.completed: set[str]              = set()
        self.failed:    set[str]              = set()
        self.cancelled: set[str]              = set()
        self.checkpoints: list[Checkpoint]    = []
        self.total_cost:  float               = 0.0
        self._lock = asyncio.Lock()

    async def execute(self) -> "RunResult":
        dag   = self.graph._dag
        order = list(nx.topological_sort(dag))          # deterministic order
        in_degree = {n: dag.in_degree(n) for n in dag}
        ready_q: asyncio.Queue[str] = asyncio.Queue()

        # seed: nodes with no dependencies
        for name in order:
            if in_degree[name] == 0:
                await ready_q.put(name)

        running: dict[str, asyncio.Task] = {}

        while True:
            # drain the ready queue, launch all ready nodes in parallel
            while not ready_q.empty():
                name = await ready_q.get()
                if name in self.cancelled:
                    continue
                task = asyncio.create_task(
                    self._run_node(name),
                    name=f"cerebellum:{name}",
                )
                running[name] = task

            if not running:
                break   # nothing left to do

            # wait for ANY task to finish (parallel execution)
            done, _ = await asyncio.wait(
                running.values(),
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in done:
                name = task.get_name().replace("cerebellum:", "")
                running.pop(name, None)
                result = self.results[name]

                if result.status == NodeStatus.SUCCESS:
                    self.completed.add(name)
                    self._save_checkpoint()

                    # unlock downstream nodes
                    for child in dag.successors(name):
                        parents = list(dag.predecessors(child))
                        if all(
                            p in self.completed or p in self.failed
                            for p in parents
                        ):
                            if child not in self.cancelled:
                                await ready_q.put(child)

                elif result.status in (NodeStatus.FAILED, NodeStatus.SKIPPED):
                    self.failed.add(name)
                    self._save_checkpoint()

                    # handle downstream based on strategy
                    node_def = self.graph._nodes[name]
                    if node_def.fail_policy.strategy == FailStrategy.ABORT:
                        await self._cancel_downstream(name)
                    else:
                        # SKIP or FALLBACK: still propagate to children
                        for child in dag.successors(name):
                            parents = list(dag.predecessors(child))
                            if all(
                                p in self.completed or p in self.failed
                                for p in parents
                            ):
                                if child not in self.cancelled:
                                    await ready_q.put(child)

        return RunResult(
            run_id      = self.run_id,
            results     = self.results,
            checkpoints = self.checkpoints,
            total_cost  = self.total_cost,
        )

    async def _run_node(self, name: str) -> None:
        node_def    = self.graph._nodes[name]
        policy      = node_def.fail_policy
        attempts    = 0
        max_attempts= policy.max_retry + 1

        # build inputs from upstream results + initial input
        inputs = {**self.initial_input}
        for dep in node_def.depends_on:
            dep_result = self.results.get(dep)
            if dep_result and dep_result.output is not None:
                inputs[dep] = dep_result.output

        result = NodeResult(
            node_name  = name,
            status     = NodeStatus.RUNNING,
            started_at = time.time(),
        )
        self.results[name] = result
        self._log(f"▶ {name}")

        while attempts < max_attempts:
            attempts += 1
            try:
                output = await asyncio.wait_for(
                    node_def.agent(inputs),
                    timeout=policy.timeout,
                )
                # agent may return (output, cost_usd) tuple
                cost = 0.0
                if isinstance(output, tuple) and len(output) == 2:
                    output, cost = output

                async with self._lock:
                    self.total_cost += cost
                    if self.budget_usd and self.total_cost > self.budget_usd:
                        raise BudgetExceeded(
                            f"Budget ${self.budget_usd:.2f} exceeded "
                            f"(spent ${self.total_cost:.4f})"
                        )

                result.output   = output
                result.cost_usd = cost
                result.status   = NodeStatus.SUCCESS
                result.attempts = attempts
                result.ended_at = time.time()
                self._log(f"✓ {name} ({result.duration:.2f}s, ${cost:.4f})")
                return

            except BudgetExceeded as e:
                result.status   = NodeStatus.FAILED
                result.error    = e
                result.ended_at = time.time()
                self._log(f"✗ {name} — budget exceeded")
                await self._cancel_downstream(name)
                return

            except Exception as e:
                result.error    = e
                result.attempts = attempts
                if attempts < max_attempts:
                    wait = 2 ** (attempts - 1)   # exponential backoff
                    self._log(f"↻ {name} retry {attempts}/{policy.max_retry} (wait {wait}s)")
                    await asyncio.sleep(wait)
                else:
                    result.ended_at = time.time()
                    # apply final fail strategy
                    if policy.strategy == FailStrategy.SKIP:
                        result.status = NodeStatus.SKIPPED
                        self._log(f"⊘ {name} — skipped after {attempts} attempts")
                    elif policy.strategy == FailStrategy.FALLBACK and policy.fallback:
                        result.status = NodeStatus.SKIPPED
                        self._log(f"⤳ {name} → fallback '{policy.fallback}'")
                        # inject fallback node as if it were already in the graph
                        fallback_def = self.graph._nodes.get(policy.fallback)
                        if fallback_def:
                            await self._run_node(policy.fallback)
                    else:
                        result.status = NodeStatus.FAILED
                        self._log(f"✗ {name} — failed: {e}")

    async def _cancel_downstream(self, failed_node: str) -> None:
        dag = self.graph._dag
        descendants = nx.descendants(dag, failed_node)
        for node in descendants:
            if node not in self.completed:
                self.cancelled.add(node)
                self.results[node] = NodeResult(
                    node_name = node,
                    status    = NodeStatus.CANCELLED,
                )
                self._log(f"⊗ {node} — cancelled (upstream failure)")

    def _save_checkpoint(self) -> None:
        cp = Checkpoint(
            run_id    = self.run_id,
            timestamp = time.time(),
            results   = dict(self.results),
            completed = set(self.completed),
        )
        self.checkpoints.append(cp)

    def _log(self, msg: str) -> None:
        print(f"[cerebellum:{self.run_id}] {msg}")


# ─── RunResult ────────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    run_id:      str
    results:     dict[str, NodeResult]
    checkpoints: list[Checkpoint]
    total_cost:  float

    @property
    def success(self) -> bool:
        return all(
            r.status in (NodeStatus.SUCCESS, NodeStatus.SKIPPED)
            for r in self.results.values()
        )

    def output_of(self, node_name: str) -> Any:
        r = self.results.get(node_name)
        return r.output if r else None

    def summary(self) -> str:
        lines = [f"Run {self.run_id} — {'OK' if self.success else 'FAILED'}",
                 f"Total cost: ${self.total_cost:.4f}"]
        for name, r in self.results.items():
            icon = {"success":"✓","failed":"✗","skipped":"⊘",
                    "cancelled":"⊗","running":"▶","pending":"·"
                    }.get(r.status.value, "?")
            lines.append(f"  {icon} {name:<18} {r.status.value:<10} "
                         f"{r.duration:.2f}s  ${r.cost_usd:.4f}")
        return "\n".join(lines)


class BudgetExceeded(Exception):
    pass