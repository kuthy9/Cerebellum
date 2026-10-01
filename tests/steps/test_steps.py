import json

import httpx
import pytest

from cerebellum.ai.base import AIError, AIResult, Usage
from cerebellum.ai.mock import MockProvider
from cerebellum.config import Settings
from cerebellum.connectors import ConnectorError
from cerebellum.connectors.postgres import SqliteSandboxConnector
from cerebellum.connectors.rest import RestConnector
from cerebellum.errors import StepError
from cerebellum.spec.models import (
    AiStep,
    HttpStep,
    MockRule,
    QueryStep,
    RestConnectorSpec,
    TaskStep,
    ValidateStep,
)
from cerebellum.steps import EXECUTORS
from cerebellum.steps.base import StepRuntime
from cerebellum.steps.query import returns_rows

CTX = {
    "input": {"order_id": "A1", "amount": 50, "reason": "broken"},
    "params": {"threshold": 100},
    "steps": {"load": {"output": {"id": "A1", "amount": 80}, "status": "succeeded"}},
    "run": {"id": "r_1", "cost_usd": 0.0},
}
SCHEMA = {
    "type": "object",
    "required": ["ok"],
    "additionalProperties": False,
    "properties": {"ok": {"type": "boolean"}},
}


class FakePool:
    def __init__(self, **connectors):
        self.connectors = connectors

    async def get(self, name):
        return self.connectors[name]


class Recorder:
    def __init__(self):
        self.calls = []
        self.tasks = []

    def record(self, kind, data, cost_usd=0.0):
        self.calls.append((kind, data, cost_usd))

    def create_task(self, title, assignee, payload):
        self.tasks.append((title, assignee, payload))
        return "tk_0001"


def runtime(step, *, pool=None, ai=None):
    recorder = Recorder()
    rt = StepRuntime(
        run_id="r_1",
        step=step,
        attempt=1,
        span_id=f"{step.id}#1",
        ctx=CTX,
        connectors=pool or FakePool(),
        ai=ai or MockProvider(latency=(0, 0)),
        settings=Settings.from_env({}),
        record_call=recorder.record,
        create_task=recorder.create_task,
    )
    return rt, recorder


@pytest.fixture
async def orders(tmp_path):
    seed = tmp_path / "seed.sql"
    seed.write_text(
        "CREATE TABLE orders (id TEXT PRIMARY KEY, amount NUMERIC NOT NULL);"
        "INSERT INTO orders VALUES ('A1', 80), ('A2', 20);"
    )
    connector = SqliteSandboxConnector("db", tmp_path / "db.sqlite", seed)
    yield connector
    await connector.close()


def test_registry_covers_executable_step_types():
    assert set(EXECUTORS) == {"query", "http", "ai", "validate", "task"}


async def test_query_expect_one_returns_row_and_records_call(orders):
    step = QueryStep(
        id="load",
        type="query",
        connector="db",
        expect="one",
        sql="SELECT id, amount FROM orders WHERE id = :id",
        params={"id": "{{ input.order_id }}"},
    )
    rt, rec = runtime(step, pool=FakePool(db=orders))
    assert await EXECUTORS["query"](rt) == {"id": "A1", "amount": 80}
    kind, data, _ = rec.calls[0]
    assert kind == "connector"
    assert data["operation"] == "query" and data["params"] == {"id": "A1"}
    assert data["ok"] is True and data["rows"] == 1


async def test_query_expect_one_with_no_rows_is_not_retryable(orders):
    step = QueryStep(
        id="load",
        type="query",
        connector="db",
        expect="one",
        sql="SELECT id FROM orders WHERE id = :id",
        params={"id": "ZZ"},
    )
    rt, _ = runtime(step, pool=FakePool(db=orders))
    with pytest.raises(StepError, match="expected exactly one row, got 0") as info:
        await EXECUTORS["query"](rt)
    assert info.value.retryable is False and info.value.kind == "expect"


async def test_query_expect_many_and_none(orders):
    many = QueryStep(
        id="q", type="query", connector="db", expect="many", sql="SELECT id FROM orders"
    )
    rt, _ = runtime(many, pool=FakePool(db=orders))
    assert len(await EXECUTORS["query"](rt)) == 2
    none = QueryStep(
        id="q", type="query", connector="db", expect="none", sql="SELECT id FROM orders"
    )
    rt, _ = runtime(none, pool=FakePool(db=orders))
    with pytest.raises(StepError, match="expected no rows"):
        await EXECUTORS["query"](rt)


async def test_query_write_returns_rowcount(orders):
    step = QueryStep(
        id="mark",
        type="query",
        connector="db",
        sql="UPDATE orders SET amount = 0 WHERE id = :id",
        params={"id": "A2"},
    )
    rt, rec = runtime(step, pool=FakePool(db=orders))
    assert await EXECUTORS["query"](rt) == {"rowcount": 1}
    assert rec.calls[0][1]["operation"] == "execute" and rec.calls[0][1]["rowcount"] == 1


