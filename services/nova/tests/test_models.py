"""The language-model adapters, checked against fake SDK clients: no network, no cost."""

import asyncio
from types import SimpleNamespace

import pytest

from nova.config import Settings
from nova.models import (
    AnthropicModel,
    ModelRefused,
    OllamaModel,
    WarmUpError,
    build_classifier,
    build_model,
    warm_up,
)

ENV = {
    "LIVEKIT_URL": "wss://example.livekit.cloud",
    "LIVEKIT_API_KEY": "APIkey",
    "LIVEKIT_API_SECRET": "secret",
}


class Recorder:
    def __init__(self, response):
        self.calls: list[dict] = []
        self.response = response

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_openai(content: str):
    completions = Recorder(
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    )
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def claude_reply(text: str = "Theo, your turn.", stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
    )


def fake_anthropic(response):
    messages, beta = Recorder(response), Recorder(response)
    client = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=beta))
    return client, messages, beta


async def test_ollama_labels_are_short_and_repeatable_with_thinking_off():
    """The distress check: temperature 0, so the same words get the same label."""
    client, completions = fake_openai("yes")
    model = OllamaModel("gemma4:26b", client=client)
    assert await model.label(system="RULES", prompt="TASK <data>{}</data>") == "yes"
    call = completions.calls[0]
    assert (call["temperature"], call["reasoning_effort"]) == (0.0, "none")
    assert call["max_tokens"] <= 32, "a small JSON object"
    assert call["messages"][0] == {"role": "system", "content": "RULES"}
    assert "response_format" not in call, "no schema, no format"


SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string", "enum": ["yes", "maybe", "no"]}},
    "required": ["label"],
    "additionalProperties": False,
}


async def test_ollama_holds_a_label_to_its_schema_with_structured_output():
    client, completions = fake_openai('{"label": "no"}')
    model = OllamaModel("gemma4:26b", client=client)
    await model.label(system="RULES", prompt="TASK", schema=SCHEMA)
    format_ = completions.calls[0]["response_format"]
    assert format_["type"] == "json_schema" and format_["json_schema"]["schema"] == SCHEMA


