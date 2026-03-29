# Cerebellum

Deterministic multi-agent orchestration layer.
LLM handles *content*. Cerebellum handles *coordination*.

## Files

| File | Role |
|---|---|
| `engine.py` | DAG execution engine, parallel scheduling, budget guard |
| `state_machine.py` | Node state FSM, SQLite checkpoint store, rollback manager |
| `message_bus.py` | Typed pub/sub message bus, request/reply, audit log |
| `cerebellum.py` | Integrated runtime — wires all three layers together |
| `scenarios.py` | Three real-world demos: research pipeline, trading signals, crash+resume |

## Quickstart

```python
pip install networkx

from cerebellum import Cerebellum
from engine import FailPolicy, FailStrategy

c = Cerebellum("my_run.db")

c.node("search",  agent=search_fn)
c.node("analyze", agent=analyze_fn, depends_on=["search"])
c.node("write_a", agent=write_fn,   depends_on=["analyze"])
c.node("write_b", agent=write_fn,   depends_on=["analyze"])
c.node("merge",   agent=merge_fn,   depends_on=["write_a", "write_b"])

c.on_fail("search", retry=3, timeout=10.0)

result = await c.run({"query": "topic"}, budget_usd=0.10)
print(result.summary())
```

## Agent contract

An agent is just an async function:

```python
async def my_agent(inputs: dict) -> tuple[Any, float]:
    # inputs: outputs from upstream nodes + initial_input
    output = ...  # call your LLM here
    cost   = 0.005  # USD spent on tokens
    return output, cost
```

## Key properties

- **Parallel by default** — independent branches run with asyncio.gather
- **Deterministic scheduling** — topological sort, no LLM decides order
- **Persistent checkpoints** — every terminal node saves to SQLite
- **Exact resume** — completed nodes are never re-run after crash
- **Budget guard** — hard USD cap, downstream auto-cancelled on breach
- **Typed messages** — all inter-agent comms go through the message bus
- **Zero new infra** — only dependency is `networkx`; state in SQLite