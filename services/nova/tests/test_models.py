"""The language-model adapters, checked against fake SDK clients: no network, no cost."""

from types import SimpleNamespace

import pytest

from nova.config import Settings
from nova.models import AnthropicModel, ModelRefused, OllamaModel, build_model

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
    assert call["max_tokens"] <= 8
    assert call["messages"][0] == {"role": "system", "content": "RULES"}


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
