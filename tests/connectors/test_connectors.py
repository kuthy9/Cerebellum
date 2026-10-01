import asyncio
import os
import threading
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from cerebellum.connectors import (
    ConnectorEnv,
    ConnectorError,
    ConnectorPool,
    create_connector,
    jsonable,
)
from cerebellum.connectors.postgres import (
    PostgresConnector,
    SqliteSandboxConnector,
    sandbox_db_path,
    to_pyformat,
)
from cerebellum.connectors.rest import RestConnector, redact_headers
from cerebellum.errors import CerebellumError
from cerebellum.spec.models import PostgresConnectorSpec, RestConnectorSpec

SEED = """
CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, amount NUMERIC(10, 2) NOT NULL,
                                   status TEXT NOT NULL);
INSERT INTO orders (id, amount, status) VALUES ('A1', 120.00, 'delivered'), ('A2', 15.50, 'shipped')
ON CONFLICT (id) DO NOTHING;
"""


@pytest.fixture
def seed_file(tmp_path):
    path = tmp_path / "seed.sql"
    path.write_text(SEED)
    return path


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM t WHERE id = :id", "SELECT * FROM t WHERE id = %(id)s"),
        ("SELECT x::text FROM t WHERE a = :a_1", "SELECT x::text FROM t WHERE a = %(a_1)s"),
        (
            "SELECT ':not' AS s, '5%' AS p WHERE b = :b",
            "SELECT ':not' AS s, '5%%' AS p WHERE b = %(b)s",
        ),
        ("SELECT 'it''s :x' WHERE c = :c", "SELECT 'it''s :x' WHERE c = %(c)s"),
        ("SELECT 10 % 3", "SELECT 10 %% 3"),
    ],
)
def test_to_pyformat(sql, expected):
    assert to_pyformat(sql) == expected


def test_jsonable():
    assert jsonable(Decimal("12.50")) == 12.5
    assert jsonable(date(2026, 10, 1)) == "2026-10-01"
    assert jsonable(datetime(2026, 10, 1, 8, 30)) == "2026-10-01T08:30:00"
    assert jsonable(b"\x01\xff") == "01ff"
    assert jsonable("x") == "x"


async def test_sandbox_seeds_once_and_queries(tmp_path, seed_file):
    db = tmp_path / "sandbox.db"
    connector = SqliteSandboxConnector("orders_db", db, seed_file)
    rows = await connector.query(
        "SELECT id, amount, status FROM orders WHERE id = :id", {"id": "A1"}
    )
    assert rows == [{"id": "A1", "amount": 120, "status": "delivered"}]
    updated = await connector.execute(
        "UPDATE orders SET status = 'refunded' WHERE id = :id", {"id": "A1"}
    )
    assert updated == 1
    await connector.close()

    reopened = SqliteSandboxConnector("orders_db", db, seed_file)
    rows = await reopened.query("SELECT status FROM orders WHERE id = :id", {"id": "A1"})
    assert rows == [{"status": "refunded"}]  # not re-seeded
    health = await reopened.health()
    assert health.ok and "sqlite sandbox" in health.detail
    await reopened.close()


async def test_sandbox_missing_param_is_not_retryable(tmp_path, seed_file):
    connector = SqliteSandboxConnector("db", tmp_path / "s.db", seed_file)
    with pytest.raises(ConnectorError) as info:
        await connector.query("SELECT * FROM orders WHERE id = :id", {})
    assert info.value.retryable is False and info.value.kind == "sql"
    await connector.close()


async def test_sandbox_bad_seed_removes_partial_file(tmp_path):
    bad = tmp_path / "bad.sql"
    bad.write_text("CREATE TABLE broken (;")
    db = tmp_path / "s.db"
    connector = SqliteSandboxConnector("db", db, bad)
    with pytest.raises(ConnectorError, match="cannot seed") as info:
        await connector.open()
    assert info.value.retryable is False
    assert not db.exists()


async def abandoned_operation(connector):
    """Start a sandbox operation and cancel its caller mid-way, as a step timeout does: the
    operation's thread keeps running until `release` is set."""
    started, release, order = threading.Event(), threading.Event(), []

    def slow(conn):
        order.append("slow started")
        started.set()
        release.wait(5)
        order.append("slow finished")

    caller = asyncio.create_task(connector._run(slow))
    await asyncio.to_thread(started.wait, 5)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    return release, order


async def test_sandbox_operation_waits_for_an_abandoned_one(tmp_path, seed_file):
    connector = SqliteSandboxConnector("db", tmp_path / "s.db", seed_file)
    release, order = await abandoned_operation(connector)

    def count(conn):
        order.append("next")
        return conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]

    following = asyncio.create_task(connector._run(count))
    await asyncio.sleep(0.1)
    assert order == ["slow started"]  # the shared connection is still in use
    release.set()
    assert await following == 2
    assert order == ["slow started", "slow finished", "next"]
    await connector.close()


async def test_sandbox_close_waits_for_an_abandoned_operation(tmp_path, seed_file):
    connector = SqliteSandboxConnector("db", tmp_path / "s.db", seed_file)
    release, order = await abandoned_operation(connector)
    closing = asyncio.create_task(connector.close())
    await asyncio.sleep(0.1)
    assert not closing.done()
    release.set()
    await closing
    assert order == ["slow started", "slow finished"]


