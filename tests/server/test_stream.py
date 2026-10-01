import json
import threading
import time

import httpx
import pytest

from cerebellum.ai.mock import MockProvider
from cerebellum.config import DEFAULT_UI_SHUTDOWN_GRACE_SECONDS
from cerebellum.runtime.states import StepStatus
from cerebellum.runtime.store import Store
from cerebellum.server.app import create_app, dashboard_server
from cerebellum.server.stream import event_stream


def fields(frame):
    return dict(line.split(": ", 1) for line in frame.strip().split("\n"))


async def test_event_stream_yields_new_events_in_order(store, simple_workflow):
    store.save_workflow(simple_workflow)
    store.create_run("r_s0000001", simple_workflow, {}, {}, mock=True)
    store.step_transition("r_s0000001", "first", StepStatus.RUNNING, event="started")
    checks = iter([False, False, True])

    async def disconnected():
        return next(checks)

    frames = [f async for f in event_stream(store, after=0, poll=0, is_disconnected=disconnected)]
    assert frames[0] == "retry: 2000\n\n"
    parsed = [fields(frame) for frame in frames[1:]]
    assert [json.loads(p["data"])["type"] for p in parsed] == ["run.started", "step.started"]
    assert [int(p["id"]) for p in parsed] == [1, 2]


async def test_event_stream_sends_keepalives_while_idle(store):
    checks = iter([False, False, False, True])

    async def disconnected():
        return next(checks)

    frames = [
        f
        async for f in event_stream(
            store, after=0, poll=0, is_disconnected=disconnected, keepalive_every=2
        )
    ]
    assert frames == ["retry: 2000\n\n", ": keep-alive\n\n"]


def serve(settings, port, workflows_dir):
    """Run the dashboard the way `cerebellum ui` does, in a background thread."""
    app = create_app(
        settings,
        provider=MockProvider(latency=(0, 0)),
        mode="mock AI (requested)",
        workflows_dir=workflows_dir,
    )
    server = dashboard_server(app, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    return server, thread


@pytest.fixture
def live_server(settings, free_port, tmp_path):
    server, thread = serve(settings, free_port, tmp_path)
    yield f"http://127.0.0.1:{free_port}"
    server.should_exit = True
    thread.join(timeout=10)


def test_shutdown_does_not_wait_for_open_event_streams(settings, free_port, tmp_path):
    """Review finding: an open dashboard tab (its EventSource never ends) kept `cerebellum ui`
    from stopping on Ctrl-C. Streams end by themselves once the server stops, well inside the
    grace period, so nothing has to be cancelled (which uvicorn logs as an ASGI exception)."""
    server, thread = serve(settings, free_port, tmp_path)
    with httpx.stream("GET", f"http://127.0.0.1:{free_port}/api/stream", timeout=10) as response:
        lines = response.iter_lines()  # keep a reference: closing the iterator closes the stream
        assert next(lines) == "retry: 2000"
        server.should_exit = True  # what Ctrl-C does
        thread.join(timeout=DEFAULT_UI_SHUTDOWN_GRACE_SECONDS * 0.75)
        assert not thread.is_alive(), "the server is still waiting for the open stream"
        assert [line for line in lines if line] == []  # the stream reached its end


def first_event(response):
    for line in response.iter_lines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError("the stream ended without an event")


def test_stream_pushes_events_written_by_another_process(live_server, settings, simple_workflow):
    with Store(settings.db_path) as writer:
        with httpx.stream("GET", f"{live_server}/api/stream", timeout=10) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            writer.save_workflow(simple_workflow)
            writer.create_run("r_sse00001", simple_workflow, {}, {}, mock=True)
            event = first_event(response)
    assert event["type"] == "run.started" and event["run_id"] == "r_sse00001"


def test_stream_resumes_after_last_event_id(live_server, settings, simple_workflow):
    """Review focus: an EventSource reconnect continues exactly after the last event it saw."""
    with Store(settings.db_path) as writer:
        writer.save_workflow(simple_workflow)
        writer.create_run("r_sse00002", simple_workflow, {}, {}, mock=True)
        writer.step_transition("r_sse00002", "first", StepStatus.RUNNING, event="started")
        first, second = writer.get_events("r_sse00002")
        headers = {"Last-Event-ID": str(first.seq)}
        with httpx.stream("GET", f"{live_server}/api/stream", headers=headers, timeout=10) as r:
            event = first_event(r)
    assert event["seq"] == second.seq and event["type"] == "step.started"
