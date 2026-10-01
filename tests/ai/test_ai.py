import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from cerebellum.ai import ProviderChoice, select_provider
from cerebellum.ai.anthropic_provider import REFUSAL_FALLBACK_BETA, AnthropicProvider
from cerebellum.ai.base import AIError, AIRequest, Usage
from cerebellum.ai.mock import MockProvider
from cerebellum.ai.pricing import DEFAULT_PRICES, ModelPrice, Pricing
from cerebellum.config import Settings
from cerebellum.spec.models import MockRule

SCHEMA = {
    "type": "object",
    "required": ["eligible", "risk"],
    "additionalProperties": False,
    "properties": {"eligible": {"type": "boolean"}, "risk": {"enum": ["low", "high"]}},
}


def test_default_prices_include_current_models():
    assert DEFAULT_PRICES["claude-opus-5-5"] == ModelPrice(4.0, 20.0, cache_read=0.20)
    assert DEFAULT_PRICES["claude-sonnet-5-5"].input == 2.0
    assert DEFAULT_PRICES["claude-haiku-4-5"].output == 5.0


def test_pricing_cost():
    pricing = Pricing()
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert pricing.cost("claude-opus-5-5", usage) == pytest.approx(24.0)
    cached = Usage(cache_read_input_tokens=1_000_000, cache_creation_input_tokens=1_000_000)
    assert pricing.cost("claude-opus-5-5", cached) == pytest.approx(0.20 + 5.0)
    assert pricing.cost("unknown-model", usage) == 0.0


def test_pricing_file_override(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(
        json.dumps(
            {"claude-opus-5-5": {"input": 1, "output": 2}, "custom": {"input": 3, "output": 4}}
        )
    )
    pricing = Pricing.load(path)
    assert pricing.cost("claude-opus-5-5", Usage(input_tokens=1_000_000)) == pytest.approx(1.0)
    assert pricing.cost("custom", Usage(output_tokens=1_000_000)) == pytest.approx(4.0)
    assert Pricing.load(None).prices == DEFAULT_PRICES


def request(**overrides):
    base = dict(
        model="claude-opus-5-5",
        prompt="Assess",
        schema=SCHEMA,
        context={"input": {"reason": "Fraud attempt", "amount": 900}, "params": {"t": 500}},
    )
    base.update(overrides)
    return AIRequest(**base)


async def test_mock_picks_first_matching_rule_and_renders_templates():
    rules = (
        MockRule(
            when="'fraud' in input.reason | lower",
            output={"eligible": False, "risk": "high", "note": "{{ input.amount }}"},
        ),
        MockRule(output={"eligible": True, "risk": "low"}),
    )
    result = await MockProvider(latency=(0, 0)).generate(request(mock_rules=rules), [])
    assert json.loads(result.text) == {"eligible": False, "risk": "high", "note": 900}
    assert result.mock is True and result.cost_usd == 0.0 and result.usage == Usage()

    calm = request(mock_rules=rules, context={"input": {"reason": "late", "amount": 1}})
    calm_result = await MockProvider(latency=(0, 0)).generate(calm, [])
    assert json.loads(calm_result.text)["risk"] == "low"


async def test_mock_without_rules_returns_minimal_valid_instance():
    result = await MockProvider(latency=(0, 0)).generate(request(), [])
    assert json.loads(result.text) == {"eligible": False, "risk": "low"}


async def test_mock_simulates_latency_through_injected_sleep():
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    await MockProvider(latency=(0.1, 0.2), sleep=fake_sleep).generate(request(), [])
    assert len(delays) == 1 and 0.1 <= delays[0] <= 0.2


class FakeMessages:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def create(self, **params):
        self.calls.append(params)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def fake_client(outcome):
    messages = FakeMessages(outcome)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def message(
    stop_reason="end_turn",
    text='{"eligible": true, "risk": "low"}',
    model="claude-opus-5-5",
    stop_details=None,
):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        model=model,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=0,
        ),
    )


