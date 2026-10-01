import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from cerebellum.ai.mock import MockProvider
from cerebellum.runtime.engine import Engine
from cerebellum.sandbox.payments import FailMode, PaymentsState, create_payments_app
from cerebellum.server.app import create_app
from cerebellum.server.catalog import Catalog
from cerebellum.spec import parse_workflow
from cerebellum.templates import template_path

WORKFLOW_ID = "workflow.yaml"  # the packaged refund template, scanned from its own directory


def sample(name):
    path = template_path("refund") / "inputs" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def payments():
    return PaymentsState()


@pytest.fixture
def client(store, settings, payments):
    app = create_app(
        settings,
        provider=MockProvider(latency=(0, 0)),
        mode="mock AI (requested)",
        mock_requested=True,
        workflows_dir=template_path("refund"),
        store=store,
        http_transports={"payments": httpx.ASGITransport(app=create_payments_app(payments))},
    )
    with TestClient(app) as test_client:
        yield test_client


def eventually(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached in time")


def settled(store, run_id, status):
    def check():
        run = store.get_run(run_id)
        return run.status.value == status and run.lease_owner is None

    eventually(check)
    return store.get_run(run_id)


def start(client, name):
    response = client.post("/api/runs", json={"workflow": WORKFLOW_ID, "input": sample(name)})
    assert response.status_code == 201, response.text
    return response.json()["run"]["run_id"]


class FakeClaude:
    """Stands in for the Claude provider; the workflow below never calls it."""

    name = "claude"
    mock = False

    async def generate(self, request, messages):  # pragma: no cover - not used
        raise AssertionError("not expected")


SIGN_OFF = """
name: sign_off
steps:
  - {id: gate, type: approval, title: Sign off}
  - {id: note, type: validate, needs: [gate], rules: [{expr: "true", message: ok}]}
"""


def test_a_mock_dashboard_refuses_to_continue_a_claude_run(client, store, settings, tmp_path):
    """Review finding: the dashboard on mock AI silently continued Claude runs on it."""
    workflow = parse_workflow(SIGN_OFF, base_dir=tmp_path, env={})
    run = asyncio.run(Engine(store, settings, FakeClaude()).start(workflow))
    [pending] = store.list_approvals(run_id=run.run_id)
    decided = client.post(
        f"/api/approvals/{pending.id}/decision", json={"approved": True, "by": "ui-user"}
    )
    assert decided.status_code == 409, decided.text
    assert "started with the Claude API" in decided.json()["detail"]
    resumed = client.post(f"/api/runs/{run.run_id}/resume")
    assert resumed.status_code == 409, resumed.text
    assert store.get_approval(pending.id).status == "pending"


NEEDS_ENV = """
name: needs_env
connectors:
  api: {type: rest, base_url: "${CEREBELLUM_TEST_API_URL}"}
steps:
  - {id: gate, type: approval, title: Go ahead}
  - {id: call, type: http, needs: [gate], connector: api, method: GET, path: /ping}
"""


def test_run_detail_shows_the_graph_when_connector_variables_are_unset(
    client, store, settings, tmp_path, monkeypatch
):
    """Review finding: the run detail parsed the snapshot as if to drive it and dropped the step
    graph when a connector ${VAR} was unset in the dashboard's environment."""
    monkeypatch.setenv("CEREBELLUM_TEST_API_URL", "http://127.0.0.1:9")
    workflow = parse_workflow(NEEDS_ENV, base_dir=tmp_path)
    run = asyncio.run(Engine(store, settings, MockProvider(latency=(0, 0))).start(workflow))
    monkeypatch.delenv("CEREBELLUM_TEST_API_URL")
    detail = client.get(f"/api/runs/{run.run_id}")
    assert detail.status_code == 200, detail.text
    graph = detail.json()["graph"]
    assert graph is not None
    assert [step["id"] for step in graph["steps"]] == ["gate", "call"]


def test_info_reports_mock_mode(client):
    info = client.get("/api/info").json()
    assert info["mock"] is True and info["mode"] == "mock AI (requested)"
    assert info["mock_requested"] is True
    assert info["version"] == "0.2.0"


def test_info_tells_a_mock_fallback_from_a_requested_mock(store, settings, tmp_path):
    app = create_app(
        settings,
        provider=MockProvider(latency=(0, 0)),
        mode="mock AI (no Anthropic credentials found)",
        workflows_dir=tmp_path,
        store=store,
    )
    with TestClient(app) as c:
        info = c.get("/api/info").json()
    assert info["mock"] is True and info["mock_requested"] is False


def test_small_refund_runs_in_the_background(client, store):
    run_id = start(client, "small")
    settled(store, run_id, "succeeded")
    [listed] = client.get("/api/runs").json()["runs"]
    assert listed["run_id"] == run_id and listed["status"] == "succeeded"
    assert listed["stale"] is False and listed["duration_s"] is not None
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["output"]["decision"] == "refunded"
    assert [s["step_id"] for s in detail["steps"]][:2] == ["fetch_order", "policy_check"]
    assert len(detail["steps"]) == 7
    assert detail["graph"]["steps"][-1]["id"] == "mark_refunded"
    assert any(span["label"] == "POST /refunds → 201" for span in detail["spans"])
    events = client.get(f"/api/runs/{run_id}/events").json()["events"]
    assert events[0]["type"] == "run.started"
    later = client.get(f"/api/runs/{run_id}/events", params={"after": events[-2]["seq"]})
    assert [e["seq"] for e in later.json()["events"]] == [events[-1]["seq"]]


def test_approval_flow_over_the_api(client, store):
    run_id = start(client, "large")
    settled(store, run_id, "waiting_approval")
    [approval] = client.get("/api/approvals").json()["approvals"]
    assert approval["run_id"] == run_id and approval["workflow_name"] == "refund_request"
    assert approval["context"]["input"]["amount"] == 899
    url = f"/api/approvals/{approval['id']}/decision"
    decided = client.post(url, json={"approved": True, "by": "  dana ", "comment": "fine"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["approval"]["decided_by"] == "dana"
    again = client.post(url, json={"approved": False, "by": "erin"})
    assert again.status_code == 409 and "already approved" in again.json()["detail"]
    settled(store, run_id, "succeeded")
    assert client.get("/api/approvals").json()["approvals"] == []
    [decided_one] = client.get("/api/approvals", params={"status": "all"}).json()["approvals"]
    assert decided_one["status"] == "approved"


def test_decisions_need_a_name_and_an_existing_approval(client, store):
    run_id = start(client, "large")
    settled(store, run_id, "waiting_approval")
    [approval] = client.get("/api/approvals").json()["approvals"]
    url = f"/api/approvals/{approval['id']}/decision"
    assert client.post(url, json={"approved": True, "by": "   "}).status_code == 422
    missing = client.post("/api/approvals/ap_missing/decision", json={"approved": True, "by": "x"})
    assert missing.status_code == 404


def test_outage_opens_a_task_that_can_be_resolved(client, store, payments):
    payments.set_fail_mode(FailMode.parse("always"))
    run_id = start(client, "outage")
    settled(store, run_id, "needs_attention")
    [task] = client.get("/api/tasks").json()["tasks"]
    assert task["assignee"] == "finance-ops" and task["workflow_name"] == "refund_request"
    url = f"/api/tasks/{task['id']}/resolve"
    resolved = client.post(url, json={"by": "ops", "note": "paid by hand"})
    assert resolved.status_code == 200 and resolved.json()["task"]["status"] == "resolved"
    assert client.get("/api/tasks").json()["tasks"] == []
    assert len(client.get("/api/tasks", params={"status": "all"}).json()["tasks"]) == 1
    assert client.post(url, json={"by": "ops"}).status_code == 409
    assert client.post("/api/tasks/tk_missing/resolve", json={"by": "ops"}).status_code == 404


def test_bad_requests_are_explained(client):
    body = {"workflow": WORKFLOW_ID, "input": {"order_id": "A1001", "amount": "120"}}
    bad = client.post("/api/runs", json=body)
    assert bad.status_code == 400
    assert any(issue["path"] == "input.amount" for issue in bad.json()["detail"]["issues"])
    assert client.post("/api/runs", json={"workflow": "nope.yaml"}).status_code == 404
    assert client.get("/api/runs/r_00000000").status_code == 404
    assert client.get("/api/runs", params={"status": "bogus"}).status_code == 400
    assert client.get("/api/metrics", params={"window": "soon"}).status_code == 400
    assert client.get("/api/approvals", params={"status": "maybe"}).status_code == 400
    assert client.get("/api/tasks", params={"status": "maybe"}).status_code == 400
    assert client.get("/api/does-not-exist").status_code == 404


def test_resume_over_the_api(client, store):
    done = start(client, "small")
    settled(store, done, "succeeded")
    assert client.post(f"/api/runs/{done}/resume").status_code == 409
    body = {"workflow": WORKFLOW_ID, "input": {"order_id": "ZZZ", "amount": 10}}
    failed = client.post("/api/runs", json=body).json()["run"]["run_id"]
    settled(store, failed, "failed")
    assert client.post(f"/api/runs/{failed}/resume").status_code == 202
    eventually(lambda: store.get_step(failed, "fetch_order").attempts == 2)
    settled(store, failed, "failed")


def test_metrics_and_workflows(client, store):
    run_id = start(client, "small")
    settled(store, run_id, "succeeded")
    metrics = client.get("/api/metrics").json()
    assert metrics["runs"] == 1 and metrics["success_rate"] == 1.0
    assert metrics["window"] == "24h"
    [workflow] = client.get("/api/workflows").json()["workflows"]
    assert workflow["id"] == WORKFLOW_ID and workflow["source"] == "file"
    assert workflow["runs"] == 1 and workflow["name"] == "refund_request"
    detail = client.get(f"/api/workflows/{WORKFLOW_ID}").json()
    assert set(detail["samples"]) == {"small", "large", "flaky", "outage", "fraud"}
    assert detail["yaml"].startswith("name: refund_request")
    assert detail["params"] == {"approval_threshold": 500}
    assert client.get("/api/workflows/missing.yaml").status_code == 404


def test_the_workflow_catalog_is_scanned_off_the_event_loop(client, store, monkeypatch):
    scanned_on = []
    real_entries = Catalog.entries

    def entries(self):
        try:
            asyncio.get_running_loop()
            scanned_on.append("event loop")
        except RuntimeError:
            scanned_on.append("thread")
        return real_entries(self)

    monkeypatch.setattr(Catalog, "entries", entries)
    assert client.get("/api/workflows").status_code == 200
    assert client.get(f"/api/workflows/{WORKFLOW_ID}").status_code == 200
    settled(store, start(client, "small"), "succeeded")
    assert scanned_on == ["thread", "thread", "thread"]


def make_static(tmp_path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (static / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    return static


def app_with(store, settings, tmp_path, static_dir):
    return create_app(
        settings,
        provider=MockProvider(latency=(0, 0)),
        mode="mock AI (requested)",
        workflows_dir=tmp_path,
        store=store,
        static_dir=static_dir,
    )


def test_serves_the_ui_with_an_spa_fallback(store, settings, tmp_path):
    with TestClient(app_with(store, settings, tmp_path, make_static(tmp_path))) as c:
        assert "<title>ui</title>" in c.get("/").text
        assert "<title>ui</title>" in c.get("/runs/r_12345678").text
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/favicon.svg").text == "<svg/>"
        assert "secret" not in c.get("/..%2fsecret.txt").text
        assert c.get("/api/nope").status_code == 404


def test_the_index_is_revalidated_so_a_rebuilt_ui_is_picked_up(store, settings, tmp_path):
    with TestClient(app_with(store, settings, tmp_path, make_static(tmp_path))) as c:
        for path in ("/", "/index.html", "/runs/r_12345678"):
            assert c.get(path).headers.get("cache-control") == "no-cache", path


def test_every_path_to_the_index_is_revalidated(store, settings, tmp_path):
    """Review finding: on a case-insensitive file system /INDEX.HTML is index.html, but it was
    served as an ordinary static file, without Cache-Control: no-cache."""
    with TestClient(app_with(store, settings, tmp_path, make_static(tmp_path))) as c:
        for path in ("/INDEX.HTML", "/Index.html", "/./index.html"):
            page = c.get(path)
            assert "<title>ui</title>" in page.text, path
            assert page.headers.get("cache-control") == "no-cache", path


def test_head_requests_to_the_ui_are_answered(store, settings, tmp_path):
    """Review finding: HEAD / answered 405 because the UI route only accepted GET."""
    with TestClient(app_with(store, settings, tmp_path, make_static(tmp_path))) as c:
        for path in ("/", "/index.html", "/runs/r_12345678"):
            page = c.head(path)
            assert page.status_code == 200, (path, page.status_code)
            assert page.headers.get("cache-control") == "no-cache", path
            assert page.headers["content-type"].startswith("text/html"), path
        favicon = c.head("/favicon.svg")
        assert favicon.status_code == 200 and "cache-control" not in favicon.headers
        assert c.head("/assets/app.js").status_code == 200
        for path in ("/api/info", "/api/nope"):  # HEAD is for the UI; the JSON API stays GET-only
            assert c.head(path).status_code == 405, path
    with TestClient(app_with(store, settings, tmp_path, tmp_path / "not-built")) as c:
        assert c.head("/").status_code == 200


def test_paths_the_filesystem_rejects_fall_back_to_the_ui(store, settings, tmp_path):
    app = app_with(store, settings, tmp_path, make_static(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as c:
        page = c.get("/%00")  # a NUL byte: no file can have that name
        assert page.status_code == 200 and "<title>ui</title>" in page.text


def test_over_long_paths_fall_back_to_the_ui(store, settings, tmp_path):
    """Review finding: a segment longer than any file name (ENAMETOOLONG) answered 500."""
    app = app_with(store, settings, tmp_path, make_static(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as c:
        for path in ("/" + "a" * 5000, "/runs/" + "b" * 5000 + "/steps"):
            page = c.get(path)
            assert page.status_code == 200 and "<title>ui</title>" in page.text, page.status_code


def test_explains_how_to_build_a_missing_ui(store, settings, tmp_path):
    with TestClient(app_with(store, settings, tmp_path, tmp_path / "not-built")) as c:
        page = c.get("/")
        assert page.status_code == 200 and "make ui" in page.text
