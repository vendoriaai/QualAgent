"""LLM provider tests: privacy gate, request shapes, response parsing (mocked)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from qualagent.config import LLMSettings, QualAgentConfig, RemoteSettings
from qualagent.domain.errors import (
    LLMProviderError,
    RemoteProviderNotAccepted,
    ValidationError,
)
from qualagent.llm.base import Message
from qualagent.llm.factory import create_provider

SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array"}},
    "required": ["results"],
}
MSGS = [Message(role="system", content="sys"), Message(role="user", content="code this")]


def _cfg(provider: str, *, remote_ok: bool = False) -> QualAgentConfig:
    return QualAgentConfig(
        llm=LLMSettings(
            provider=provider, model="test-model", remote=RemoteSettings(accepted=remote_ok)
        )
    )


class TestPrivacyGate:
    @pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
    def test_remote_without_consent_raises(self, provider: str) -> None:
        with pytest.raises(RemoteProviderNotAccepted):
            create_provider(_cfg(provider))

    def test_remote_with_cli_flag_needs_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QUALAGENT_OPENAI_API_KEY", raising=False)
        with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
            create_provider(_cfg("openai"), cli_accepted_remote=True)

    def test_remote_with_flag_and_key_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from qualagent.llm.openai_provider import OpenAIProvider

        monkeypatch.setenv("QUALAGENT_OPENAI_API_KEY", "sk-test")
        provider = create_provider(_cfg("openai"), cli_accepted_remote=True)
        assert isinstance(provider, OpenAIProvider)
        provider.close()

    def test_local_ollama_no_gate_no_key(self) -> None:
        from qualagent.llm.ollama_provider import OllamaProvider

        provider = create_provider(_cfg("ollama"))
        assert isinstance(provider, OllamaProvider)
        assert provider.model_id == "ollama/test-model"
        provider.close()


class TestOllama:
    def test_chat_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from qualagent.llm.ollama_provider import OllamaProvider

        monkeypatch.setenv("QUALAGENT_OLLAMA_HOST", "http://localhost:11434")
        provider = OllamaProvider("qwen3:32b")
        with respx.mock:
            route = respx.post("http://localhost:11434/api/chat").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "message": {"role": "assistant", "content": '{"results": []}'},
                        "prompt_eval_count": 10,
                        "eval_count": 5,
                    },
                )
            )
            result = provider.complete(MSGS, schema=SCHEMA, temperature=0.0, seed=42)
        assert result.text == '{"results": []}'
        assert result.model == "ollama/qwen3:32b"
        assert result.tokens == 15
        body = json.loads(route.calls[0].request.content.decode())
        assert body["model"] == "qwen3:32b"
        assert body["stream"] is False
        assert body["format"] == SCHEMA
        assert body["options"] == {"temperature": 0.0, "seed": 42}
        provider.close()

    def test_custom_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from qualagent.llm.ollama_provider import OllamaProvider

        monkeypatch.setenv("QUALAGENT_OLLAMA_HOST", "http://gpu-box:11434/")
        provider = OllamaProvider("m")
        with respx.mock:
            respx.post("http://gpu-box:11434/api/chat").mock(
                return_value=httpx.Response(200, json={"message": {"content": "{}"}})
            )
            result = provider.complete(MSGS, schema=SCHEMA)
        assert result.text == "{}"
        provider.close()

    def test_http_error_maps_to_llm_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from qualagent.llm.ollama_provider import OllamaProvider

        monkeypatch.setenv("QUALAGENT_OLLAMA_HOST", "http://localhost:11434")
        provider = OllamaProvider("qwen3:32b")
        with respx.mock:
            respx.post("http://localhost:11434/api/chat").mock(
                return_value=httpx.Response(500, text="boom")
            )
            with pytest.raises(LLMProviderError, match="status=500"):
                provider.complete(MSGS, schema=SCHEMA)
        provider.close()

    def test_connection_error_maps_to_llm_error(self) -> None:
        from qualagent.llm.ollama_provider import OllamaProvider

        provider = OllamaProvider("qwen3:32b", host="http://localhost:1")
        with respx.mock:
            respx.post("http://localhost:1/api/chat").mock(
                side_effect=httpx.ConnectError("no server")
            )
            with pytest.raises(LLMProviderError):
                provider.complete(MSGS, schema=SCHEMA)
        provider.close()


class TestOpenAI:
    def test_json_schema_request_and_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from qualagent.llm.openai_provider import OpenAIProvider

        monkeypatch.setenv("QUALAGENT_OPENAI_API_KEY", "sk-test")
        provider = OpenAIProvider("gpt-5.2")
        with respx.mock:
            route = respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": '{"results": []}'}}],
                        "usage": {"total_tokens": 7},
                    },
                )
            )
            result = provider.complete(MSGS, schema=SCHEMA, temperature=0.0, seed=42)
        assert result.model == "openai/gpt-5.2"
        body = json.loads(route.calls[0].request.content.decode())
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["schema"] == SCHEMA
        assert body["seed"] == 42
        assert "Authorization" in route.calls[0].request.headers
        provider.close()

    def test_missing_key_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from qualagent.llm.openai_provider import OpenAIProvider

        monkeypatch.delenv("QUALAGENT_OPENAI_API_KEY", raising=False)
        with pytest.raises(ValidationError):
            OpenAIProvider("gpt-5.2")


class TestAnthropic:
    def test_forced_tool_use(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from qualagent.llm.anthropic_provider import AnthropicProvider

        monkeypatch.setenv("QUALAGENT_ANTHROPIC_API_KEY", "ak-test")
        provider = AnthropicProvider("claude-sonnet-4")
        with respx.mock:
            route = respx.post("https://api.anthropic.com/v1/messages").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "content": [
                            {"type": "text", "text": "thinking..."},
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "emit_json",
                                "input": {"results": []},
                            },
                        ],
                        "usage": {"input_tokens": 3, "output_tokens": 4},
                    },
                )
            )
            result = provider.complete(MSGS, schema=SCHEMA, temperature=0.0, seed=1)
        assert result.text == '{"results": []}'
        assert result.tokens == 7
        body = json.loads(route.calls[0].request.content.decode())
        assert body["tool_choice"] == {"type": "tool", "name": "emit_json"}
        assert body["tools"][0]["input_schema"] == SCHEMA
        assert body["system"] == "sys"
        assert all(m["role"] != "system" for m in body["messages"])
        provider.close()


class TestGemini:
    def test_response_schema_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from qualagent.llm.gemini_provider import GeminiProvider

        monkeypatch.setenv("QUALAGENT_GOOGLE_API_KEY", "gk-test")
        provider = GeminiProvider("gemini-2.5-pro")
        with respx.mock:
            route = respx.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.5-pro:generateContent"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "candidates": [{"content": {"parts": [{"text": '{"results": []}'}]}}],
                        "usageMetadata": {"totalTokenCount": 9},
                    },
                )
            )
            result = provider.complete(MSGS, schema=SCHEMA, temperature=0.0, seed=42)
        assert result.text == '{"results": []}'
        assert result.tokens == 9
        body = json.loads(route.calls[0].request.content.decode())
        gc = body["generationConfig"]
        assert gc["responseMimeType"] == "application/json"
        assert gc["responseSchema"] == SCHEMA
        assert gc["seed"] == 42
        assert body["systemInstruction"]["parts"][0]["text"] == "sys"
        provider.close()

    def test_missing_key_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from qualagent.llm.gemini_provider import GeminiProvider

        monkeypatch.delenv("QUALAGENT_GOOGLE_API_KEY", raising=False)
        with pytest.raises(ValidationError):
            GeminiProvider("gemini-2.5-pro")