async def run_sql(connector, sql, *, expect="any", params=None):
    step = QueryStep(
        id="q", type="query", connector="db", sql=sql, expect=expect, params=params or {}
    )
    rt, rec = runtime(step, pool=FakePool(db=connector))
    return await EXECUTORS["query"](rt), rec.calls[0][1]["operation"]


async def committed_amounts(orders):
    fresh = SqliteSandboxConnector("check", orders.path, None)
    try:
        rows = await fresh.query("SELECT id, amount FROM orders ORDER BY id", {})
    finally:
        await fresh.close()
    return {row["id"]: row["amount"] for row in rows}


async def test_query_statement_after_a_comment_is_classified_by_the_statement(orders):
    read = "-- the order\nSELECT id FROM orders WHERE id = :id"
    assert await run_sql(orders, read, expect="one", params={"id": "A1"}) == (
        {"id": "A1"},
        "query",
    )
    update = "-- note\nUPDATE orders SET amount = 1 WHERE id = 'A1'"
    assert await run_sql(orders, update) == ({"rowcount": 1}, "execute")
    delete = "/* x */ DELETE FROM orders WHERE id = 'A2'"
    assert await run_sql(orders, delete) == ({"rowcount": 1}, "execute")
    assert await committed_amounts(orders) == {"A1": 1}


async def test_query_data_modifying_cte_is_a_write(orders):
    sql = (
        "WITH target AS (SELECT 'A2' AS id) "
        "UPDATE orders SET amount = 0 WHERE id IN (SELECT id FROM target)"
    )
    assert await run_sql(orders, sql) == ({"rowcount": 1}, "execute")
    assert await committed_amounts(orders) == {"A1": 80, "A2": 0}


async def test_query_write_with_returning_outputs_its_rows_and_commits(orders):
    sql = "UPDATE orders SET amount = amount + 1 WHERE id = :id RETURNING id, amount"
    assert await run_sql(orders, sql, expect="one", params={"id": "A1"}) == (
        {"id": "A1", "amount": 81},
        "query",
    )
    assert await committed_amounts(orders) == {"A1": 81, "A2": 20}
    quoted = "UPDATE orders SET amount = 5 WHERE id = 'returning'"
    assert await run_sql(orders, quoted) == ({"rowcount": 0}, "execute")


@pytest.mark.parametrize(
    ("sql", "rows"),
    [
        ("select 1", True),
        ("  /* a */ -- b\n  VALUES (1)", True),
        ("WITH x AS (SELECT 1) SELECT * FROM x", True),
        ("WITH moved AS (DELETE FROM a RETURNING *) SELECT * FROM moved", True),
        ("WITH RECURSIVE x(n) AS (SELECT 1) INSERT INTO t SELECT n FROM x", False),
        ("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x RETURNING id", True),
        ("INSERT INTO t (v) VALUES ('a') ON CONFLICT (v) DO UPDATE SET v = 'b' RETURNING v", True),
        ('UPDATE t SET note = $$ returning $$, other = "returning"', False),
        ("UPDATE t SET note = 'it''s -- returning' WHERE id = 1", False),
        ("-- only a comment", False),
    ],
)
def test_returns_rows_classifies_statements(sql, rows):
    assert returns_rows(sql) is rows


async def test_query_connector_error_is_recorded_and_raised(orders):
    step = QueryStep(id="q", type="query", connector="db", sql="SELECT * FROM missing_table")
    rt, rec = runtime(step, pool=FakePool(db=orders))
    with pytest.raises(ConnectorError):
        await EXECUTORS["query"](rt)
    assert rec.calls[0][1]["ok"] is False and "missing_table" in rec.calls[0][1]["error"]


def rest(handler):
    spec = RestConnectorSpec(
        type="rest", base_url="http://api.test", headers={"Authorization": "Bearer secret"}
    )
    return RestConnector("api", spec, transport=httpx.MockTransport(handler))


