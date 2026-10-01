"""Local mock payments API used by the demo, tests and evals.

It honours `Idempotency-Key` (a replayed key returns the original refund) and supports fault
injection so retries and fallbacks can be demonstrated deterministically."""

from __future__ import annotations

import random
import secrets
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

SERVICE_NAME = "cerebellum-sandbox-payments"


@dataclass(frozen=True)
class FailMode:
    kind: Literal["never", "first", "always", "rate"] = "never"
    n: int = 0
    p: float = 0.0

    @classmethod
    def parse(cls, text: str) -> FailMode:
        raw = text.strip().lower()
        if raw == "never":
            return cls("never")
        if raw == "always":
            return cls("always")
        name, _, arg = raw.partition(":")
        try:
            if name == "first" and arg and int(arg) >= 0:
                return cls("first", n=int(arg))
            if name == "rate" and arg and 0.0 <= float(arg) <= 1.0:
                return cls("rate", p=float(arg))
        except ValueError:
            pass
        raise ValueError(f"invalid fail mode {text!r}; use never, always, first:N or rate:P")

    def __str__(self) -> str:
        if self.kind == "first":
            return f"first:{self.n}"
        if self.kind == "rate":
            return f"rate:{self.p:g}"
        return self.kind


class PaymentsState:
    def __init__(self, fail: FailMode | None = None, *, seed: int | None = None):
        self.fail = fail or FailMode()
        self.refunds: dict[str, dict[str, Any]] = {}
        self._by_key: dict[str, str] = {}
        self._attempts: Counter[str] = Counter()
        self._rng = random.Random(seed)
        self._lock = threading.Lock()

    def set_fail_mode(self, mode: FailMode) -> None:
        with self._lock:
            self.fail = mode
            self._attempts.clear()

    def replay(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            refund_id = self._by_key.get(key)
            return self.refunds[refund_id] if refund_id else None

    def should_fail(self, key: str) -> bool:
        with self._lock:
            self._attempts[key] += 1
            attempt = self._attempts[key]
            if self.fail.kind == "always":
                return True
            if self.fail.kind == "first":
                return attempt <= self.fail.n
            if self.fail.kind == "rate":
                return self._rng.random() < self.fail.p
            return False

    def create(
        self, key: str, order_id: str, amount: float, currency: str, reason: str | None
    ) -> dict[str, Any]:
        with self._lock:
            if key in self._by_key:
                return self.refunds[self._by_key[key]]
            refund_id = "rf_" + secrets.token_hex(5)
            record = {
                "id": refund_id,
                "order_id": order_id,
                "amount": amount,
                "currency": currency,
                "reason": reason,
                "status": "succeeded",
                "idempotency_key": key,
                "created_at": time.time(),
            }
            self.refunds[refund_id] = record
            self._by_key[key] = refund_id
            return record


class RefundRequest(BaseModel):
    order_id: str = Field(min_length=1)
    amount: float = Field(gt=0)
    currency: str = "USD"
    reason: str | None = None


class FailModeRequest(BaseModel):
    mode: str


def create_payments_app(state: PaymentsState | None = None) -> FastAPI:
    app = FastAPI(title="Cerebellum Sandbox Payments", docs_url=None, redoc_url=None)
    payments = state or PaymentsState()
    app.state.payments = payments

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": SERVICE_NAME,
            "fail_mode": str(payments.fail),
            "refunds": len(payments.refunds),
        }

    @app.post("/refunds", status_code=201)
    def create_refund(
        body: RefundRequest, idempotency_key: str | None = Header(default=None)
    ) -> Any:
        key = idempotency_key or "anon_" + secrets.token_hex(6)
        existing = payments.replay(key)
        if existing is not None:
            return JSONResponse(existing, status_code=200)
        if payments.should_fail(key):
            raise HTTPException(
                503, detail="payments provider unavailable (sandbox fault injection)"
            )
        return payments.create(key, body.order_id, body.amount, body.currency, body.reason)

    @app.get("/refunds/{refund_id}")
    def get_refund(refund_id: str) -> dict[str, Any]:
        record = payments.refunds.get(refund_id)
        if record is None:
            raise HTTPException(404, detail=f"refund {refund_id} not found")
        return record

    @app.put("/_sandbox/fail-mode")
    def set_fail_mode(body: FailModeRequest) -> dict[str, str]:
        try:
            mode = FailMode.parse(body.mode)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        payments.set_fail_mode(mode)
        return {"fail_mode": str(mode)}

    return app
