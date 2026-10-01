"""The dashboard server: JSON API, live event stream and the prebuilt UI."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Collection, Mapping
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Receive, Scope, Send

from cerebellum import __version__
from cerebellum.ai.base import AIProvider
from cerebellum.config import DEFAULT_UI_SHUTDOWN_GRACE_SECONDS, Settings
from cerebellum.errors import CerebellumError, NotFound, SpecError
from cerebellum.runtime.engine import load_run_workflow
from cerebellum.runtime.states import RunStatus
from cerebellum.runtime.store import EvalRunRecord, RunRecord, Store
from cerebellum.runtime.trace import build_spans
from cerebellum.server import serialize as js
from cerebellum.server.catalog import Catalog
from cerebellum.server.stream import event_stream
from cerebellum.server.worker import Worker
from cerebellum.spec.durations import parse_duration

STATIC_DIR = Path(__file__).parent / "static"
# Names of this machine; a dashboard bound to one of them only answers requests addressed to them.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# index.html names the hashed /assets files of the current build, so browsers must revalidate it
# (or a rebuilt UI would load the old assets); the hashed assets themselves may stay cached.
INDEX_HEADERS = {"Cache-Control": "no-cache"}
# How many runs of each suite the eval list and the eval detail return for their trend lines.
EVAL_HISTORY = 30
UI_MISSING = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Cerebellum</title></head>
<body style="background:#0a0b0d;color:#e3e6ea;font:14px ui-monospace,monospace;padding:48px">
<p>The dashboard UI has not been built.</p>
<p>Run <code>make ui</code> (Node 20.19+), then restart <code>cerebellum ui</code>.
The JSON API is available under <code>/api</code>.</p>
</body></html>
"""


def host_name(header: str) -> str:
    """The host part of a Host header: `[::1]:7400` → `::1`, `localhost:7400` → `localhost`."""
    value = header.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    name, sep, port = value.rpartition(":")
    return name if sep and port.isdigit() and ":" not in name else value


