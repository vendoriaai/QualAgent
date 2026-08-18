"""Configuration loading: built-in defaults, global config, project overrides.

Layering (later wins): defaults <- ``~/.qualagent/config.toml`` <-
``{study}/.qualagent/config.toml``. Secrets are read from environment
variables only and are never written to disk (builder rule 9).

The remote-provider privacy gate (TAD D8) lives here: ``assert_remote_allowed``
raises unless the user explicitly accepted remote data transfer via the config
flag or the ``--accept-remote`` CLI option.
"""

from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from qualagent.domain.errors import RemoteProviderNotAccepted, ValidationError

#: Providers whose API calls send data off the local machine.
REMOTE_PROVIDERS = frozenset({"openai", "anthropic", "gemini"})
#: All supported provider names.
ALL_PROVIDERS = REMOTE_PROVIDERS | {"ollama"}

GLOBAL_DIR_ENV = "QUALAGENT_HOME"

DEFAULTS: dict[str, Any] = {
    "llm": {
        "provider": "ollama",
        "model": "qwen3:32b",
        "temperature": 0.0,
        "seed": 42,
        "max_retries": 2,
        "remote": {"accepted": False},
    },
    "memory": {"embedding_model": "all-MiniLM-L6-v2", "top_k": 8},
    "coding": {"batch_size": 20, "batch_token_budget": 4000},
}


@dataclass(frozen=True)
class RemoteSettings:
    """Remote-provider consent state (privacy gate)."""

    accepted: bool = False


@dataclass(frozen=True)
class LLMSettings:
    """LLM provider settings."""

    provider: str = "ollama"
    model: str = "qwen3:32b"
    temperature: float = 0.0
    seed: int | None = 42
    max_retries: int = 2
    remote: RemoteSettings = field(default_factory=RemoteSettings)


@dataclass(frozen=True)
class MemorySettings:
    """Vector-memory settings (embeddings for correction recall)."""

    embedding_model: str = "all-MiniLM-L6-v2"
    top_k: int = 8


@dataclass(frozen=True)
class CodingSettings:
    """Coding pipeline batching settings."""

    batch_size: int = 20
    batch_token_budget: int = 4000


@dataclass(frozen=True)
class QualAgentConfig:
    """Fully resolved configuration."""

    llm: LLMSettings = field(default_factory=LLMSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    coding: CodingSettings = field(default_factory=CodingSettings)


def global_config_dir() -> Path:
    """Return the global QualAgent config directory (``~/.qualagent`` by default)."""
    env = os.environ.get(GLOBAL_DIR_ENV)
    return Path(env).expanduser() if env else Path.home() / ".qualagent"


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - defensive
        raise ValidationError(f"Invalid TOML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"Expected a table at top level of {path}")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _build_settings(section_cls: type[Any], data: dict[str, Any], section: str) -> Any:
    """Instantiate a frozen settings dataclass from a config section."""
    valid_keys = {f.name for f in fields(section_cls)}
    unknown = set(data) - valid_keys
    if unknown:
        raise ValidationError(f"Unknown key(s) in [{section}]: {', '.join(sorted(unknown))}")
    try:
        if section_cls is LLMSettings and "remote" in data:
            data = {**data, "remote": RemoteSettings(**data["remote"])}
        return section_cls(**data)
    except TypeError as exc:
        raise ValidationError(f"Invalid [{section}] config: {exc}") from exc


def load_config(
    project_dir: Path | None = None,
    *,
    global_dir: Path | None = None,
) -> QualAgentConfig:
    """Load the resolved configuration.

    Args:
        project_dir: Study ``.qualagent`` directory (project-level overrides),
            or ``None`` to load defaults + global config only.
        global_dir: Override for the global config directory (tests); defaults
            to ``~/.qualagent``.

    Returns:
        Fully resolved, validated :class:`QualAgentConfig`.

    Raises:
        ValidationError: If any config file is malformed or has unknown keys.
    """
    gdir = global_dir if global_dir is not None else global_config_dir()
    merged = _deep_merge(DEFAULTS, _read_toml(gdir / "config.toml"))
    if project_dir is not None:
        merged = _deep_merge(merged, _read_toml(project_dir / "config.toml"))

    llm = _build_settings(LLMSettings, merged["llm"], "llm")
    if llm.provider not in ALL_PROVIDERS:
        raise ValidationError(
            f"llm.provider must be one of {sorted(ALL_PROVIDERS)}, got {llm.provider!r}"
        )
    if not 0.0 <= llm.temperature <= 2.0:
        raise ValidationError("llm.temperature must be within [0.0, 2.0]")
    if not 0 <= llm.max_retries <= 5:
        raise ValidationError("llm.max_retries must be within [0, 5]")

    memory = _build_settings(MemorySettings, merged["memory"], "memory")
    if memory.top_k < 1:
        raise ValidationError("memory.top_k must be >= 1")

    coding = _build_settings(CodingSettings, merged["coding"], "coding")
    if coding.batch_size < 1 or coding.batch_token_budget < 1:
        raise ValidationError("coding.batch_size and batch_token_budget must be >= 1")

    return QualAgentConfig(llm=llm, memory=memory, coding=coding)


def is_remote(provider: str) -> bool:
    """Return True if the provider sends data to a hosted API."""
    return provider in REMOTE_PROVIDERS


def assert_remote_allowed(config: QualAgentConfig, *, cli_accepted: bool = False) -> None:
    """Enforce the privacy gate for remote providers (TAD D8).

    Args:
        config: Resolved configuration.
        cli_accepted: True when the user passed ``--accept-remote``.

    Raises:
        RemoteProviderNotAccepted: If the provider is remote and consent was
            not given via config or CLI.
    """
    if not is_remote(config.llm.provider):
        return
    if config.llm.remote.accepted or cli_accepted:
        return
    raise RemoteProviderNotAccepted(
        f"Provider '{config.llm.provider}' sends document text to a hosted API. "
        "Data leaving this machine requires explicit consent: set "
        "[llm.remote] accepted = true in config.toml or pass --accept-remote."
    )


#: Env var names per provider; gemini's key comes from QUALAGENT_GOOGLE_API_KEY
#: per 04_DATA_MODEL section 4.
_API_KEY_ENV = {
    "openai": "QUALAGENT_OPENAI_API_KEY",
    "anthropic": "QUALAGENT_ANTHROPIC_API_KEY",
    "gemini": "QUALAGENT_GOOGLE_API_KEY",
}


def get_api_key(provider: str) -> str | None:
    """Return the API key for a provider from the environment, if set."""
    var = _API_KEY_ENV.get(provider, f"QUALAGENT_{provider.upper()}_API_KEY")
    return os.environ.get(var)


def get_ollama_host() -> str:
    """Return the Ollama base URL from the environment (default localhost)."""
    return os.environ.get("QUALAGENT_OLLAMA_HOST", "http://localhost:11434")


def config_to_dict(config: QualAgentConfig) -> dict[str, Any]:
    """Serialize a config back to a plain dict (no secrets; secrets are env-only)."""
    return {
        "llm": {
            "provider": config.llm.provider,
            "model": config.llm.model,
            "temperature": config.llm.temperature,
            "seed": config.llm.seed,
            "max_retries": config.llm.max_retries,
            "remote": {"accepted": config.llm.remote.accepted},
        },
        "memory": {
            "embedding_model": config.memory.embedding_model,
            "top_k": config.memory.top_k,
        },
        "coding": {
            "batch_size": config.coding.batch_size,
            "batch_token_budget": config.coding.batch_token_budget,
        },
    }
