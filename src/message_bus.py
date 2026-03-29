"""
Cerebellum — Agent Message Bus
Standardised inter-agent communication protocol.

Three layers:
  1. Message schema      — typed envelope every message must conform to
  2. MessageBus          — async pub/sub router with topic filtering
  3. AgentChannel        — per-agent view: send / receive / request-reply
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Awaitable


# ─── 1. MESSAGE SCHEMA ────────────────────────────────────────────────────────
#
#  Every message between agents is a typed envelope.
#  Nothing passes as a raw dict — the bus rejects untyped payloads.
#
#  Anatomy:
#    id          — globally unique, used for request/reply correlation
#    type        — what kind of message (see MsgType)
#    topic       — routing key (e.g. "search.done", "analyze.request")
#    sender      — agent name that emitted this
#    recipient   — None = broadcast, str = unicast
#    reply_to    — id of the message this is responding to
#    payload     — arbitrary data, must be JSON-serialisable
#    metadata    — cost, latency, model used, etc.

class MsgType(Enum):
    EVENT   = "event"    # fire-and-forget broadcast
    REQUEST = "request"  # expects exactly one REPLY
    REPLY   = "reply"    # response to a REQUEST
    ERROR   = "error"    # signals a failure to any subscribers
    COMMAND = "command"  # directive to a specific agent (unicast)


@dataclass
class Message:
    type:      MsgType
    topic:     str
    sender:    str
    payload:   Any                   = None
    recipient: str | None            = None   # None = broadcast
    reply_to:  str | None            = None
    metadata:  dict[str, Any]        = field(default_factory=dict)
    id:        str                   = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: float                 = field(default_factory=time.time)

    def reply(self, payload: Any, sender: str) -> "Message":
        """Convenience: build a REPLY addressed back to this message's sender."""
        return Message(
            type      = MsgType.REPLY,
            topic     = f"{self.topic}.reply",
            sender    = sender,
            recipient = self.sender,
            reply_to  = self.id,
            payload   = payload,
        )

    def error(self, reason: str, sender: str) -> "Message":
        return Message(
            type      = MsgType.ERROR,
            topic     = f"{self.topic}.error",
            sender    = sender,
            recipient = self.sender,
            reply_to  = self.id,
            payload   = {"error": reason},
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        d = dict(d)
        d["type"] = MsgType(d["type"])
        return cls(**d)

    def __repr__(self) -> str:
        r = f"→ {self.recipient}" if self.recipient else "⊕ broadcast"
        return (f"[{self.type.value.upper()}] {self.sender} {r} "
                f"topic={self.topic!r} id={self.id}")


# ─── 2. MESSAGE BUS ───────────────────────────────────────────────────────────
#
#  Central router. Agents never talk to each other directly.
#  All messages flow through here, giving us:
#    • a full audit log
#    • topic-based filtering
#    • request/reply correlation (pending_replies dict)
#    • dead-letter queue for unroutable messages

HandlerFn = Callable[[Message], Awaitable[None]]


class MessageBus:

    def __init__(self, audit: bool = True):
        self._subs:           dict[str, list[tuple[str, HandlerFn]]] = {}
        self._pending_replies: dict[str, asyncio.Future]             = {}
        self._dead_letters:    list[Message]                         = []
        self._audit_log:       list[dict]                            = []
        self._audit           = audit
        self._lock            = asyncio.Lock()

    # ── Subscribe / Unsubscribe ───────────────────────────────────────────────

    def subscribe(self, topic_pattern: str, agent_name: str, handler: HandlerFn) -> None:
        """
        topic_pattern supports wildcards:
          "search.done"      — exact match
          "search.*"         — one-level wildcard
          "*"                — everything
        """
        self._subs.setdefault(topic_pattern, []).append((agent_name, handler))

    def unsubscribe(self, topic_pattern: str, agent_name: str) -> None:
        subs = self._subs.get(topic_pattern, [])
        self._subs[topic_pattern] = [(n, h) for n, h in subs if n != agent_name]

    # ── Publish ───────────────────────────────────────────────────────────────

    async def publish(self, msg: Message) -> None:
        if self._audit:
            self._audit_log.append(msg.to_dict())

        # If this is a REPLY, resolve the pending future first
        if msg.type == MsgType.REPLY and msg.reply_to:
            async with self._lock:
                fut = self._pending_replies.pop(msg.reply_to, None)
            if fut and not fut.done():
                fut.set_result(msg)
                return   # replies don't broadcast further

        # Route to subscribers
        handlers = self._resolve_handlers(msg)
        if not handlers:
            self._dead_letters.append(msg)
            return

        await asyncio.gather(*[h(msg) for _, h in handlers], return_exceptions=True)

    # ── Request / Reply ───────────────────────────────────────────────────────

    async def request(
        self,
        msg:     Message,
        timeout: float = 10.0,
    ) -> Message:
        """
        Send a REQUEST and block until a REPLY arrives (or timeout).
        Raises asyncio.TimeoutError if no reply within `timeout` seconds.
        """
        fut: asyncio.Future[Message] = asyncio.get_event_loop().create_future()
        async with self._lock:
            self._pending_replies[msg.id] = fut

        await self.publish(msg)

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            async with self._lock:
                self._pending_replies.pop(msg.id, None)
            raise asyncio.TimeoutError(
                f"No reply to {msg.topic!r} (id={msg.id}) within {timeout}s"
            )

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def audit_log(self) -> list[dict]:
        return list(self._audit_log)

    @property
    def dead_letters(self) -> list[Message]:
        return list(self._dead_letters)

    def stats(self) -> dict:
        return {
            "total_messages":   len(self._audit_log),
            "dead_letters":     len(self._dead_letters),
            "pending_replies":  len(self._pending_replies),
            "subscriptions":    {k: [n for n,_ in v] for k,v in self._subs.items()},
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _resolve_handlers(self, msg: Message) -> list[tuple[str, HandlerFn]]:
        matched = []
        for pattern, subs in self._subs.items():
            if not self._topic_matches(pattern, msg.topic):
                continue
            for name, handler in subs:
                # unicast: only deliver to the named recipient
                if msg.recipient and msg.recipient != name:
                    continue
                matched.append((name, handler))
        return matched

    @staticmethod
    def _topic_matches(pattern: str, topic: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            return topic.startswith(pattern[:-2])
        return pattern == topic


# ─── 3. AGENT CHANNEL ────────────────────────────────────────────────────────
#
#  Per-agent facade. Each agent gets one channel object.
#  Hides the bus internals — agents only call emit / request / on.

class AgentChannel:

    def __init__(self, agent_name: str, bus: MessageBus):
        self.name = agent_name
        self._bus = bus
        # personal inbox for unicast COMMANDs/REPLYs
        self._inbox: asyncio.Queue[Message] = asyncio.Queue()
        bus.subscribe("*", agent_name, self._inbox_handler)

    async def _inbox_handler(self, msg: Message) -> None:
        if msg.recipient == self.name:
            await self._inbox.put(msg)

    # ── Emit (fire-and-forget) ────────────────────────────────────────────────

    async def emit(self, topic: str, payload: Any = None, **meta) -> None:
        msg = Message(
            type     = MsgType.EVENT,
            topic    = topic,
            sender   = self.name,
            payload  = payload,
            metadata = meta,
        )
        await self._bus.publish(msg)

    # ── Request/Reply ─────────────────────────────────────────────────────────

    async def ask(
        self,
        topic:     str,
        payload:   Any   = None,
        recipient: str   = None,
        timeout:   float = 10.0,
    ) -> Any:
        """Send a request and await the reply payload."""
        msg = Message(
            type      = MsgType.REQUEST,
            topic     = topic,
            sender    = self.name,
            recipient = recipient,
            payload   = payload,
        )
        reply = await self._bus.request(msg, timeout=timeout)
        return reply.payload

    async def reply_to(self, original: Message, payload: Any) -> None:
        await self._bus.publish(original.reply(payload, sender=self.name))

    # ── Subscribe ─────────────────────────────────────────────────────────────

    def on(self, topic_pattern: str, handler: HandlerFn) -> None:
        self._bus.subscribe(topic_pattern, self.name, handler)

    # ── Receive from inbox ────────────────────────────────────────────────────

    async def receive(self, timeout: float = 5.0) -> Message:
        return await asyncio.wait_for(self._inbox.get(), timeout=timeout)