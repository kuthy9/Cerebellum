<div align="center">

```
  ██████╗███████╗██████╗ ███████╗██████╗ ███████╗██╗     ██╗     ██╗   ██╗███╗   ███╗
 ██╔════╝██╔════╝██╔══██╗██╔════╝██╔══██╗██╔════╝██║     ██║     ██║   ██║████╗ ████║
 ██║     █████╗  ██████╔╝█████╗  ██████╔╝█████╗  ██║     ██║     ██║   ██║██╔████╔██║
 ██║     ██╔══╝  ██╔══██╗██╔══╝  ██╔══██╗██╔══╝  ██║     ██║     ██║   ██║██║╚██╔╝██║
 ╚██████╗███████╗██║  ██║███████╗██████╔╝███████╗███████╗███████╗╚██████╔╝██║ ╚═╝ ██║
  ╚═════╝╚══════╝╚═╝  ╚═╝╚══════╝╚═════╝ ╚══════╝╚══════╝╚══════╝ ╚═════╝ ╚═╝     ╚═╝
```

**LLM handles content. Cerebellum handles coordination.**

[![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Version](https://img.shields.io/badge/version-0.1.0-purple?style=flat-square)](CHANGELOG.md)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-1-brightgreen?style=flat-square)](requirements.txt)
[![Stars](https://img.shields.io/github/stars/your-org/cerebellum?style=flat-square&color=yellow)](https://github.com/your-org/cerebellum)

</div>

---

Every multi-agent system eventually hits the same wall.

Your agents are smart. They reason, they plan, they write. But the moment you chain more than three of them together — things fall apart. One timeout cascades into five. A $40 debugging run re-executes nodes that already succeeded. Agent B calls Agent C which is still waiting on Agent A, and nobody told the LLM to care about sequencing. You spend more time managing coordination than building intelligence.

**Cerebellum is the coordination layer you've been patching together by hand.**

It's the part of the brain that doesn't think — it just makes sure everything fires in the right order, at the right time, without wasting a single token.

<br>

## What it does

```python
from cerebellum import Cerebellum

c = Cerebellum("my_run.db")

c.node("search",   agent=search_fn)
c.node("analyze",  agent=analyze_fn,  depends_on=["search"])
c.node("write_a",  agent=writer_fn,   depends_on=["analyze"])
c.node("write_b",  agent=writer_fn,   depends_on=["analyze"])   # ← runs in parallel
c.node("merge",    agent=merge_fn,    depends_on=["write_a", "write_b"])

c.on_fail("search", retry=3, timeout=10.0)
c.on_fail("write_b", skip=True)        # optional — pipeline continues without it

result = await c.run({"query": "topic"}, budget_usd=0.10)
```

`write_a` and `write_b` run in parallel. If the live data feed fails, it falls back to cache. If the run crashes at node 7 of 20, `resume=True` picks up from exactly where it left off — no re-running, no wasted cost.

None of this requires an LLM call. The coordination is deterministic code.

<br>

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       your agents                           │
│          async functions that call LLMs, APIs, tools        │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                      cerebellum.py                          │
│                   integrated runtime                        │
├──────────────┬──────────────────────┬───────────────────────┤
│  engine.py   │   state_machine.py   │   message_bus.py      │
│              │                      │                       │
│  DAG         │   8-state FSM        │   typed pub/sub       │
│  execution   │   SQLite             │   request / reply     │
│  parallel    │   checkpoints        │   dead-letter queue   │
│  scheduling  │   rollback /         │   full audit log      │
│  budget cap  │   resume             │                       │
└──────────────┴──────────────────────┴───────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                       SQLite                                │
│          run history · checkpoints · audit log              │
└─────────────────────────────────────────────────────────────┘
```

Three modules. One file per concern. No magic.

<br>

## Features

**Parallel execution by default**
Independent branches run concurrently via `asyncio`. The scheduler uses topological sort — not an LLM — to determine what can run in parallel. A five-branch fan-out runs in the time of one branch.

**Deterministic coordination**
Scheduling, ordering, error routing, and state transitions are all handled by code with predictable outcomes. The LLM is called exactly once per agent, for content only.

**Persistent checkpoints**
Every node that reaches a terminal state writes a snapshot to SQLite. If the process dies, the next run picks up from the last clean checkpoint. Completed nodes are never re-executed.

**Rollback to any point**
Not just "resume latest" — you can roll back to any historical checkpoint by sequence number. Every checkpoint is an immutable record of system state at that moment.

**Hard budget cap**
Set `budget_usd=0.05` and Cerebellum tracks cumulative token cost across all agents. The moment the limit is breached, downstream nodes are cancelled. No runaway spend.

**Typed message bus**
Agents communicate through a central bus using typed envelopes (`EVENT`, `REQUEST`, `REPLY`, `COMMAND`, `ERROR`). Topics support wildcards. Every message is logged. Nothing disappears silently.

**Graceful degradation**
Three failure strategies per node: `retry` with exponential backoff, `skip` to continue the pipeline without the node, or `fallback` to route to an alternative node. Each is one line of config.

<br>

## Quickstart

```bash
git clone https://github.com/your-org/cerebellum
cd cerebellum
pip install -r requirements.txt   # one dependency: networkx
python scenarios.py               # run all three demos
```

**Your agent contract**

```python
async def my_agent(inputs: dict) -> tuple[Any, float]:
    # inputs: outputs of upstream nodes + initial_input keys
    result = await call_your_llm(inputs["upstream_node"])
    cost   = 0.008   # USD — report what you spent
    return result, cost
```

That's the entire interface. An agent is an async function that takes a dict and returns `(output, cost_usd)`.

<br>

## Scenarios

Three runnable examples are included in `scenarios.py`.

**Research pipeline** — 11 nodes, three parallel fan-outs. Web search feeds two parallel extractors, which feed three parallel writers, which feed parallel SEO and fact-check agents, which feed a publisher. Total wall time is bounded by the longest parallel branch, not the sum of all nodes.

**Trading signal pipeline** — live market data feed fails at runtime. Cerebellum automatically falls back to cached prices, runs momentum, mean-reversion, and sentiment agents in parallel, feeds an ensemble signal to a risk check, and executes the order — or skips it if confidence is below threshold. Budget cap prevents runaway cost on bad market data.

**Crash and resume** — an 8-step linear pipeline crashes at step 5. On the second run with `resume=True`, Cerebellum restores from the last checkpoint: steps 1–4 are skipped entirely, steps 5–8 re-run. Cost for the resumed run is exactly half the full run cost.

<br>

## Why not LangGraph / AutoGen / Airflow?

|                        | Cerebellum | LangGraph | AutoGen | Airflow |
|------------------------|:----------:|:---------:|:-------:|:-------:|
| Deterministic scheduling | ✓ | △ | ✗ | ✓ |
| Parallel agent execution | ✓ | ✓ | △ | ✓ |
| Exact crash resume       | ✓ | ✗ | ✗ | ✓ |
| Hard budget cap          | ✓ | ✗ | ✗ | ✗ |
| LLM-free coordination    | ✓ | △ | ✗ | ✓ |
| Zero infra required      | ✓ | △ | ✓ | ✗ |
| Typed inter-agent messages | ✓ | ✗ | ✗ | ✗ |

△ = partial or optional

LangGraph gives you the graph abstraction but still routes coordination decisions through LLM calls. AutoGen is conversation-first — agents negotiate their own sequencing, which is non-deterministic by design. Airflow was built for data pipelines and doesn't understand token cost or LLM call semantics. Cerebellum is the layer none of them are.

<br>

## Roadmap

- [ ] Redis-backed checkpoint store for multi-process deployments
- [ ] FastAPI server — expose any graph as an HTTP endpoint
- [ ] OpenTelemetry traces — per-node spans with token cost attributes
- [ ] Graph visualiser — live execution map in the terminal
- [ ] Workflow templates — one-line setup for common patterns (research, trading, content)
- [ ] CLI — `cerebellum run graph.yaml`

<br>

## Contributing

Open an issue before opening a PR. Describe what you're solving, not what you're changing.

The coordination layer should remain LLM-free. If a proposed feature requires an LLM call inside the scheduler, it belongs in an agent, not in Cerebellum.

<br>

<div align="center">

Built with the conviction that **the brain that coordinates shouldn't be the same brain that thinks**.

</div>