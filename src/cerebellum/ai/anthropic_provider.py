"""Claude provider using native structured outputs (output_config.format = json_schema)."""

from __future__ import annotations

import time
from typing import Any

import anthropic

from cerebellum.ai.base import AIError, AIRequest, AIResult, Usage
from cerebellum.ai.pricing import Pricing

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept the server-side refusal fallback ("default" routing).
REFUSAL_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


class AnthropicProvider:
    name = "anthropic"
    mock = False

    def __init__(
        self,
        client: Any | None = None,
        *,
        pricing: Pricing | None = None,
        refusal_fallbacks: bool = True,
    ):
        # The engine owns retries (with backoff and tracing), so the SDK must not retry silently.
        self._client = client if client is not None else anthropic.AsyncAnthropic(max_retries=0)
        self._pricing = pricing or Pricing()
        self._refusal_fallbacks = refusal_fallbacks

    def build_params(self, request: AIRequest, messages: list[dict[str, Any]]) -> dict[str, Any]:
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": request.schema}
        }
        if request.effort:
            output_config["effort"] = request.effort
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
            "output_config": output_config,
        }
        if request.system:
            params["system"] = request.system
        if self._refusal_fallbacks and request.model in REFUSAL_FALLBACK_MODELS:
            params["betas"] = [REFUSAL_FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    async def generate(self, request: AIRequest, messages: list[dict[str, Any]]) -> AIResult:
        params = self.build_params(request, messages)
        started = time.perf_counter()
        try:
            message = await self._client.beta.messages.create(**params)
        except anthropic.APIStatusError as exc:
            raise AIError(
                f"Claude API error {exc.status_code}: {exc.message}",
                retryable=exc.status_code in _RETRYABLE_STATUS,
                kind="api",
                details={"status": exc.status_code},
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise AIError(
                f"cannot reach the Claude API: {exc}", retryable=True, kind="connection"
            ) from exc
        latency = (time.perf_counter() - started) * 1000

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise AIError(
                f"Claude declined the request (refusal category: {category or 'unspecified'})",
                retryable=False,
                kind="refusal",
                details={"category": category},
            )
        if message.stop_reason == "max_tokens":
            raise AIError(
                f"output truncated at max_tokens={request.max_tokens}; "
                "raise max_tokens on the step",
                retryable=False,
                kind="max_tokens",
            )

        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        raw = message.usage
        usage = Usage(
            input_tokens=raw.input_tokens or 0,
            output_tokens=raw.output_tokens or 0,
            cache_read_input_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
        )
        return AIResult(
            text=text,
            model=message.model,
            usage=usage,
            cost_usd=self._pricing.cost(message.model, usage),
            mock=False,
            stop_reason=message.stop_reason or "end_turn",
            latency_ms=latency,
        )
