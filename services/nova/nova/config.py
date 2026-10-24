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
    def parse(cls, value: str, *, setting: str = "NOVA_LLM") -> Self:
        """setting: the name a ConfigError gives, the one that's wrong."""
        provider, _, model = value.strip().partition("/")
        if not provider or not model:
            raise ConfigError(f'{setting} must look like "provider/model"')
        if provider not in PROVIDERS:
            raise ConfigError(f"{setting}'s provider must be one of {', '.join(PROVIDERS)}")
        return cls(provider, model)


@dataclass(frozen=True)
class Settings:
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str = field(repr=False)
    llm: LlmChoice
    anthropic_api_key: str | None = field(default=None, repr=False)
    # The model for the distress check's labels, if not NOVA_LLM ("provider/model"): a
    # smaller one can label without queueing behind live lines on the same Ollama.
    classifier: LlmChoice | None = None
    ollama_base_url: str = "http://localhost:11434"
    # How long a live reaction may take before Nova uses a fallback line instead. Measured
    # (decision 0005): warm Gemma's live lines took a median of 321-342 ms and at most 565 ms
    # over 60 runs, so 2 s is about 3.5x the worst case. It caps the wait for the model, not
    # the silence the room hears: the voice's first audio adds about 0.2 to 0.5 s, and a
    # reply also waits for the end of the dater's sentence and its transcript.
    live_timeout: float = 2.0
    # How long one request for a set piece may take before the date. Loading Gemma cold took
    # 20-28 s (decision 0005), so 60 s covers a cold start plus the line.
    prep_timeout: float = 60.0
    # How long preparing all of a date's set pieces may take. When it runs out, every set
    # piece not yet chosen gets its fallback, so a model that hangs holds a show for the
    # warm-up's deadline (below) and this, not ten set pieces times the prep timeout. With
    # the warm-up, the product owner's bound from session start to Nova ready (2026-10-22):
    # 60 s. Measured warm: 11.2 and 11.4 s for all ten set pieces, three candidates each.
    prep_budget: float = 20.0
    # The warm-up's one deadline, before the prep budget starts and apart from it: a cold
    # load took 17.6 and 18.8 s on the development PC (2026-10-22; 17.7 s and 20-28 s
    # before), and cancelling a request mid-load aborts the load (decision 0005), so the
    # warm-up holds one request for all of it. If it fails, the doors hold for the
    # moderator (Rehearsal.hold_until_ready), never a silent date of fallbacks.
    warm_up: float = 40.0
    # How many requests for set pieces may be in flight at once while they're prepared. A
    # distress check during prep queues behind them on the same model, and with Ollama
    # serving one request at a time (OLLAMA_NUM_PARALLEL), more never prepared faster: set
    # it to match OLLAMA_NUM_PARALLEL.
    prep_parallel: int = 1
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
    # This date's recent turns (decision 0008) go to a model that isn't local only when this
    # is true: off until the privacy notice covers that provider and data flow (decision
    # 0005). A local model (Ollama on this machine) always sees them.
    dialogue_to_provider: bool = False

    REQUIRED: ClassVar[tuple[str, ...]] = (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "NOVA_LLM",
    )
    ENV_NAMES: ClassVar[tuple[str, ...]] = (
        *REQUIRED,
        "NOVA_CLASSIFIER_LLM",
        "ANTHROPIC_API_KEY",
        "OLLAMA_BASE_URL",
        "NOVA_LIVE_TIMEOUT_SECONDS",
        "NOVA_PREP_TIMEOUT_SECONDS",
        "NOVA_PREP_BUDGET_SECONDS",
        "NOVA_WARM_UP_SECONDS",
        "NOVA_PREP_PARALLEL",
        "NOVA_DISTRESS_TIMEOUT_SECONDS",
        "NOVA_UTTERANCE_GRACE_SECONDS",
        "NOVA_UTTERANCE_MAX_WAIT_SECONDS",
        "NOVA_VOICE",
        "NOVA_STT",
        "NOVA_DIALOGUE_TO_PROVIDER",
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        missing = [name for name in cls.REQUIRED if not env.get(name, "").strip()]
        if missing:
            raise ConfigError(f"Missing in services/nova/.env: {', '.join(missing)}")

        llm = LlmChoice.parse(env["NOVA_LLM"])
        classifier_value = env.get("NOVA_CLASSIFIER_LLM", "").strip()
        classifier = (
            LlmChoice.parse(classifier_value, setting="NOVA_CLASSIFIER_LLM")
            if classifier_value
            else None
        )
        anthropic_api_key = env.get("ANTHROPIC_API_KEY", "").strip() or None
        uses_anthropic = "anthropic" in {llm.provider, classifier.provider if classifier else ""}
        if uses_anthropic and anthropic_api_key is None:
            raise ConfigError(
                "ANTHROPIC_API_KEY is required when NOVA_LLM or NOVA_CLASSIFIER_LLM uses anthropic"
            )

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
            classifier=classifier,
            ollama_base_url=env.get("OLLAMA_BASE_URL", "").strip() or cls.ollama_base_url,
            live_timeout=_seconds(env, "NOVA_LIVE_TIMEOUT_SECONDS", cls.live_timeout),
            prep_timeout=_seconds(env, "NOVA_PREP_TIMEOUT_SECONDS", cls.prep_timeout),
            prep_budget=_seconds(env, "NOVA_PREP_BUDGET_SECONDS", cls.prep_budget),
            warm_up=_seconds(env, "NOVA_WARM_UP_SECONDS", cls.warm_up),
            prep_parallel=_count(env, "NOVA_PREP_PARALLEL", cls.prep_parallel),
            distress_timeout=_seconds(env, "NOVA_DISTRESS_TIMEOUT_SECONDS", cls.distress_timeout),
            utterance_grace=grace,
            utterance_max_wait=max_wait,
            voice=_voice(env.get("NOVA_VOICE", "").strip() or cls.voice),
            stt=_stt(env.get("NOVA_STT", "").strip() or cls.stt),
            dialogue_to_provider=_flag(env, "NOVA_DIALOGUE_TO_PROVIDER"),
        )

    @property
    def shares_dialogue(self) -> bool:
        """Whether a live date's recent turns may go into Nova's prompts: always to a local
        model, to another only once NOVA_DIALOGUE_TO_PROVIDER says the notice covers it."""
        return self.llm.provider == "ollama" or self.dialogue_to_provider


def _voice(value: str) -> str:
    model = value.partition(":")[0]
    provider, _, name = model.partition("/")
    if not provider or not name:
        raise ConfigError('NOVA_VOICE must look like "provider/model:voice"')
    return value


def _stt(value: str) -> str:
    provider, _, model = value.partition("/")
    if not provider or not model or ":" in value:
        raise ConfigError('NOVA_STT must look like "provider/model"')
    return value


def _flag(env: Mapping[str, str], name: str) -> bool:
    raw = env.get(name, "").strip().lower()
    if raw in ("", "false", "0", "no"):
        return False
    if raw in ("true", "1", "yes"):
        return True
    raise ConfigError(f"{name} must be true or false")


def _count(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise ConfigError(f"{name} must be a whole number, 1 or more")
    return int(raw)


def _seconds(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} must be a positive number of seconds")
    return value


def load_settings(env_file: Path = DEFAULT_ENV_FILE) -> Settings:
    """Reads the .env file, then lets real environment variables override it."""
    from_file = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    return Settings.from_env({**from_file, **os.environ})
