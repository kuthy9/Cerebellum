import asyncio

import httpx
import pytest

from cerebellum.ai.base import AIResult, Usage
from cerebellum.ai.mock import MockProvider
from cerebellum.errors import SpecError
from cerebellum.runtime.engine import Engine
from cerebellum.runtime.states import RunStatus, StepStatus
from cerebellum.spec import parse_workflow

API_YAML = """
name: api_flow
input:
  order_id: {type: string, required: true}
connectors:
  api: {type: rest, base_url: "http://api.test"}
steps:
  - id: call
    type: http
    connector: api
    method: POST
    path: /refunds
    body: {order_id: "{{ input.order_id }}"}
    retry: {max: 3, base: 1s}
    on_failure: {fallback: manual}
  - id: after
    type: validate
    needs: [call]
    rules:
      - {expr: "steps.call.status in ['succeeded', 'recovered']", message: call must finish}
fallbacks:
  - id: manual
    type: task
    title: "Manual handling for {{ input.order_id }} after {{ failure.step }}"
    assignee: ops
output:
  status_code: "{{ steps.call.output.status }}"
"""
NO_FALLBACK_YAML = API_YAML.replace("    on_failure: {fallback: manual}\n", "")


def wf(text, tmp_path):
    return parse_workflow(text, base_dir=tmp_path, env={})


def engine_for(store, settings, transports=None, ai=None):
    return Engine(
        store,
        settings,
        ai or MockProvider(latency=(0, 0)),
        http_transports=transports or {},
        jitter=0,
    )


class Responses:
    """Scripted handler: returns the queued responses in order, repeating the last one."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        status, body = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return httpx.Response(status, json=body)


def event_types(store, run_id, step_id=None):
    return [e.type for e in store.get_events(run_id) if step_id is None or e.step_id == step_id]


async def test_retry_then_success(store, settings, clock, tmp_path):
    handler = Responses((503, {"e": 1}), (503, {"e": 2}), (201, {"id": "rf_1"}))
    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(API_YAML, tmp_path), {"order_id": "A1"})

    assert run.status is RunStatus.SUCCEEDED
    assert run.output == {"status_code": 201}
    call = store.get_step(run.run_id, "call")
    assert call.attempts == 3 and call.output["body"] == {"id": "rf_1"}
    assert clock.sleeps == [1.0, 2.0]
    assert event_types(store, run.run_id, "call").count("step.retrying") == 2
    assert {r.headers["Idempotency-Key"] for r in handler.requests} == {f"{run.run_id}:call"}
    assert store.get_step(run.run_id, "manual").status is StepStatus.PENDING
    assert run.lease_owner is None
    assert event_types(store, run.run_id)[0] == "run.started"
    assert event_types(store, run.run_id)[-1] == "run.completed"


def test_a_run_records_where_its_rest_connectors_point_without_credentials(
    store, settings, tmp_path
):
    """The URLs a run's REST connectors resolved to when it started (later hints use them:
    the environment may differ by then), with user, password, query and fragment removed."""
    text = API_YAML.replace(
        'base_url: "http://api.test"', 'base_url: "https://user:pw@api.test:8443/v1?token=x#f"'
    )
    run = engine_for(store, settings).prepare(wf(text, tmp_path), {"order_id": "A1"})
    [started] = [e for e in store.get_events(run.run_id) if e.type == "run.started"]
    assert started.data["rest_urls"] == {"api": "https://api.test:8443/v1"}


async def test_non_retryable_failure_cancels_downstream(store, settings, clock, tmp_path):
    handler = Responses((404, {"detail": "nope"}))
    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(NO_FALLBACK_YAML, tmp_path), {"order_id": "A1"})

    assert run.status is RunStatus.FAILED
    assert "HTTP 404" in run.error
    assert store.get_step(run.run_id, "call").attempts == 1
    assert store.get_step(run.run_id, "after").status is StepStatus.CANCELLED
    assert clock.sleeps == []
    failed = [e for e in store.get_events(run.run_id) if e.type == "step.failed"][0]
    assert failed.data["kind"] == "http_status" and failed.data["retryable"] is False


async def test_fallback_recovers_and_marks_needs_attention(store, settings, clock, tmp_path):
    handler = Responses((503, {"detail": "down"}))
    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(API_YAML, tmp_path), {"order_id": "A1"})

    assert run.status is RunStatus.NEEDS_ATTENTION
    call = store.get_step(run.run_id, "call")
    assert call.status is StepStatus.RECOVERED
    assert call.output["task_id"].startswith("tk_")
    assert store.get_step(run.run_id, "manual").status is StepStatus.SUCCEEDED
    assert store.get_step(run.run_id, "after").status is StepStatus.SUCCEEDED
    tasks = store.list_tasks(run_id=run.run_id)
    assert [t.title for t in tasks] == ["Manual handling for A1 after call"]
    assert len(handler.requests) == 4 and clock.sleeps == [1.0, 2.0, 4.0]
    assert run.output == {"status_code": None}
    started = [
        e
        for e in store.get_events(run.run_id)
        if e.type == "step.started" and e.step_id == "manual"
    ]
    assert started[0].data["fallback_for"] == "call"


async def test_failing_fallback_leaves_run_failed(store, settings, tmp_path):
    text = """
