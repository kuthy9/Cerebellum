import pytest

from cerebellum.runtime.store import EventRecord
from cerebellum.runtime.trace import build_spans


def ev(seq, event_type, ts, step_id=None, span_id=None, parent=None, /, **data):
    return EventRecord(seq, "r_1", step_id, span_id, parent, event_type, ts, data)


def test_build_spans_pairs_attempts_and_nests_calls():
    events = [
        ev(1, "run.started", 0.0),
        ev(2, "step.started", 1.0, "pay", "pay#1", attempt=1, type="http"),
        ev(
            3,
            "connector.call",
            1.5,
            "pay",
            "pay#1:connector:a",
            "pay#1",
            method="POST",
            path="/refunds",
            status=503,
            ok=False,
            duration_ms=400,
        ),
        ev(4, "step.retrying", 1.6, "pay", "pay#1", attempt=1),
        ev(5, "step.started", 2.0, "pay", "pay#2", attempt=2, type="http"),
        ev(
            6,
            "connector.call",
            2.2,
            "pay",
            "pay#2:connector:b",
            "pay#2",
            method="POST",
            path="/refunds",
            status=201,
            ok=True,
            duration_ms=100,
        ),
        ev(7, "step.succeeded", 2.3, "pay", "pay#2"),
        ev(8, "step.started", 3.0, "gate", "gate#1", attempt=1, type="approval"),
        ev(9, "approval.requested", 3.0, "gate", "gate#1", approval_id="ap_1"),
        ev(10, "step.waiting", 3.0, "gate", "gate#1"),
        ev(
            11,
            "llm.call",
            3.5,
            "judge",
            "judge#1:llm:c",
            "judge#1",
            model="claude-opus-5-5",
            mock=True,
            ok=True,
            duration_ms=250,
        ),
        ev(
            12,
            "connector.call",
            4.0,
            "load",
            "load#1:connector:d",
            "load#1",
            operation="query",
            ok=True,
            duration_ms=0,
        ),
    ]
    spans = build_spans(events)
    assert [s.label for s in spans] == [
        "pay #1",
        "POST /refunds → 503",
        "pay #2",
        "POST /refunds → 201",
        "gate #1",
        "llm claude-opus-5-5 (mock)",
        "sql query",
    ]
    first, call1, second, call2, gate, llm, sql = spans
    assert (first.status, first.end) == ("retrying", 1.6)
    assert call1.parent_id == "pay#1" and call1.status == "failed"
    assert call1.start == pytest.approx(1.1)
    assert (second.status, second.end) == ("succeeded", 2.3)
    assert gate.status == "waiting" and gate.end is None
    assert llm.kind == "llm" and llm.start == pytest.approx(3.25)
    assert sql.kind == "connector" and sql.start == sql.end == 4.0