async def test_claude_holds_a_label_to_its_schema_with_structured_output():
    client, messages, _ = fake_anthropic(claude_reply('{"label": "maybe"}'))
    model = AnthropicModel("claude-opus-5-5", client=client)
    assert await model.label(system="RULES", prompt="TASK", schema=SCHEMA) == '{"label": "maybe"}'
    config = messages.calls[0]["output_config"]
    assert config["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert config["effort"] == "low"


def test_the_distress_check_can_use_its_own_model():
    settings = Settings.from_env(
        {**ENV, "NOVA_LLM": "ollama/gemma4:26b", "NOVA_CLASSIFIER_LLM": "ollama/gemma3:4b"}
    )
    assert build_classifier(settings).name == "ollama/gemma3:4b"
    assert build_model(settings).name == "ollama/gemma4:26b"
    same = Settings.from_env({**ENV, "NOVA_LLM": "ollama/gemma4:26b"})
    assert build_classifier(same).name == "ollama/gemma4:26b", "NOVA_LLM by default"


async def test_ollama_turns_thinking_off_and_keeps_rules_apart_from_the_task():
    client, completions = fake_openai("Maya, over to you.")
    model = OllamaModel("gemma4:26b", client=client)
    assert await model.write(system="RULES", prompt="TASK <data>{}</data>") == "Maya, over to you."
    call = completions.calls[0]
    assert call["model"] == "gemma4:26b"
    assert call["reasoning_effort"] == "none"
    assert call["messages"] == [
        {"role": "system", "content": "RULES"},
        {"role": "user", "content": "TASK <data>{}</data>"},
    ]
    assert call["max_tokens"] <= 200
    assert model.name == "ollama/gemma4:26b"


async def test_claude_caches_the_rules_and_sends_no_sampling_settings():
    client, messages, beta = fake_anthropic(claude_reply())
    model = AnthropicModel("claude-haiku-5-5", client=client)
    assert await model.write(system="RULES", prompt="TASK") == "Theo, your turn."
    call = messages.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["system"] == [
        {"type": "text", "text": "RULES", "cache_control": {"type": "ephemeral"}}
    ]
    assert call["messages"] == [{"role": "user", "content": "TASK"}]
    assert call["output_config"] == {"effort": "low"}
    for not_sent in ("temperature", "top_p", "top_k", "thinking"):
        assert not_sent not in call
    assert beta.calls == []


@pytest.mark.parametrize("name", ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"])
async def test_no_claude_model_uses_server_side_refusal_fallbacks_for_now(name):
    """Decision 0005: added back if and when Nova switches to Claude."""
    client, messages, beta = fake_anthropic(claude_reply())
    await AnthropicModel(name, client=client).write(system="RULES", prompt="TASK")
    assert beta.calls == []
    assert len(messages.calls) == 1
    assert "betas" not in messages.calls[0] and "fallbacks" not in messages.calls[0]


async def test_a_claude_refusal_raises_so_nova_uses_a_fallback_line():
    client, *_ = fake_anthropic(claude_reply(text="", stop_reason="refusal"))
    with pytest.raises(ModelRefused):
        await AnthropicModel("claude-haiku-5-5", client=client).write(system="R", prompt="P")


def test_the_model_follows_nova_llm_with_no_code_change():
    gemma = build_model(Settings.from_env({**ENV, "NOVA_LLM": "ollama/gemma4:26b"}))
    assert isinstance(gemma, OllamaModel) and gemma.name == "ollama/gemma4:26b"
    claude = build_model(
        Settings.from_env(
            {**ENV, "NOVA_LLM": "anthropic/claude-haiku-5-5", "ANTHROPIC_API_KEY": "sk-ant-test"}
        )
    )
    assert isinstance(claude, AnthropicModel) and claude.name == "anthropic/claude-haiku-5-5"


# --- Review of main at bfaed17 ---------------------------------------------------------------

LABEL_SCHEMA = {"type": "object"}


@pytest.mark.parametrize(
    "name", ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-sonnet-4-5-20250929"]
)
async def test_claude_models_that_reject_effort_are_never_sent_it(name):
    """A smaller Claude is an obvious NOVA_CLASSIFIER_LLM: with effort, every check failed."""
    client, messages, _ = fake_anthropic(claude_reply('{"label": "no", "conduct": "none"}'))
    model = AnthropicModel(name, client=client)
    await model.write(system="RULES", prompt="TASK")
    await model.label(system="RULES", prompt="TASK", schema=LABEL_SCHEMA)
    write, label = messages.calls
    assert "output_config" not in write
    assert label["output_config"] == {"format": {"type": "json_schema", "schema": LABEL_SCHEMA}}


@pytest.mark.parametrize("name", ["claude-haiku-5-5", "claude-opus-4-5", "claude-sonnet-4-6"])
async def test_other_claude_models_are_asked_for_low_effort(name):
    client, messages, _ = fake_anthropic(claude_reply())
    await AnthropicModel(name, client=client).write(system="RULES", prompt="TASK")
    assert messages.calls[0]["output_config"] == {"effort": "low"}


class Down:
    name = "fake/down"

    def __init__(self, error: Exception | None = None, *, hang: bool = False) -> None:
        self.error, self.hang = error, hang

    async def label(self, *, system: str, prompt: str, schema=None) -> str:
        if self.hang:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return "ok"


@pytest.mark.parametrize(
    ("model", "error"),
    [
        (Down(RuntimeError("connection refused")), WarmUpError.ERROR),
        (Down(ModelRefused("refused")), WarmUpError.REFUSED),
        (Down(hang=True), WarmUpError.TIMEOUT),
        (Down(), None),
    ],
)
async def test_a_warm_up_says_when_the_model_cant_answer(model, error):
    """A dead or misconfigured model used to look warm: 0 ms and no error."""
    warmed = await warm_up(model, deadline=0.05)
    assert warmed.error is error
