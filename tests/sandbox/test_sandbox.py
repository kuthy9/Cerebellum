import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from cerebellum.errors import CerebellumError
from cerebellum.sandbox.payments import FailMode, PaymentsState, create_payments_app
from cerebellum.sandbox.server import sandbox_running, start_sandbox

REFUND = {"order_id": "A1001", "amount": 120.0}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("never", FailMode("never")),
        ("always", FailMode("always")),
        ("first:2", FailMode("first", n=2)),
        ("rate:0.3", FailMode("rate", p=0.3)),
        (" FIRST:0 ", FailMode("first", n=0)),
    ],
)
def test_fail_mode_parse(text, expected):
    mode = FailMode.parse(text)
    assert mode == expected
    assert FailMode.parse(str(mode)) == mode


@pytest.mark.parametrize("text", ["sometimes", "first:", "first:-1", "rate:2", "rate:x", ""])
def test_fail_mode_rejects_invalid(text):
    with pytest.raises(ValueError, match="invalid fail mode"):
        FailMode.parse(text)


def client_for(state=None):
    return TestClient(create_payments_app(state or PaymentsState()))


def test_create_and_replay_by_idempotency_key():
    client = client_for()
    first = client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "r_1:pay"})
    assert first.status_code == 201
    body = first.json()
    assert body["id"].startswith("rf_") and body["status"] == "succeeded"
    replay = client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "r_1:pay"})
    assert replay.status_code == 200 and replay.json()["id"] == body["id"]
    other = client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "r_2:pay"})
    assert other.json()["id"] != body["id"]
    assert client.get(f"/refunds/{body['id']}").json()["order_id"] == "A1001"
    assert client.get("/refunds/rf_missing").status_code == 404


def test_first_n_fails_per_key_then_succeeds():
    client = client_for(PaymentsState(FailMode.parse("first:2")))
    codes = [
        client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "k1"}).status_code
        for _ in range(3)
    ]
    assert codes == [503, 503, 201]
    second_key = client.post("/refunds", json=REFUND, headers={"Idempotency-Key": "k2"})
    assert second_key.status_code == 503


def test_always_and_switching_fail_mode():
    state = PaymentsState(FailMode.parse("always"))
    client = client_for(state)
    headers = {"Idempotency-Key": "k"}
    assert client.post("/refunds", json=REFUND, headers=headers).status_code == 503
    assert client.put("/_sandbox/fail-mode", json={"mode": "never"}).json() == {
        "fail_mode": "never"
    }
    assert client.post("/refunds", json=REFUND, headers=headers).status_code == 201
    assert client.put("/_sandbox/fail-mode", json={"mode": "bogus"}).status_code == 422
    assert len(state.refunds) == 1


def test_validation_errors_are_422():
    client = client_for()
    assert client.post("/refunds", json={"order_id": "A1", "amount": 0}).status_code == 422


def test_health_reports_service_and_mode():
    body = client_for(PaymentsState(FailMode.parse("first:1"))).get("/health").json()
    assert body == {
        "ok": True,
        "service": "cerebellum-sandbox-payments",
        "fail_mode": "first:1",
        "refunds": 0,
    }


def test_start_sandbox_runs_in_background_and_is_reused(free_port):
    handle = start_sandbox("127.0.0.1", free_port, "never")
    try:
        assert handle.owned and sandbox_running(handle.url)
        reused = start_sandbox("127.0.0.1", free_port, "first:1")
        assert reused.owned is False
        assert httpx.get(f"{handle.url}/health").json()["fail_mode"] == "first:1"
        reused.stop()  # no-op for a borrowed sandbox
        assert sandbox_running(handle.url)
    finally:
        handle.stop()
    assert not sandbox_running(handle.url)


def test_start_sandbox_refuses_a_port_used_by_something_else(free_port):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", free_port))
        blocker.listen()
        with pytest.raises(CerebellumError, match="in use by another service"):
            start_sandbox("127.0.0.1", free_port)
