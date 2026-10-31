"""The language models that write Nova's lines, chosen by NOVA_LLM ("provider/model").

Both adapters make one request per line with retries off: if a request is slow or fails,
Nova uses a fallback line instead of waiting. A request that times out is cancelled
(asyncio.wait_for), which closes its connection. Measured on the development PC
(2026-10-21, decision 0005): Ollama then drops the work, running or queued, so abandoned
requests don't pile up behind later ones. But cancelling the only request waiting on a cold
load aborts the load too, which is why the warm-up holds one request for its whole deadline.
"""

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import anthropic
import openai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from nova.config import LlmChoice, Settings
from nova.measure import ms_since


class ModelError(Exception):
    """The model gave no usable line."""


class ModelRefused(ModelError):
    """The model declined (Claude's refusal stop reason)."""


class LineModel(Protocol):
    name: str

    async def write(self, *, system: str, prompt: str) -> str:
        """A line for Nova to say."""
        ...

    async def label(
        self, *, system: str, prompt: str, schema: Mapping[str, object] | None = None
    ) -> str:
        """A label from a fixed set (the distress check): short and as repeatable as the
        model allows. schema: a JSON schema the answer must follow, as structured output."""
        ...


class OllamaModel:
    """A local model (development: Gemma) through Ollama's OpenAI-compatible API.

    reasoning_effort "none" turns Gemma's thinking off: measured on the development PC, a
    line took 7.6 s with thinking on and 0.36 s with it off.
    """

    MAX_TOKENS = 160
    LABEL_TOKENS = 32  # room for a small JSON object, not just a word

    def __init__(
        self, model: str, *, base_url: str = "http://localhost:11434", client: Any = None
    ) -> None:
        self.name = f"ollama/{model}"
        self.endpoint = base_url  # where requests go: local or not (nova/privacy.py)
        self._model = model
        self._client = client or AsyncOpenAI(
            base_url=f"{base_url.rstrip('/')}/v1", api_key="ollama", max_retries=0
        )

    async def write(self, *, system: str, prompt: str) -> str:
        return await self._complete(system, prompt, max_tokens=self.MAX_TOKENS, temperature=0.8)

    async def sample(self, *, system: str, prompt: str, temperature: float, seed: int) -> str:
        """Words at the caller's temperature and seed: the date benchmark's actors, each
        dater sampled its own way, the same on every run (nova/benchmark.py). One call, as
        write() is; Nova's own lines always use write()."""
        return await self._complete(
            system, prompt, max_tokens=self.MAX_TOKENS, temperature=temperature, seed=seed
        )

    async def label(
        self, *, system: str, prompt: str, schema: Mapping[str, object] | None = None
    ) -> str:
        """Temperature 0, so the same words get the same label; with a schema, Ollama's
        structured output (response_format) holds the answer to it."""
        extra: dict[str, Any] = {}
        if schema is not None:
            extra["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "label", "schema": dict(schema), "strict": True},
            }
        return await self._complete(
            system, prompt, max_tokens=self.LABEL_TOKENS, temperature=0.0, **extra
        )

    async def _complete(
        self, system: str, prompt: str, *, max_tokens: int, temperature: float, **extra: Any
    ) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            reasoning_effort="none",
            temperature=temperature,
            **extra,
        )
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise ModelError("cut off")  # half a line would be said as it stands
        content = choice.message.content
        if not content or not content.strip():
            raise ModelError("empty reply")
        return content


class AnthropicModel:
    """Claude through the official Anthropic SDK.

    Nova's rules go in a system block marked for caching. Caching only applies above the
    model's minimum cacheable prompt (512 tokens on current Claude models, 1,024 to 4,096 on
    older ones); Nova's rules are about 510 tokens and the distress check's about 810, so
    it depends on the model: usage.cache_read_input_tokens says whether it did. No
    temperature or other sampling settings
    (current Claude models reject them), and effort "low": these are short spoken lines.
    A refusal raises, and Nova says its own fallback line. No server-side refusal fallback
    for now; decision 0005 adds it back if and when Nova switches to Claude.
    """

    MAX_TOKENS = 2048  # room for adaptive thinking; the line itself is short
    # Models that reject output_config.effort (the Claude API reference): a smaller one is
    # an obvious NOVA_CLASSIFIER_LLM, and with effort every check would fail.
    NO_EFFORT = ("claude-haiku-4-5", "claude-sonnet-4-5")

    def __init__(self, model: str, *, api_key: str | None = None, client: Any = None) -> None:
        self.name = f"anthropic/{model}"
        self._model = model
        self._client = client or AsyncAnthropic(api_key=api_key, max_retries=0)
        self._effort: dict[str, Any] = {} if model.startswith(self.NO_EFFORT) else {"effort": "low"}

    async def write(self, *, system: str, prompt: str) -> str:
        return await self._create(system, prompt, dict(self._effort))

    async def label(
        self, *, system: str, prompt: str, schema: Mapping[str, object] | None = None
    ) -> str:
        """The same request as a line, with the schema as structured output
        (output_config.format), so the answer is JSON that follows it. Claude takes no
        sampling settings to make it more repeatable."""
        config: dict[str, Any] = dict(self._effort)
        if schema is not None:
            config["format"] = {"type": "json_schema", "schema": dict(schema)}
        return await self._create(system, prompt, config)

    async def _create(self, system: str, prompt: str, output_config: dict[str, Any]) -> str:
        options: dict[str, Any] = {"output_config": output_config} if output_config else {}
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self.MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
            **options,
        )
        if response.stop_reason == "refusal":
            raise ModelRefused("refused")
        if response.stop_reason == "max_tokens":
            raise ModelError("cut off")
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise ModelError("empty reply")
        return text