class HostGuard:
    """Refuse requests addressed to any other host name. A web page whose domain is re-pointed at
    127.0.0.1 (DNS rebinding) is same-origin with the dashboard, but its Host header is not."""

    def __init__(self, app: ASGIApp, allowed: Collection[str]):
        self.app = app
        self.allowed = frozenset(allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
            if host_name(host) not in self.allowed:
                await PlainTextResponse("unknown host", status_code=400)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def dashboard_server(app: FastAPI, *, host: str, port: int) -> uvicorn.Server:
    """The uvicorn server `cerebellum ui` runs. Event streams end as soon as it starts stopping,
    so open browser tabs never hold up Ctrl-C."""
    server = uvicorn.Server(uvicorn.Config(app, **serve_options(host=host, port=port)))
    app.state.stopping = lambda: server.should_exit
    return server


def serve_options(*, host: str, port: int) -> dict[str, Any]:
    """The uvicorn settings `cerebellum ui` serves the dashboard with. Open event streams never
    end on their own, so shutdown waits for them only briefly."""
    return {
        "host": host,
        "port": port,
        "log_level": "warning",
        "timeout_graceful_shutdown": DEFAULT_UI_SHUTDOWN_GRACE_SECONDS,
    }


class StartRun(BaseModel):
    workflow: str = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    approved: bool
    by: str = Field(max_length=80, pattern=r"\S")
    comment: str = Field("", max_length=2000)


class Resolution(BaseModel):
    by: str = Field(max_length=80, pattern=r"\S")
    note: str = Field("", max_length=2000)


def create_app(
    settings: Settings,
    *,
    provider: AIProvider,
    mode: str,
    workflows_dir: Path,
    mock_requested: bool = False,
    store: Store | None = None,
    http_transports: Mapping[str, httpx.AsyncBaseTransport] | None = None,
    static_dir: Path = STATIC_DIR,
    allowed_hosts: Collection[str] | None = None,
) -> FastAPI:
    """Build the dashboard app. Without `store`, the app opens and closes its own. With
    `allowed_hosts`, requests addressed to any other host name are refused. `mock_requested`
    tells the UI the mock AI was asked for, so it does not suggest setting an API key."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active = store if store is not None else Store(settings.db_path)
        worker = Worker(active, settings, provider, http_transports=http_transports)
        app.state.store = active
        app.state.worker = worker
        app.state.catalog = Catalog(active, workflows_dir)
        worker.start()
        try:
            yield
        finally:
            await worker.stop()
            if store is None:
                active.close()

    app = FastAPI(
        title="Cerebellum",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    if allowed_hosts is not None:
        app.add_middleware(HostGuard, allowed=allowed_hosts)

    @app.exception_handler(CerebellumError)
    async def cerebellum_error(request: Request, exc: CerebellumError) -> JSONResponse:
        if isinstance(exc, NotFound):
            return JSONResponse({"detail": str(exc)}, status_code=404)
        if isinstance(exc, SpecError):
            issues = [{"path": issue.path, "message": issue.message} for issue in exc.issues]
            return JSONResponse(
                {"detail": {"message": str(exc), "issues": issues}}, status_code=400
            )
        return JSONResponse({"detail": str(exc)}, status_code=409)

    def parts(request: Request) -> tuple[Store, Worker, Catalog]:
        state = request.app.state
        return state.store, state.worker, state.catalog

    def run_lookup(active: Store) -> Callable[[str], RunRecord]:
        cache: dict[str, RunRecord] = {}

        def get(run_id: str) -> RunRecord:
            if run_id not in cache:
                cache[run_id] = active.get_run(run_id)
            return cache[run_id]

        return get

    @app.get("/api/info")
    async def info() -> dict[str, Any]:
        return {
            "version": __version__,
            "mode": mode,
            "mock": provider.mock,
            "mock_requested": mock_requested,
            "model": settings.model,
        }

    @app.get("/api/runs")
    async def list_runs(request: Request, status: str | None = None, limit: int = 50):
        active, _, _ = parts(request)
        wanted: RunStatus | None = None
        if status:
            try:
                wanted = RunStatus(status)
            except ValueError as exc:
                raise HTTPException(400, f"unknown status {status!r}") from exc
        runs = active.list_runs(status=wanted, limit=min(max(limit, 1), 500), include_evals=False)
        return {"runs": [js.run_json(run, stale=active.is_stale(run)) for run in runs]}

    @app.post("/api/runs", status_code=201)
    async def start_run(body: StartRun, request: Request):
        _, worker, catalog = parts(request)
        try:
            entry = await asyncio.to_thread(catalog.get, body.workflow)
        except KeyError as exc:
            raise HTTPException(404, f"unknown workflow {body.workflow!r}") from exc
        record = worker.start_run(entry.workflow, body.input, body.params)
        return {"run": js.run_json(record)}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, request: Request):
        active, _, _ = parts(request)
        run = active.get_run(run_id)
        try:
            graph = js.graph_json(load_run_workflow(active, run))
        except SpecError:
            graph = None  # the snapshot needs env vars this server does not have
        return {
            "run": js.run_json(run, stale=active.is_stale(run)),
            "steps": [js.step_json(step) for step in active.get_steps(run_id).values()],
            "graph": graph,
            "approvals": [js.approval_json(a, run) for a in active.list_approvals(run_id=run_id)],
            "tasks": [js.task_json(t, run) for t in active.list_tasks(run_id=run_id)],
            "spans": [js.span_json(span) for span in build_spans(active.get_events(run_id))],
        }

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, after: int = 0):
        active, _, _ = parts(request)
        active.get_run(run_id)
        return {"events": [js.event_json(e) for e in active.get_events(run_id, after_seq=after)]}

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    async def resume_run(run_id: str, request: Request):
        _, worker, _ = parts(request)
        return {"run": js.run_json(worker.resume(run_id))}

    @app.get("/api/approvals")
    async def list_approvals(request: Request, status: str = "pending"):
        active, _, _ = parts(request)
        if status not in ("pending", "all"):
            raise HTTPException(400, "status must be 'pending' or 'all'")
        items = active.list_approvals(status="pending" if status == "pending" else None)
        if status == "all":
            items.reverse()  # newest first; the pending queue stays oldest first
        run = run_lookup(active)
        return {"approvals": [js.approval_json(a, run(a.run_id)) for a in items]}

    @app.post("/api/approvals/{approval_id}/decision")
    async def decide(approval_id: str, body: Decision, request: Request):
        active, worker, _ = parts(request)
        approval = await worker.decide(
            approval_id,
            approved=body.approved,
            by=body.by.strip(),
            comment=body.comment.strip(),
        )
        return {"approval": js.approval_json(approval, active.get_run(approval.run_id))}

    @app.get("/api/tasks")
    async def list_tasks(request: Request, status: str = "open"):
        active, _, _ = parts(request)
        if status not in ("open", "all"):
            raise HTTPException(400, "status must be 'open' or 'all'")
        items = active.list_tasks(status="open" if status == "open" else None)
        if status == "all":
            items.reverse()
        run = run_lookup(active)
        return {"tasks": [js.task_json(t, run(t.run_id)) for t in items]}

    @app.post("/api/tasks/{task_id}/resolve")
    async def resolve_task(task_id: str, body: Resolution, request: Request):
        active, _, _ = parts(request)
        task = active.resolve_task(task_id, by=body.by.strip(), note=body.note.strip())
        return {"task": js.task_json(task, active.get_run(task.run_id))}

    @app.get("/api/metrics")
    async def metrics(request: Request, window: str = "24h"):
        active, _, _ = parts(request)
        try:
            seconds = parse_duration(window)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {**active.metrics(active.clock.now() - seconds), "window": window}

    def eval_json(active: Store, record: EvalRunRecord) -> dict[str, Any]:
        # Stale: still "running" with no heartbeat for a lease period (its process died).
        stale = active.is_eval_stale(record, timeout=settings.lease_seconds)
        return js.eval_run_json(record, stale=stale)

    @app.get("/api/evals")
    async def list_evals(request: Request, suite: str | None = None, limit: int = EVAL_HISTORY):
        """The newest `limit` runs of each suite (or of `suite`), newest first."""
        active, _, _ = parts(request)
        limit = min(max(limit, 1), 500)
        if suite is None:
            records = active.list_eval_runs_per_suite(limit)
        else:
            records = active.list_eval_runs(suite=suite, limit=limit)
        return {"evals": [eval_json(active, record) for record in records]}

    @app.get("/api/evals/{eval_run_id}")
    async def eval_detail(eval_run_id: str, request: Request):
        active, _, _ = parts(request)
        record = active.get_eval_run(eval_run_id)
        baseline = None
        if record.baseline_id:
            with contextlib.suppress(NotFound):
                baseline = active.get_eval_run(record.baseline_id)
        history = active.list_eval_runs(suite=record.suite, limit=EVAL_HISTORY)
        history.reverse()
        return {
            "eval": eval_json(active, record),
            "baseline": None if baseline is None else eval_json(active, baseline),
            "results": [js.eval_result_json(r) for r in active.get_eval_results(eval_run_id)],
            "history": [eval_json(active, r) for r in history],
        }

    # The catalog walks the --workflows tree and parses what changed: in a worker thread, so a
    # large tree never stalls event streams or running steps.
    @app.get("/api/workflows")
    async def list_workflows(request: Request):
        _, _, catalog = parts(request)
        entries = await asyncio.to_thread(catalog.entries)
        return {"workflows": [js.workflow_summary(entry) for entry in entries]}

    @app.get("/api/workflows/{workflow_id:path}")
    async def workflow_detail(workflow_id: str, request: Request):
        _, _, catalog = parts(request)
        try:
            entry = await asyncio.to_thread(catalog.get, workflow_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown workflow {workflow_id!r}") from exc
        return js.workflow_detail(entry)

    @app.get("/api/stream")
    async def stream(request: Request, after: int | None = None) -> StreamingResponse:
        active, _, _ = parts(request)
        last = request.headers.get("last-event-id", "")
        if after is None:
            after = int(last) if last.isdigit() else active.last_seq()
        stopping: Callable[[], bool] = getattr(request.app.state, "stopping", lambda: False)

        async def finished() -> bool:
            return stopping() or await request.is_disconnected()

        return StreamingResponse(
            event_stream(
                active,
                after=after,
                poll=settings.stream_poll,
                is_disconnected=finished,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def ui(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "not found")
        root = static_dir.resolve()
        index = root / "index.html"
        try:
            candidate = (root / path).resolve()
        except ValueError:
            candidate = None  # a name no file can have, e.g. one with a NUL byte
        found = path and candidate and candidate.is_file() and root in candidate.parents
        if found and candidate != index:
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, headers=INDEX_HEADERS)
        return HTMLResponse(UI_MISSING)

    return app
