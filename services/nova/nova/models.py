"""The language models that write Nova's lines, chosen by NOVA_LLM ("provider/model").

Both adapters make one request per line with retries off: if a request is slow or fails,
Nova uses a fallback line instead of waiting.
"""

import asyncio
import contextlib
import time
from collections.abc import Mapping
from typing import Any, Protocol

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from nova.config import LlmChoice, Settings


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
        self._model = model
        self._client = client or AsyncOpenAI(
            base_url=f"{base_url.rstrip('/')}/v1", api_key="ollama", max_retries=0
        )

    async def write(self, *, system: str, prompt: str) -> str:
        return await self._complete(system, prompt, max_tokens=self.MAX_TOKENS, temperature=0.8)

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
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ModelError("empty reply")
        return content


class AnthropicModel:
    """Claude through the official Anthropic SDK.

    Nova's rules go in a cached system block. No temperature or other sampling settings
    (current Claude models reject them), and effort "low": these are short spoken lines.
    A refusal raises, and Nova says its own fallback line. No server-side refusal fallback
    for now; decision 0005 adds it back if and when Nova switches to Claude.
    """

    MAX_TOKENS = 2048  # room for adaptive thinking; the line itself is short

    def __init__(self, model: str, *, api_key: str | None = None, client: Any = None) -> None:
        self.name = f"anthropic/{model}"
        self._model = model
        self._client = client or AsyncAnthropic(api_key=api_key, max_retries=0)

    async def write(self, *, system: str, prompt: str) -> str:
        return await self._create(system, prompt, {"effort": "low"})

    async def label(
        self, *, system: str, prompt: str, schema: Mapping[str, object] | None = None
    ) -> str:
        """The same request as a line, with the schema as structured output
        (output_config.format), so the answer is JSON that follows it. Claude takes no
        sampling settings to make it more repeatable."""
        config: dict[str, Any] = {"effort": "low"}
        if schema is not None:
            config["format"] = {"type": "json_schema", "schema": dict(schema)}
        return await self._create(system, prompt, config)

    async def _create(self, system: str, prompt: str, output_config: dict[str, Any]) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self.MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
        )
        if response.stop_reason == "refusal":
            raise ModelRefused("refused")
        if response.stop_reason == "max_tokens":
            raise ModelError("cut off")
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise ModelError("empty reply")
        return text


# The warm-up's wait: long enough for the 20-28 s cold load measured on the development PC,
# short next to the prep timeout. Its answer, or failure, is ignored.
WARM_UP_TIMEOUT = 30.0
WARM_UP_SYSTEM = "Reply with one word."


async def warm_up(model: LineModel, *, timeout: float = WARM_UP_TIMEOUT) -> int:
    """One tiny request, so a cold model loads now. Without it, a short timeout (the
    distress check's 2 s) gives up on the load, the next request starts it again, and every
    check fails until something waits long enough. Returns how long it took, in ms; the
    answer, or a failure, is ignored."""
    started = time.monotonic()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(model.label(system=WARM_UP_SYSTEM, prompt="Say ok."), timeout)
    return round((time.monotonic() - started) * 1000)


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
