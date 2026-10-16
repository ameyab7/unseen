import re
from pathlib import Path

import pytest

from nova.config import ConfigError, LlmChoice, Settings, load_settings

GOOD = {
    "LIVEKIT_URL": "wss://example.livekit.cloud",
    "LIVEKIT_API_KEY": "APIkey",
    "LIVEKIT_API_SECRET": "super-secret-value",
    "NOVA_LLM": "ollama/gemma4:26b",
}


class TestLlmChoice:
    def test_splits_provider_from_model_at_the_first_slash(self):
        assert LlmChoice.parse("ollama/gemma4:26b") == LlmChoice("ollama", "gemma4:26b")
        assert LlmChoice.parse(" anthropic/claude-haiku-5-5 ") == LlmChoice(
            "anthropic", "claude-haiku-5-5"
        )

    @pytest.mark.parametrize("value", ["gemma4:26b", "ollama/", "/gemma4", ""])
    def test_rejects_a_value_without_provider_and_model(self, value):
        with pytest.raises(ConfigError, match='NOVA_LLM must look like "provider/model"'):
            LlmChoice.parse(value)

    def test_rejects_an_unknown_provider(self):
        with pytest.raises(ConfigError, match="provider must be one of anthropic, ollama"):
            LlmChoice.parse("openai/gpt-5")


class TestSpeech:
    def test_nova_has_a_voice_and_hears_daters_by_default(self):
        settings = Settings.from_env(GOOD)
        assert settings.voice == "cartesia/sonic-3:f31cc6a7-c1e8-4764-980c-60a361443dd1"
        assert settings.stt == "deepgram/nova-3"

    def test_the_voice_and_speech_recognition_are_settings(self):
        settings = Settings.from_env(
            {**GOOD, "NOVA_VOICE": " deepgram/aura-2:odysseus ", "NOVA_STT": "deepgram/flux"}
        )
        assert (settings.voice, settings.stt) == ("deepgram/aura-2:odysseus", "deepgram/flux")

    @pytest.mark.parametrize("value", ["odysseus", "deepgram/", "/aura-2:odysseus"])
    def test_a_voice_needs_a_provider_and_model(self, value):
        with pytest.raises(ConfigError, match='NOVA_VOICE must look like "provider/model'):
            Settings.from_env({**GOOD, "NOVA_VOICE": value})


class TestSettings:
    def test_reads_every_value(self):
        settings = Settings.from_env(GOOD)
        assert settings.livekit_url == "wss://example.livekit.cloud"
        assert settings.livekit_api_key == "APIkey"
        assert settings.livekit_api_secret == "super-secret-value"
        assert settings.llm == LlmChoice("ollama", "gemma4:26b")
        assert settings.anthropic_api_key is None

    def test_names_every_missing_value_at_once(self):
        with pytest.raises(ConfigError) as error:
            Settings.from_env({"NOVA_LLM": "ollama/gemma4:26b", "LIVEKIT_API_KEY": ""})
        assert str(error.value) == (
            "Missing in services/nova/.env: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET"
        )

    def test_needs_a_claude_key_only_for_claude(self):
        claude = {**GOOD, "NOVA_LLM": "anthropic/claude-haiku-5-5"}
        with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY is required"):
            Settings.from_env(claude)
        settings = Settings.from_env({**claude, "ANTHROPIC_API_KEY": "sk-ant-test"})
        assert settings.anthropic_api_key == "sk-ant-test"

    def test_never_prints_secrets(self):
        settings = Settings.from_env({**GOOD, "ANTHROPIC_API_KEY": "sk-ant-test"})
        shown = repr(settings) + str(settings)
        assert "super-secret-value" not in shown
        assert "sk-ant-test" not in shown
        assert "gemma4:26b" in shown


class TestLoadSettings:
    def test_environment_overrides_the_file(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("\n".join(f"{k}={v}" for k, v in GOOD.items()), encoding="utf-8")
        for name in GOOD:  # whatever this machine has set (Docker passes the real .env in)
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("NOVA_LLM", "anthropic/claude-opus-5-5")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        settings = load_settings(env_file)
        assert settings.llm == LlmChoice("anthropic", "claude-opus-5-5")
        assert settings.livekit_url == GOOD["LIVEKIT_URL"]

    def test_a_missing_file_reports_missing_values(self, tmp_path, monkeypatch):
        for name in GOOD:
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(ConfigError, match="Missing in services/nova/.env"):
            load_settings(tmp_path / "absent.env")


class TestTimeouts:
    def test_defaults(self):
        settings = Settings.from_env(GOOD)
        assert settings.live_timeout == 2.0
        assert settings.prep_timeout == 60.0
        assert settings.distress_timeout == 2.0
        assert (settings.utterance_grace, settings.utterance_max_wait) == (0.6, 1.5)
        assert settings.ollama_base_url == "http://localhost:11434"

    def test_can_be_set_in_the_env_file(self):
        settings = Settings.from_env(
            {
                **GOOD,
                "NOVA_LIVE_TIMEOUT_SECONDS": "1.5",
                "NOVA_PREP_TIMEOUT_SECONDS": "90",
                "NOVA_DISTRESS_TIMEOUT_SECONDS": "0.8",
                "NOVA_UTTERANCE_GRACE_SECONDS": "0.4",
                "NOVA_UTTERANCE_MAX_WAIT_SECONDS": "1",
                "OLLAMA_BASE_URL": "http://gpu-box:11434",
            }
        )
        assert (settings.live_timeout, settings.prep_timeout) == (1.5, 90.0)
        assert settings.distress_timeout == 0.8
        assert (settings.utterance_grace, settings.utterance_max_wait) == (0.4, 1.0)
        assert settings.ollama_base_url == "http://gpu-box:11434"

    @pytest.mark.parametrize(
        "name",
        [
            "NOVA_LIVE_TIMEOUT_SECONDS",
            "NOVA_DISTRESS_TIMEOUT_SECONDS",
            "NOVA_UTTERANCE_GRACE_SECONDS",
            "NOVA_UTTERANCE_MAX_WAIT_SECONDS",
        ],
    )
    @pytest.mark.parametrize("value", ["0", "-1", "soon", "nan", "inf"])
    def test_must_be_a_positive_number_of_seconds(self, name, value):
        with pytest.raises(ConfigError, match=f"{name} must be a positive"):
            Settings.from_env({**GOOD, name: value})

    def test_the_most_nova_waits_for_an_utterance_is_never_less_than_the_grace(self):
        env = {**GOOD, "NOVA_UTTERANCE_GRACE_SECONDS": "2", "NOVA_UTTERANCE_MAX_WAIT_SECONDS": "1"}
        with pytest.raises(ConfigError, match="MAX_WAIT_SECONDS must be at least") as raised:
            Settings.from_env(env)
        assert "2" not in str(raised.value) and "1.0" not in str(raised.value)


def test_env_example_lists_exactly_the_names_settings_reads():
    example = Path(__file__).parents[1] / ".env.example"
    names = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", example.read_text(encoding="utf-8"), re.M))
    assert names == set(Settings.ENV_NAMES)
