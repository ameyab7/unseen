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

# LiveKit's built-in speech models. The voice is a shortlist pick until the product owner
# chooses one (task 5); Robyn is Cartesia's neutral, mature voice.
DEFAULT_VOICE = "cartesia/sonic-3:f31cc6a7-c1e8-4764-980c-60a361443dd1"
DEFAULT_STT = "deepgram/nova-3"


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
    # warm Gemma writes a line in about 0.4 s, so 2 s is roughly 4x that. It caps the wait
    # for the model, not the silence the room hears: the voice's first audio adds about 0.2
    # to 0.5 s, and a reply also waits for the end of the dater's sentence and its transcript.
    live_timeout: float = 2.0
    # How long a set piece may take before the date. Loading Gemma cold took 28 s, so 60 s
    # covers a cold start plus the line; the date only starts once these are ready.
    prep_timeout: float = 60.0
    # How long the distress check (decision 0005, the meaning layer) may take before the
    # moderator is alerted instead. A "yes" pauses the dater only once it arrives, so this
    # also bounds how late the kind line can come.
    distress_timeout: float = 2.0
    # How long Nova waits, once a dater has stopped speaking and the last transcript is in,
    # before acting on what they said (decision 0005, utterances). Speech recognition
    # finalizes at almost every pause, so without it Nova would answer half a sentence
    # ("Nova,") or pause a dater for a phrase they hadn't finished. It counts against the
    # 2.5 s reply target (docs/spike-results.md).
    utterance_grace: float = 0.6
    # The most Nova waits after a dater stops speaking, however late transcripts keep coming.
    utterance_max_wait: float = 1.5
    # Nova's voice as "provider/model:voice", and the speech recognition that hears daters.
    voice: str = DEFAULT_VOICE
    stt: str = DEFAULT_STT

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
        "NOVA_DISTRESS_TIMEOUT_SECONDS",
        "NOVA_UTTERANCE_GRACE_SECONDS",
        "NOVA_UTTERANCE_MAX_WAIT_SECONDS",
        "NOVA_VOICE",
        "NOVA_STT",
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

        grace = _seconds(env, "NOVA_UTTERANCE_GRACE_SECONDS", cls.utterance_grace)
        max_wait = _seconds(env, "NOVA_UTTERANCE_MAX_WAIT_SECONDS", cls.utterance_max_wait)
        if max_wait < grace:
            raise ConfigError(
                "NOVA_UTTERANCE_MAX_WAIT_SECONDS must be at least NOVA_UTTERANCE_GRACE_SECONDS"
            )

        return cls(
            livekit_url=env["LIVEKIT_URL"].strip(),
            livekit_api_key=env["LIVEKIT_API_KEY"].strip(),
            livekit_api_secret=env["LIVEKIT_API_SECRET"].strip(),
            llm=llm,
            anthropic_api_key=anthropic_api_key,
            ollama_base_url=env.get("OLLAMA_BASE_URL", "").strip() or cls.ollama_base_url,
            live_timeout=_seconds(env, "NOVA_LIVE_TIMEOUT_SECONDS", cls.live_timeout),
            prep_timeout=_seconds(env, "NOVA_PREP_TIMEOUT_SECONDS", cls.prep_timeout),
            distress_timeout=_seconds(env, "NOVA_DISTRESS_TIMEOUT_SECONDS", cls.distress_timeout),
            utterance_grace=grace,
            utterance_max_wait=max_wait,
            voice=_voice(env.get("NOVA_VOICE", "").strip() or cls.voice),
            stt=env.get("NOVA_STT", "").strip() or cls.stt,
        )


def _voice(value: str) -> str:
    model = value.partition(":")[0]
    provider, _, name = model.partition("/")
    if not provider or not name:
        raise ConfigError(f'NOVA_VOICE must look like "provider/model:voice", got {value!r}')
    return value


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
