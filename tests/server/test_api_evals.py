import httpx
import pytest
from fastapi.testclient import TestClient

from cerebellum.ai.mock import MockProvider
from cerebellum.evals import EvalRunner, load_suite
from cerebellum.sandbox.payments import PaymentsState, create_payments_app
from cerebellum.server.app import create_app
from cerebellum.templates import template_path

WORKFLOW = template_path("refund") / "workflow.yaml"


@pytest.fixture
def transports():
    return {"payments": httpx.ASGITransport(app=create_payments_app(PaymentsState()))}


@pytest.fixture
def client(store, settings, transports):
    app = create_app(
        settings,
        provider=MockProvider(latency=(0, 0)),
        mode="mock AI (requested)",
        workflows_dir=template_path("refund"),
        store=store,
        http_transports=transports,
    )
    with TestClient(app) as test_client:
        yield test_client


def suite(tmp_path, decision):
    path = tmp_path / "evals.yaml"
    path.write_text(
        f"""
suite: api_smoke
workflow: {WORKFLOW}
cases:
  - id: small
    input: {{order_id: A1001, amount: 120}}
    expect: {{output.decision: {decision}}}
  - id: unknown
    input: {{order_id: Z9999, amount: 10}}
    expect: {{status: failed}}
""",
        encoding="utf-8",
    )
    return load_suite(path, env={})


async def run_eval(store, settings, transports, loaded):
    runner = EvalRunner(
        store, settings, MockProvider(latency=(0, 0)), http_transports=transports, jitter=0
    )
    return await runner.run(loaded)


async def test_eval_endpoints_list_runs_and_show_cases_with_regressions(
    client, store, settings, transports, tmp_path
):
    first = await run_eval(store, settings, transports, suite(tmp_path, "refunded"))
    second = await run_eval(store, settings, transports, suite(tmp_path, "manual"))

    listed = client.get("/api/evals").json()["evals"]
    assert [e["id"] for e in listed] == [second.id, first.id]
    assert listed[0]["pass_rate"] == 0.5 and listed[0]["regressions"] == 1
    assert listed[1]["pass_rate"] == 1.0 and listed[1]["duration_s"] is not None
    assert listed[1]["ai_first_pass_rate"] == 1.0

    detail = client.get(f"/api/evals/{second.id}").json()
    assert detail["eval"]["baseline_id"] == first.id and detail["baseline"]["id"] == first.id
    small, unknown = detail["results"]
    assert small["case_id"] == "small" and small["passed"] is False and small["regression"] is True
    assert (small["checks"][0]["expected"], small["checks"][0]["actual"]) == ("manual", "refunded")
    assert unknown["passed"] is True and unknown["regression"] is False
    assert [h["id"] for h in detail["history"]] == [first.id, second.id]
    run = client.get(f"/api/runs/{small['run_id']}").json()["run"]
    assert run["eval_run_id"] == second.id
    assert client.get("/api/evals?suite=nope").json()["evals"] == []
    assert client.get(f"/api/evals/{first.id}").json()["baseline"] is None


def test_eval_json_says_when_a_running_eval_lost_its_process(client, store, settings, clock):
    """Review finding: the Evals pages polled a killed eval's "running" row forever."""
    store.create_eval_run(
        "ev_00000001",
        suite="s",
        suite_path="/x/evals.yaml",
        workflow_name="wf",
        workflow_digest="d1",
        mock=True,
        total=2,
        baseline_id=None,
    )
    [alive] = client.get("/api/evals").json()["evals"]
    assert alive["stale"] is False and alive["heartbeat_at"] == clock.now()
    clock.advance(settings.lease_seconds + 1)
    [stale] = client.get("/api/evals").json()["evals"]
    assert stale["status"] == "running" and stale["stale"] is True
    detail = client.get("/api/evals/ev_00000001").json()
    assert detail["eval"]["stale"] is True and detail["history"][0]["stale"] is True


def test_unknown_eval_is_404(client):
    response = client.get("/api/evals/ev_nope")
    assert response.status_code == 404 and "ev_nope" in response.json()["detail"]


async def test_runs_endpoint_leaves_eval_runs_out(client, store, settings, transports, tmp_path):
    await run_eval(store, settings, transports, suite(tmp_path, "refunded"))
    assert client.get("/api/runs").json()["runs"] == []
    metrics = client.get("/api/metrics").json()
    assert metrics["runs"] == 0 and metrics["open_tasks"] == 0


async def test_eval_runs_cannot_be_resumed_from_the_dashboard(
    client, store, settings, transports, tmp_path
):
    record = await run_eval(store, settings, transports, suite(tmp_path, "refunded"))
    failed = next(r.run_id for r in store.get_eval_results(record.id) if r.case_id == "unknown")
    response = client.post(f"/api/runs/{failed}/resume")
    assert response.status_code == 409 and "eval" in response.json()["detail"]
