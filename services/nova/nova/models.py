"""The language models that write Nova's lines, chosen by NOVA_LLM ("provider/model").

Both adapters make one request per line with retries off: if a request is slow or fails,
Nova uses a fallback line instead of waiting.
"""

from typing import Any, Protocol

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from nova.config import Settings


class ModelError(Exception):
    """The model gave no usable line."""


class ModelRefused(ModelError):
    """The model declined (Claude's refusal stop reason)."""


class LineModel(Protocol):
    name: str

    async def write(self, *, system: str, prompt: str) -> str: ...


class OllamaModel:
    """A local model (development: Gemma) through Ollama's OpenAI-compatible API.

    reasoning_effort "none" turns Gemma's thinking off: measured on the development PC, a
    line took 7.6 s with thinking on and 0.36 s with it off.
    """

    MAX_TOKENS = 160

    def __init__(
        self, model: str, *, base_url: str = "http://localhost:11434", client: Any = None
    ) -> None:
        self.name = f"ollama/{model}"
        self._model = model
        self._client = client or AsyncOpenAI(
            base_url=f"{base_url.rstrip('/')}/v1", api_key="ollama", max_retries=0
        )

    async def write(self, *, system: str, prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.MAX_TOKENS,
            reasoning_effort="none",
            temperature=0.8,
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ModelError("empty reply")
        return content


# Server-side refusal fallbacks (beta), on by default for these models. Nova still has its
# own fallback line if the whole chain declines.
SERVER_FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})


class AnthropicModel:
    """Claude through the official Anthropic SDK.

    Nova's rules go in a cached system block. No temperature or other sampling settings
    (current Claude models reject them), and effort "low": these are short spoken lines.
    """

    MAX_TOKENS = 2048  # room for adaptive thinking; the line itself is short

    def __init__(self, model: str, *, api_key: str | None = None, client: Any = None) -> None:
        self.name = f"anthropic/{model}"
        self._model = model
        self._client = client or AsyncAnthropic(api_key=api_key, max_retries=0)

    async def write(self, *, system: str, prompt: str) -> str:
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self.MAX_TOKENS,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": "low"},
        }
        if self._model in SERVER_FALLBACK_MODELS:
            response = await self._client.beta.messages.create(
                **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        else:
            response = await self._client.messages.create(**request)

        if response.stop_reason == "refusal":
            raise ModelRefused("refused")
        if response.stop_reason == "max_tokens":
            raise ModelError("cut off")
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise ModelError("empty reply")
        return text


def build_model(settings: Settings) -> LineModel:
    """The model NOVA_LLM names. Changing models is an .env change, not a code change."""
    choice = settings.llm
    if choice.provider == "anthropic":
        return AnthropicModel(choice.model, api_key=settings.anthropic_api_key)
    return OllamaModel(choice.model, base_url=settings.ollama_base_url)