def test_factory_picks_sandbox_or_postgres(tmp_path):
    env = ConnectorEnv(home=tmp_path, base_dir=tmp_path)
    sandbox = create_connector(
        "orders_db", PostgresConnectorSpec(type="postgres", dsn="sandbox", seed="seed.sql"), env
    )
    assert isinstance(sandbox, SqliteSandboxConnector)
    assert sandbox.path == sandbox_db_path(tmp_path, "orders_db")
    assert sandbox.path == tmp_path / "sandbox_orders_db.db"
    assert sandbox.seed == tmp_path / "seed.sql"
    real = create_connector(
        "orders_db", PostgresConnectorSpec(type="postgres", dsn="postgresql://u@h/db"), env
    )
    assert isinstance(real, PostgresConnector)


def make_rest(handler, **spec):
    spec = RestConnectorSpec(type="rest", base_url="http://api.test", **spec)
    return RestConnector("api", spec, transport=httpx.MockTransport(handler))


async def test_rest_success_sends_idempotency_key_and_default_headers():
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        return httpx.Response(201, json={"id": "rf_1"}, headers={"x-request-id": "abc"})

    rest = make_rest(handler, headers={"Authorization": "Bearer secret"})
    response = await rest.request(
        "POST",
        "/refunds",
        json={"amount": 10},
        headers={"X-Trace": "t"},
        idempotency_key="r_1:pay",
    )
    assert response.status == 201
    assert response.body == {"id": "rf_1"}
    assert response.headers == {"content-type": "application/json", "x-request-id": "abc"}
    assert seen["headers"]["idempotency-key"] == "r_1:pay"
    assert seen["headers"]["authorization"] == "Bearer secret"
    assert seen["headers"]["x-trace"] == "t"
    assert rest.default_headers == {"Authorization": "Bearer secret"}
    await rest.close()


async def test_rest_5xx_and_429_are_retryable_with_details():
    rest = make_rest(lambda request: httpx.Response(503, json={"detail": "down"}))
    with pytest.raises(ConnectorError) as info:
        await rest.request("POST", "/refunds")
    assert info.value.retryable is True
    assert info.value.kind == "http_status"
    assert info.value.details["status"] == 503
    assert info.value.details["body"] == {"detail": "down"}

    limited = make_rest(lambda request: httpx.Response(429))
    with pytest.raises(ConnectorError) as info:
        await limited.request("GET", "/x")
    assert info.value.retryable is True


async def test_rest_4xx_is_not_retryable():
    rest = make_rest(lambda request: httpx.Response(404, json={"detail": "missing"}))
    with pytest.raises(ConnectorError, match="returned HTTP 404") as info:
        await rest.request("GET", "/refunds/x")
    assert info.value.retryable is False


async def test_rest_error_with_non_json_body():
    """Review focus: a gateway answering with HTML/plain text or nothing must not crash."""
    text = make_rest(
        lambda request: httpx.Response(
            502, text="Bad Gateway", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(ConnectorError) as info:
        await text.request("POST", "/refunds")
    assert info.value.details["body"] == "Bad Gateway"

    broken_json = make_rest(
        lambda request: httpx.Response(
            500, content=b"{not json", headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(ConnectorError) as info:
        await broken_json.request("POST", "/refunds")
    assert info.value.details["body"] == "{not json"

    empty = make_rest(lambda request: httpx.Response(500))
    with pytest.raises(ConnectorError) as info:
        await empty.request("POST", "/refunds")
    assert info.value.details["body"] is None


async def test_rest_timeouts_and_connection_errors_are_retryable():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ConnectorError) as info:
        await make_rest(timeout).request("GET", "/x")
    assert info.value.retryable is True and info.value.kind == "timeout"
    with pytest.raises(ConnectorError) as info:
        await make_rest(refused).request("GET", "/x")
    assert info.value.retryable is True and info.value.kind == "connection"


async def test_rest_health():
    healthy = make_rest(lambda request: httpx.Response(200, json={"ok": True}))
    assert (await healthy.health()).ok

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    status = await make_rest(refused).health()
    assert status.ok is False and "unreachable" in status.detail


def test_redact_headers():
    headers = {
        "Authorization": "Bearer x",
        "X-Api-Key": "k",
        "Cookie": "c",
        "X-Auth-Token": "t",
        "Idempotency-Key": "r_1:pay",
        "Content-Type": "application/json",
    }
    assert redact_headers(headers) == {
        "Authorization": "***",
        "X-Api-Key": "***",
        "Cookie": "***",
        "X-Auth-Token": "***",
        "Idempotency-Key": "r_1:pay",
        "Content-Type": "application/json",
    }


async def test_pool_opens_lazily_once_and_closes(tmp_path, seed_file):
    env = ConnectorEnv(home=tmp_path, base_dir=seed_file.parent)
    pool = ConnectorPool(
        {"orders_db": PostgresConnectorSpec(type="postgres", dsn="sandbox", seed=seed_file.name)},
        env,
    )
    first = await pool.get("orders_db")
    second = await pool.get("orders_db")
    assert first is second
    assert await first.query("SELECT COUNT(*) AS n FROM orders", {}) == [{"n": 2}]
    with pytest.raises(CerebellumError, match="not declared"):
        await pool.get("ghost")
    await pool.close()


@pytest.mark.postgres
async def test_real_postgres_roundtrip():
    dsn = os.environ.get("CEREBELLUM_TEST_PG_DSN")
    if not dsn:
        pytest.skip("CEREBELLUM_TEST_PG_DSN is not set")
    connector = PostgresConnector("pg", dsn)
    assert await connector.query("SELECT :x::int + 1 AS y", {"x": 1}) == [{"y": 2}]
    assert (await connector.health()).ok
    await connector.close()


def test_unknown_connector_type_is_reported(tmp_path):
    class Fake:
        type = "kafka"

    with pytest.raises(CerebellumError, match="kafka"):
        create_connector("x", Fake(), ConnectorEnv(home=tmp_path, base_dir=Path(".")))