async def test_http_success_records_redacted_request():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["idempotency-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "rf_9"})

    step = HttpStep(
        id="pay",
        type="http",
        connector="api",
        method="POST",
        path="/refunds",
        body={"order_id": "{{ input.order_id }}", "amount": "{{ input.amount }}"},
    )
    rt, rec = runtime(step, pool=FakePool(api=rest(handler)))
    assert await EXECUTORS["http"](rt) == {
        "status": 201,
        "body": {"id": "rf_9"},
        "headers": {"content-type": "application/json"},
    }
    assert seen == {"key": "r_1:pay", "body": {"order_id": "A1", "amount": 50}}
    data = rec.calls[0][1]
    assert data["request"]["headers"]["Authorization"] == "***"
    assert data["request"]["headers"]["Idempotency-Key"] == "r_1:pay"
    assert data["status"] == 201 and data["ok"] is True


async def test_http_failure_records_status_and_body():
    step = HttpStep(id="pay", type="http", connector="api", method="POST", path="/refunds")
    failing = rest(lambda r: httpx.Response(503, json={"d": 1}))
    rt, rec = runtime(step, pool=FakePool(api=failing))
    with pytest.raises(ConnectorError) as info:
        await EXECUTORS["http"](rt)
    assert info.value.retryable is True
    data = rec.calls[0][1]
    assert data["ok"] is False and data["status"] == 503
    assert data["response"]["body"] == {"d": 1}


async def test_ai_with_mock_rule_returns_validated_output():
    step = AiStep(
        id="judge",
        type="ai",
        prompt="Judge {{ input.order_id }}",
        output_schema=SCHEMA,
        mock=[MockRule(when="input.amount < params.threshold", output={"ok": True})],
    )
    rt, rec = runtime(step)
    assert await EXECUTORS["ai"](rt) == {"ok": True}
    kind, data, cost = rec.calls[0]
    assert kind == "llm" and data["mock"] is True and data["ok"] is True
    assert data["prompt"] == "Judge A1" and data["model"] == "claude-opus-5-5" and cost == 0.0


class ScriptedAI:
    name = "scripted"
    mock = False

    def __init__(self, *texts):
        self.texts = list(texts)
        self.seen = []
        self.requests = []

    async def generate(self, request, messages):
        self.seen.append(list(messages))
        self.requests.append(request)
        text = self.texts.pop(0)
        return AIResult(
            text=text,
            model=request.model,
            usage=Usage(10, 5),
            cost_usd=0.01,
            mock=False,
            stop_reason="end_turn",
            latency_ms=3.0,
        )


async def test_ai_repairs_invalid_output_with_feedback():
    ai = ScriptedAI('{"ok": "yes"}', "not json", '{"ok": false}')
    step = AiStep(id="judge", type="ai", prompt="Judge", output_schema=SCHEMA, max_repairs=2)
    rt, rec = runtime(step, ai=ai)
    assert await EXECUTORS["ai"](rt) == {"ok": False}
    assert [len(m) for m in ai.seen] == [1, 3, 5]
    assert "failed JSON Schema validation" in ai.seen[1][-1]["content"]
    assert "ok: 'yes' is not of type 'boolean'" in ai.seen[1][-1]["content"]
    assert "not valid JSON" in ai.seen[2][-1]["content"]
    assert [c[1]["ok"] for c in rec.calls] == [False, False, True]
    assert sum(c[2] for c in rec.calls) == pytest.approx(0.03)


async def test_ai_default_max_tokens_leaves_room_for_adaptive_thinking():
    """Claude Opus 5.5 always thinks, and thinking tokens count toward max_tokens."""
    ai = ScriptedAI('{"ok": true}')
    step = AiStep(id="judge", type="ai", prompt="Judge", output_schema=SCHEMA)
    rt, _ = runtime(step, ai=ai)
    await EXECUTORS["ai"](rt)
    assert ai.requests[0].max_tokens >= 16000


async def test_ai_gives_up_after_repairs_with_retryable_schema_error():
    ai = ScriptedAI('{"ok": 1}', '{"ok": 2}')
    step = AiStep(id="judge", type="ai", prompt="Judge", output_schema=SCHEMA, max_repairs=1)
    rt, _ = runtime(step, ai=ai)
    with pytest.raises(StepError, match="after 2 attempt") as info:
        await EXECUTORS["ai"](rt)
    assert info.value.retryable is True and info.value.kind == "schema"


async def test_ai_provider_error_is_recorded_and_raised():
    class Failing:
        name = "failing"
        mock = False

        async def generate(self, request, messages):
            raise AIError("declined", retryable=False, kind="refusal")

    step = AiStep(id="judge", type="ai", prompt="Judge", output_schema=SCHEMA)
    rt, rec = runtime(step, ai=Failing())
    with pytest.raises(AIError):
        await EXECUTORS["ai"](rt)
    assert rec.calls[0][1]["ok"] is False and rec.calls[0][1]["kind"] == "refusal"


async def test_validate_passes_and_lists_every_failed_rule():
    rules = [
        {"expr": "input.amount <= steps.load.output.amount", "message": "too much"},
        {"expr": "input.amount > 0", "message": "must be positive"},
    ]
    ok = ValidateStep(id="v", type="validate", rules=rules)
    assert await EXECUTORS["validate"](runtime(ok)[0]) == {"passed": True, "checked": 2}

    bad = ValidateStep(
        id="v",
        type="validate",
        rules=[
            {"expr": "input.amount > 1000", "message": "needs more"},
            {"expr": "steps.missing.output.flag", "message": "flag must be set"},
        ],
    )
    with pytest.raises(StepError, match="needs more; flag must be set") as info:
        await EXECUTORS["validate"](runtime(bad)[0])
    assert info.value.retryable is False and info.value.kind == "validation"
    assert info.value.details == {"failed": ["needs more", "flag must be set"]}


async def test_task_creates_a_manual_task():
    step = TaskStep(
        id="manual",
        type="task",
        title="Handle {{ input.order_id }}",
        assignee="ops",
        payload={"amount": "{{ input.amount }}"},
    )
    rt, rec = runtime(step)
    assert await EXECUTORS["task"](rt) == {
        "task_id": "tk_0001",
        "title": "Handle A1",
        "assignee": "ops",
    }
    assert rec.tasks == [("Handle A1", "ops", {"amount": 50})]
