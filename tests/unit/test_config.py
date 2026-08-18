"""Tests for qualagent.config: layering, validation, secrets, privacy gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from qualagent.config import (
    DEFAULTS,
    QualAgentConfig,
    RemoteSettings,
    assert_remote_allowed,
    config_to_dict,
    get_api_key,
    get_ollama_host,
    is_remote,
    load_config,
)
from qualagent.domain.errors import RemoteProviderNotAccepted, ValidationError


def write_toml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestDefaults:
    def test_defaults_with_no_files(self, global_dir: Path) -> None:
        cfg = load_config(global_dir=global_dir)
        assert cfg.llm.provider == "ollama"
        assert cfg.llm.temperature == 0.0
        assert cfg.llm.seed == 42
        assert cfg.memory.top_k == 8
        assert cfg.coding.batch_size == 20
        assert cfg.llm.remote.accepted is False

    def test_defaults_dict_matches_dataclass_defaults(self) -> None:
        cfg = load_config(project_dir=None, global_dir=Path("C:/nonexistent-qualagent"))
        assert (
            config_to_dict(cfg)["coding"]["batch_token_budget"]
            == DEFAULTS["coding"]["batch_token_budget"]
        )


class TestLayering:
    def test_global_overrides_defaults(self, global_dir: Path) -> None:
        write_toml(global_dir / "config.toml", '[llm]\nmodel = "llama3"\n')
        cfg = load_config(global_dir=global_dir)
        assert cfg.llm.model == "llama3"
        assert cfg.llm.provider == "ollama"  # untouched default

    def test_project_overrides_global(self, global_dir: Path, tmp_path: Path) -> None:
        write_toml(global_dir / "config.toml", '[llm]\nmodel = "llama3"\ntemperature = 0.3\n')
        project_dir = tmp_path / "study" / ".qualagent"
        write_toml(project_dir / "config.toml", '[llm]\nmodel = "mistral"\n')
        cfg = load_config(project_dir=project_dir, global_dir=global_dir)
        assert cfg.llm.model == "mistral"
        assert cfg.llm.temperature == 0.3  # global value survives project override

    def test_nested_remote_section_merges(self, global_dir: Path, tmp_path: Path) -> None:
        write_toml(global_dir / "config.toml", "[llm.remote]\naccepted = true\n")
        project_dir = tmp_path / ".qualagent"
        write_toml(project_dir / "config.toml", '[llm]\nprovider = "openai"\n')
        cfg = load_config(project_dir=project_dir, global_dir=global_dir)
        assert cfg.llm.remote == RemoteSettings(accepted=True)

    def test_missing_project_dir_file_is_ok(self, global_dir: Path, tmp_path: Path) -> None:
        cfg = load_config(project_dir=tmp_path / ".qualagent", global_dir=global_dir)
        assert cfg == QualAgentConfig()


class TestValidation:
    @pytest.mark.parametrize(
        "bad", [{"provider": "azure"}, {"temperature": 5.0}, {"max_retries": 99}]
    )
    def test_invalid_llm_values_rejected(self, global_dir: Path, bad: dict) -> None:
        lines = ["[llm]"] + [
            f"{k} = {v!r}" if isinstance(v, str) else f"{k} = {v}" for k, v in bad.items()
        ]
        write_toml(global_dir / "config.toml", "\n".join(lines) + "\n")
        with pytest.raises(ValidationError):
            load_config(global_dir=global_dir)

    def test_unknown_key_rejected(self, global_dir: Path) -> None:
        write_toml(global_dir / "config.toml", "[llm]\nsparkliness = 7\n")
        with pytest.raises(ValidationError, match="sparkliness"):
            load_config(global_dir=global_dir)


class TestPrivacyGate:
    def test_local_provider_needs_no_consent(self) -> None:
        assert not is_remote("ollama")
        assert_remote_allowed(QualAgentConfig())  # ollama default: no raise

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
    def test_remote_requires_consent(self, provider: str) -> None:
        from qualagent.config import LLMSettings

        cfg = QualAgentConfig(llm=LLMSettings(provider=provider))
        with pytest.raises(RemoteProviderNotAccepted):
            assert_remote_allowed(cfg)

    def test_remote_ok_with_config_flag(self) -> None:
        from qualagent.config import LLMSettings

        cfg = QualAgentConfig(
            llm=LLMSettings(provider="openai", remote=RemoteSettings(accepted=True))
        )
        assert_remote_allowed(cfg)

    def test_remote_ok_with_cli_flag(self) -> None:
        from qualagent.config import LLMSettings

        cfg = QualAgentConfig(llm=LLMSettings(provider="gemini"))
        assert_remote_allowed(cfg, cli_accepted=True)


class TestSecrets:
    def test_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUALAGENT_OPENAI_API_KEY", "sk-test")
        assert get_api_key("openai") == "sk-test"
        monkeypatch.delenv("QUALAGENT_ANTHROPIC_API_KEY", raising=False)
        assert get_api_key("anthropic") is None

    def test_ollama_host_default_and_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QUALAGENT_OLLAMA_HOST", raising=False)
        assert get_ollama_host() == "http://localhost:11434"
        monkeypatch.setenv("QUALAGENT_OLLAMA_HOST", "http://gpu-box:11434")
        assert get_ollama_host() == "http://gpu-box:11434"
