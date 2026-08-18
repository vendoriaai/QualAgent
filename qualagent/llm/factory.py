"""Provider factory: build the configured provider behind the privacy gate."""

from __future__ import annotations

from qualagent.config import QualAgentConfig, assert_remote_allowed
from qualagent.llm.base import LLMProvider
from qualagent.llm.ollama_provider import OllamaProvider


def create_provider(
    config: QualAgentConfig,
    *,
    cli_accepted_remote: bool = False,
) -> LLMProvider:
    """Instantiate the configured LLM provider.

    Args:
        config: Resolved configuration (provider, model).
        cli_accepted_remote: True when the user passed ``--accept-remote``.

    Raises:
        RemoteProviderNotAccepted: Remote provider without explicit consent.
        ValidationError: Remote provider without an API key in the environment.
    """
    assert_remote_allowed(config, cli_accepted=cli_accepted_remote)
    provider_name = config.llm.provider
    model = config.llm.model
    if provider_name == "ollama":
        return OllamaProvider(model)
    if provider_name == "openai":
        from qualagent.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(model)
    if provider_name == "anthropic":
        from qualagent.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model)
    if provider_name == "gemini":
        from qualagent.llm.gemini_provider import GeminiProvider

        return GeminiProvider(model)
    raise ValueError(f"Unknown provider: {provider_name}")  # pragma: no cover
