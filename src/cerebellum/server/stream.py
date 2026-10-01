"""Server-sent events: every new event in the store, in order — including events written by other
processes (the CLI), because the stream polls the shared event table."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from cerebellum.runtime.store import Store
from cerebellum.server.serialize import dumps, event_json


async def event_stream(
    store: Store,
    *,
    after: int,
    poll: float,
    is_disconnected: Callable[[], Awaitable[bool]],
    keepalive_every: int = 30,
) -> AsyncIterator[str]:
    seq = after
    idle = 0
    yield "retry: 2000\n\n"
    while not await is_disconnected():
        events = store.events_since(seq)
        for event in events:
            seq = event.seq
            yield f"id: {event.seq}\ndata: {dumps(event_json(event), default=str)}\n\n"
        if events:
            idle = 0
            continue
        idle += 1
        if idle >= keepalive_every:
            idle = 0
            yield ": keep-alive\n\n"
        await asyncio.sleep(poll)
