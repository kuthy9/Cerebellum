import json

import pytest

from cerebellum.ai.base import AIResult, Usage
from cerebellum.authoring import (
    DRAFT_SCHEMA,
    DraftError,
    build_prompt,
    draft_workflow,
    workflow_json_schema,
)

VALID = """\
name: invoice_approval
input:
  amount: {type: number, required: true}
steps:
  - id: check_amount
    type: validate
    rules:
      - {expr: "input.amount > 0", message: Amount must be positive}
  - id: finance_approval
    type: approval
    needs: [check_amount]
    when: "input.amount > 1000"
    title: "Approve invoice of {{ input.amount }}"
"""
INVALID = "name: invoice_approval\nsteps: []\n"


class ScriptedProvider:
    name = "scripted"
    mock = False

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def generate(self, request, messages):
        self.calls.append((request, [dict(m) for m in messages]))
        return AIResult(
            text=self.replies.pop(0),
            model=request.model,
            usage=Usage(),
            cost_usd=0.01,
            mock=False,
            stop_reason="end_turn",
            latency_ms=1.0,
        )


def reply(yaml_text, summary="Approves invoices."):
    return json.dumps({"yaml": yaml_text, "summary": summary})


async def test_valid_draft_is_returned_on_the_first_attempt(tmp_path):
    provider = ScriptedProvider(reply(VALID))
    draft = await draft_workflow(
        provider, "Approve invoices over $1000", model="claude-opus-5-5", base_dir=tmp_path, env={}
    )
    assert draft.workflow.name == "invoice_approval" and draft.attempts == 1
    assert draft.summary == "Approves invoices." and draft.cost_usd == pytest.approx(0.01)
    request, messages = provider.calls[0]
    assert request.schema == DRAFT_SCHEMA and request.model == "claude-opus-5-5"
    assert "Approve invoices over $1000" in messages[0]["content"]


async def test_invalid_draft_is_repaired_from_the_loader_issues(tmp_path):
    provider = ScriptedProvider(reply(INVALID), "not json", reply("```yaml\n" + VALID + "```"))
    draft = await draft_workflow(provider, "Approve invoices", model="m", base_dir=tmp_path, env={})
    assert draft.attempts == 3 and draft.cost_usd == pytest.approx(0.03)
    assert draft.yaml.startswith("name: invoice_approval")
    second = provider.calls[1][1]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert "failed validation" in second[2]["content"] and "steps" in second[2]["content"]
    third = provider.calls[2][1]
    assert "requested JSON object" in third[4]["content"]


async def test_draft_gives_up_after_the_attempt_budget(tmp_path):
    provider = ScriptedProvider(reply(INVALID), reply(INVALID), reply(INVALID))
    with pytest.raises(DraftError) as exc:
        await draft_workflow(provider, "x", model="m", base_dir=tmp_path, env={})
    assert exc.value.attempts == 3 and exc.value.issues[0].path == "steps"
    assert len(provider.calls) == 3


def test_prompt_carries_schema_step_guide_and_example():
    prompt = build_prompt("  Refund customers  ")
    assert "<process>\nRefund customers\n</process>" in prompt
    assert "approval: title, show" in prompt and "name: refund_request" in prompt
    schema = workflow_json_schema()
    assert {"name", "steps", "connectors", "fallbacks"} <= set(schema["properties"])
    assert not {"source_yaml", "base_dir", "digest"} & set(schema["properties"])
    assert json.dumps(schema, separators=(",", ":")) in prompt


@pytest.mark.live
async def test_claude_drafts_a_valid_workflow(tmp_path):
    from cerebellum.ai import AnthropicProvider

    draft = await draft_workflow(
        AnthropicProvider(),
        "When a customer cancels a subscription, look the account up in PostgreSQL, ask a "
        "manager to approve refunds over $200, call the billing API to cancel, and open a "
        "manual task if billing keeps failing.",
        model="claude-opus-5-5",
        base_dir=tmp_path,
    )
    assert draft.workflow.steps