async def test_anthropic_success_builds_structured_output_request_and_prices_usage():
    client, messages = fake_client(message())
    provider = AnthropicProvider(client)
    history = [{"role": "user", "content": "Assess"}]
    result = await provider.generate(request(system="Be strict", effort="low"), history)
    params = messages.calls[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["messages"] == history
    assert params["system"] == "Be strict"
    assert params["output_config"] == {
        "format": {"type": "json_schema", "schema": SCHEMA},
        "effort": "low",
    }
    assert params["betas"] == [REFUSAL_FALLBACK_BETA] and params["fallbacks"] == "default"
    assert result.text == '{"eligible": true, "risk": "low"}'
    assert result.usage == Usage(input_tokens=1000, output_tokens=200)
    assert result.cost_usd == pytest.approx((1000 * 4 + 200 * 20) / 1_000_000)
    assert result.mock is False and result.model == "claude-opus-5-5"


def test_refusal_fallbacks_only_for_supported_models():
    provider = AnthropicProvider(fake_client(message())[0])
    params = provider.build_params(request(model="claude-haiku-4-5"), [])
    assert "betas" not in params and "fallbacks" not in params
    assert "system" not in params and "effort" not in params["output_config"]
    off = AnthropicProvider(fake_client(message())[0], refusal_fallbacks=False)
    assert "fallbacks" not in off.build_params(request(), [])


async def test_refusal_is_not_retryable_and_keeps_category():
    client, _ = fake_client(
        message(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    )
    with pytest.raises(AIError, match="refusal category: cyber") as info:
        await AnthropicProvider(client).generate(request(), [])
    assert info.value.retryable is False and info.value.kind == "refusal"
    assert info.value.details == {"category": "cyber"}


async def test_max_tokens_is_not_retryable():
    client, _ = fake_client(message(stop_reason="max_tokens"))
    with pytest.raises(AIError, match="max_tokens") as info:
        await AnthropicProvider(client).generate(request(), [])
    assert info.value.retryable is False


def _api_error(cls, status):
    url = "https://api.anthropic.com/v1/messages"
    response = httpx2.Response(status, request=httpx2.Request("POST", url))
    return cls("boom", response=response, body=None)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (_api_error(anthropic.RateLimitError, 429), True),
        (_api_error(anthropic.InternalServerError, 500), True),
        (_api_error(anthropic.BadRequestError, 400), False),
        (_api_error(anthropic.AuthenticationError, 401), False),
        (
            anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com")
            ),
            True,
        ),
    ],
)
async def test_api_errors_are_classified(error, retryable):
    client, _ = fake_client(error)
    with pytest.raises(AIError) as info:
        await AnthropicProvider(client).generate(request(), [])
    assert info.value.retryable is retryable


def test_select_provider(tmp_path):
    settings = Settings.from_env({"CEREBELLUM_HOME": str(tmp_path)})
    empty_profile = tmp_path / "no-profile"
    key = {"ANTHROPIC_API_KEY": "k"}
    forced = select_provider(settings, force_mock=True, env=key, config_dir=empty_profile)
    assert isinstance(forced, ProviderChoice)
    assert forced.provider.mock and "requested" in forced.reason
    env_forced = select_provider(
        Settings.from_env({"CEREBELLUM_MOCK": "1"}), env=key, config_dir=empty_profile
    )
    assert env_forced.provider.mock
    real = select_provider(settings, env=key, config_dir=empty_profile)
    assert isinstance(real.provider, AnthropicProvider) and "claude-opus-5-5" in real.reason
    fallback = select_provider(settings, env={}, config_dir=empty_profile)
    assert fallback.provider.mock and "no Anthropic credentials" in fallback.reason


@pytest.mark.live
async def test_live_claude_structured_output():
    prompt = 'Return {"eligible": true, "risk": "low"}.'
    result = await AnthropicProvider().generate(
        request(prompt=prompt), [{"role": "user", "content": prompt}]
    )
    assert json.loads(result.text)["risk"] in ("low", "high")
