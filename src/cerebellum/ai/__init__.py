"""AI providers and provider selection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cerebellum.ai.anthropic_provider import AnthropicProvider
from cerebellum.ai.base import AIError, AIProvider, AIRequest, AIResult, Usage
from cerebellum.ai.mock import MockProvider
from cerebellum.ai.pricing import Pricing
from cerebellum.config import Settings, has_anthropic_credentials

__all__ = [
    "AIError",
    "AIProvider",
    "AIRequest",
    "AIResult",
    "AnthropicProvider",
    "MockProvider",
    "ProviderChoice",
    "Usage",
    "select_provider",
]


@dataclass(frozen=True)
class ProviderChoice:
    provider: AIProvider
    reason: str
    # True when the mock AI was asked for (--mock / CEREBELLUM_MOCK), not a missing-key fallback.
    mock_requested: bool = False


def select_provider(
    settings: Settings,
    *,
    force_mock: bool = False,
    env: Mapping[str, str] | None = None,
    config_dir: Path | None = None,
) -> ProviderChoice:
    if force_mock or settings.force_mock:
        return ProviderChoice(MockProvider(), "mock AI (requested)", mock_requested=True)
    if has_anthropic_credentials(env, config_dir):
        provider = AnthropicProvider(pricing=Pricing.load(settings.pricing_file))
        return ProviderChoice(provider, f"Claude API ({settings.model})")
    return ProviderChoice(MockProvider(), "mock AI (no Anthropic credentials found)")