# The warm-up's one deadline (NOVA_WARM_UP_SECONDS, Settings.warm_up), before the date's
# own prep budget starts: cold loads took 17.6 to 28 s on the development PC, so 40 s, with
# a 20 s prep budget, keeps Nova ready within the product owner's 60 s. Its answer is
# ignored.
WARM_UP_DEADLINE = 40.0
WARM_UP_SYSTEM = "Reply with one word."
# Pauses before trying again after a failure that may pass (the server still starting, or
# overloaded), and the most tries in all: bounded, never a tight loop.
BACKOFF = (1.0, 2.0, 4.0, 8.0)
WARM_UP_ATTEMPTS = 5


class WarmUpError(StrEnum):
    TIMEOUT = "timeout"  # the deadline ran out while the one request waited
    REFUSED = "refused"
    # Rejected outright, and never tried again: a bad key, no permission, a model not
    # pulled or unknown, a parameter or request it won't take.
    REJECTED = "rejected"
    # A failure that may pass (models.retryable), still failing when the tries or the
    # deadline ran out: the doors-hold tries again (Rehearsal.hold_until_ready).
    UNAVAILABLE = "unavailable"
    ERROR = "error"  # a failure it can't tell: never tried again


@dataclass(frozen=True)
class Warmed:
    """How long the warm-up took, how many requests it made, and why it failed, if it did:
    a model that can't answer at all shows up here, never as a warm model."""

    ms: int
    error: WarmUpError | None = None
    attempts: int = 1


def retryable(error: BaseException) -> bool:
    """A failure that may pass, worth one more try after a pause: the server not up yet or
    unreachable for a moment, overloaded, rate-limited or failing on its side. Never a bad
    key, a missing model or a request it rejects, and never something we can't tell."""
    for sdk in (openai, anthropic):
        if isinstance(error, sdk.APIConnectionError):  # refused, reset, or its own timeout
            return True
        if isinstance(error, sdk.APIStatusError):
            return error.status_code == 429 or error.status_code >= 500
    return False


def _rejected(error: BaseException) -> bool:
    return any(isinstance(error, sdk.APIStatusError) for sdk in (openai, anthropic))


async def warm_up(
    model: LineModel,
    *,
    deadline: float = WARM_UP_DEADLINE,
    backoff: Sequence[float] = BACKOFF,
    attempts: int = WARM_UP_ATTEMPTS,
) -> Warmed:
    """A tiny request, so a cold model loads now and not inside the prep budget, or on a
    2 s distress check that gives up and starts the load again. One request is held for
    whatever is left of the deadline: cancelling it would abort the load (decision 0005),
    so it is never cut short to try again. Only a fast failure that may pass is tried again
    (retryable), after a pause, within the deadline and at most `attempts` times; anything
    else ends it at once. The answer is ignored; whether there was one, how long it took
    and how many tries it took are not."""
    started = time.monotonic()
    tried = 0
    while True:
        tried += 1
        left = deadline - (time.monotonic() - started)
        try:
            await asyncio.wait_for(model.label(system=WARM_UP_SYSTEM, prompt="Say ok."), left)
            return Warmed(ms_since(started), None, tried)
        except TimeoutError:
            error = WarmUpError.TIMEOUT
        except ModelRefused:
            error = WarmUpError.REFUSED
        except Exception as caught:  # any failure is reported, never raised
            if retryable(caught):
                pause = backoff[min(tried - 1, len(backoff) - 1)] if backoff else 0.0
                in_time = time.monotonic() - started + pause < deadline
                if tried < attempts and in_time:
                    await asyncio.sleep(pause)
                    continue
                error = WarmUpError.UNAVAILABLE
            else:
                error = WarmUpError.REJECTED if _rejected(caught) else WarmUpError.ERROR
        return Warmed(ms_since(started), error, tried)


def build_model(settings: Settings, choice: LlmChoice | None = None) -> LineModel:
    """The model NOVA_LLM names (or choice). Changing models is an .env change, not a code
    change."""
    choice = choice or settings.llm
    if choice.provider == "anthropic":
        return AnthropicModel(choice.model, api_key=settings.anthropic_api_key)
    return OllamaModel(choice.model, base_url=settings.ollama_base_url)


def build_classifier(settings: Settings) -> LineModel:
    """The model for the distress check's labels: NOVA_CLASSIFIER_LLM if set, so a smaller
    model can label without queueing behind live lines on the same Ollama; else NOVA_LLM."""
    return build_model(settings, settings.classifier or settings.llm)