name: double_fail
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: call, type: http, connector: api, path: /a, on_failure: {fallback: backup}}
fallbacks:
  - {id: backup, type: http, connector: api, path: /b}
"""
    engine = engine_for(store, settings, {"api": httpx.MockTransport(Responses((500, {})))})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.FAILED
    assert store.get_step(run.run_id, "call").status is StepStatus.FAILED
    assert store.get_step(run.run_id, "backup").status is StepStatus.FAILED


async def test_when_false_skips_and_downstream_still_runs(store, settings, tmp_path):
    text = """
name: branching
input: {vip: {type: boolean, required: true}}
steps:
  - {id: vip_only, type: task, when: "input.vip", title: VIP follow-up}
  - id: always
    type: validate
    needs: [vip_only]
    rules: [{expr: "steps.vip_only.output is none", message: skipped output is null}]
"""
    run = await engine_for(store, settings).start(wf(text, tmp_path), {"vip": False})
    assert run.status is RunStatus.SUCCEEDED
    assert store.get_step(run.run_id, "vip_only").status is StepStatus.SKIPPED
    assert store.get_step(run.run_id, "always").status is StepStatus.SUCCEEDED
    skipped = [e for e in store.get_events(run.run_id) if e.type == "step.skipped"][0]
    assert skipped.data["reason"] == "condition is false"


async def test_step_timeout_is_retryable(store, settings, tmp_path):
    text = """
name: slow
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: call, type: http, connector: api, path: /slow, timeout: 50ms, retry: {max: 1, base: 1s}}
"""

    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    engine = engine_for(store, settings, {"api": httpx.MockTransport(slow)})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.FAILED
    assert store.get_step(run.run_id, "call").attempts == 2
    failed = [e for e in store.get_events(run.run_id) if e.type == "step.failed"][0]
    assert failed.data["kind"] == "timeout"


async def test_parallel_steps_respect_max_parallel(store, settings, tmp_path):
    text = """
name: fanout
limits: {max_parallel: 2}
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: a, type: http, connector: api, path: /a}
  - {id: b, type: http, connector: api, path: /b}
  - {id: c, type: http, connector: api, path: /c}
  - {id: d, type: http, connector: api, path: /d}
  - id: join
    type: validate
    needs: [a, b, c, d]
    rules: [{expr: "true", message: ok}]
"""
    state = {"active": 0, "peak": 0}

    async def handler(request):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        return httpx.Response(200, json={"path": request.url.path})

    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.SUCCEEDED
    assert state["peak"] == 2


class CostlyAI:
    name = "costly"
    mock = False

    def __init__(self, cost):
        self.cost = cost
        self.calls = 0

    async def generate(self, request, messages):
        self.calls += 1
        return AIResult(
            text='{"ok": true}',
            model=request.model,
            usage=Usage(),
            cost_usd=self.cost,
            mock=False,
            stop_reason="end_turn",
            latency_ms=1.0,
        )


async def test_budget_exceeded_fails_without_retry_and_cancels_the_rest(store, settings, tmp_path):
    text = """
name: spendy
limits: {budget_usd: 0.5}
steps:
  - id: think
    type: ai
    prompt: Think
    output_schema: {type: object, required: [ok], properties: {ok: {type: boolean}}}
  - id: think_more
    type: ai
    needs: [think]
    prompt: More
    retry: {max: 2}
    output_schema: {type: object, required: [ok], properties: {ok: {type: boolean}}}
  - {id: final, type: task, needs: [think_more], title: done}
"""
    ai = CostlyAI(0.3)
    run = await engine_for(store, settings, ai=ai).start(wf(text, tmp_path))
    assert run.status is RunStatus.FAILED
    assert "budget" in run.error
    assert ai.calls == 2
    assert run.cost_usd == pytest.approx(0.6)
    assert store.get_step(run.run_id, "think_more").attempts == 1
    assert store.get_step(run.run_id, "final").status is StepStatus.CANCELLED


async def test_template_error_fails_without_retry(store, settings, tmp_path):
    """Review focus: a template that references a missing value fails clearly, once."""
    text = """
name: broken_template
connectors: {api: {type: rest, base_url: "http://api.test"}}
steps:
  - {id: call, type: http, connector: api, path: /x, body: {v: "{{ input.nope }}"}, retry: {max: 3}}
"""
    handler = Responses((200, {}))
    engine = engine_for(store, settings, {"api": httpx.MockTransport(handler)})
    run = await engine.start(wf(text, tmp_path))
    assert run.status is RunStatus.FAILED
    call = store.get_step(run.run_id, "call")
    assert call.attempts == 1 and "input.nope" in call.error
    assert handler.requests == []


async def test_bad_input_is_rejected_before_run_creation(store, settings, tmp_path):
    with pytest.raises(SpecError, match="input.order_id: expected string"):
        await engine_for(store, settings).start(wf(API_YAML, tmp_path), {"order_id": 123})
    assert store.list_runs() == []


async def test_unknown_param_is_rejected(store, settings, tmp_path):
    with pytest.raises(SpecError, match="params.nope"):
        await engine_for(store, settings).start(
            wf(API_YAML, tmp_path), {"order_id": "A1"}, {"nope": 1}
        )
