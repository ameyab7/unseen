"""Nova's settings, read from services/nova/.env and the environment.

The language model is one setting, NOVA_LLM ("provider/model"), so moving from local
Gemma to Claude is an .env change, not a code change. Secrets never appear in repr()
or error messages.
"""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Self

from dotenv import dotenv_values

DEFAULT_ENV_FILE = Path(__file__).parents[1] / ".env"

PROVIDERS = ("anthropic", "ollama")


class ConfigError(ValueError):
    """A missing or malformed setting. The message names it, never its value."""


@dataclass(frozen=True)
class LlmChoice:
    provider: str
    model: str

    @classmethod
    def parse(cls, value: str) -> Self:
        provider, _, model = value.strip().partition("/")
        if not provider or not model:
            raise ConfigError(f'NOVA_LLM must look like "provider/model", got {value!r}')
        if provider not in PROVIDERS:
            raise ConfigError(
                f"NOVA_LLM provider must be one of {', '.join(PROVIDERS)}, got {provider!r}"
            )
        return cls(provider, model)


@dataclass(frozen=True)
class Settings:
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str = field(repr=False)
    llm: LlmChoice
    anthropic_api_key: str | None = field(default=None, repr=False)
    ollama_base_url: str = "http://localhost:11434"
    # How long a live reaction may take before Nova uses a fallback line instead. Measured:
    # warm Gemma writes a line in about 0.4 s, so 2 s is roughly 4x that, short enough that
    # the room never hears more than 2 s of dead air before Nova speaks.
    live_timeout: float = 2.0
    # How long a set piece may take before the date. Loading Gemma cold took 28 s, so 60 s
    # covers a cold start plus the line; the date only starts once these are ready.
    prep_timeout: float = 60.0

    REQUIRED: ClassVar[tuple[str, ...]] = (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "NOVA_LLM",
    )
    ENV_NAMES: ClassVar[tuple[str, ...]] = (
        *REQUIRED,
        "ANTHROPIC_API_KEY",
        "OLLAMA_BASE_URL",
        "NOVA_LIVE_TIMEOUT_SECONDS",
        "NOVA_PREP_TIMEOUT_SECONDS",
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        missing = [name for name in cls.REQUIRED if not env.get(name, "").strip()]
        if missing:
            raise ConfigError(f"Missing in services/nova/.env: {', '.join(missing)}")

        llm = LlmChoice.parse(env["NOVA_LLM"])
        anthropic_api_key = env.get("ANTHROPIC_API_KEY", "").strip() or None
        if llm.provider == "anthropic" and anthropic_api_key is None:
            raise ConfigError("ANTHROPIC_API_KEY is required when NOVA_LLM uses anthropic")

        return cls(
            livekit_url=env["LIVEKIT_URL"].strip(),
            livekit_api_key=env["LIVEKIT_API_KEY"].strip(),
            livekit_api_secret=env["LIVEKIT_API_SECRET"].strip(),
            llm=llm,
            anthropic_api_key=anthropic_api_key,
            ollama_base_url=env.get("OLLAMA_BASE_URL", "").strip() or cls.ollama_base_url,
            live_timeout=_seconds(env, "NOVA_LIVE_TIMEOUT_SECONDS", cls.live_timeout),
            prep_timeout=_seconds(env, "NOVA_PREP_TIMEOUT_SECONDS", cls.prep_timeout),
        )


def _seconds(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} must be a positive number of seconds, got {raw!r}")
    return value


def load_settings(env_file: Path = DEFAULT_ENV_FILE) -> Settings:
    """Reads the .env file, then lets real environment variables override it."""
    from_file = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    return Settings.from_env({**from_file, **os.environ})
