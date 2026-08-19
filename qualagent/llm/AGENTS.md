# qualagent.llm — LLM Provider Layer

## Purpose

Abstraction over multiple LLM providers (OpenAI, Anthropic, Gemini, Ollama) with structured output support and fake provider for testing.

## Ownership

- Primary: QualAgent development team
- Part of `qualagent` package

## Local Contracts

- Protocol: `qualagent.llm.base.LLMProvider` — `complete(prompt, schema)` → typed result
- Factory: `qualagent.llm.factory.create_provider(config)` returns provider instance
- Providers: `OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`, `OllamaProvider`, `FakeProvider`
- Structured calls: `qualagent.llm.structured.structured_complete(provider, prompt, model)`
- Configuration via `qualagent.config.Config.llm_*` settings
- Cassettes: `qualagent.llm.fake.FakeProvider` loads recorded responses from `tests/fixtures/cassettes/`

## Work Guidance

- Add new providers by implementing `LLMProvider` protocol
- Register in `factory.create_provider()`
- Use `structured_complete` for all production calls (validates JSON against Pydantic model)
- Fake provider for deterministic tests — record cassettes via `FakeProvider.record()`
- Token counting via `provider.count_tokens(text)`

## Verification

- `pytest tests/unit/test_llm_providers.py`
- `pytest tests/unit/test_structured.py`

## Child DOX Index

- No child AGENTS.md files needed