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


def test_conversation_defaults_and_explicit_roll_back():
    settings = Settings.from_env(GOOD)
    assert settings.conversational_turns and settings.full_rehearsal
    assert settings.conversation_overrun == 60 and not settings.live_bridges
    settings = Settings.from_env(
        {**GOOD, "NOVA_CONVERSATIONAL_TURNS": "false", "NOVA_FULL_REHEARSAL": "false"}
    )
    assert not settings.conversational_turns and not settings.full_rehearsal


@pytest.mark.parametrize(("left", "right"), [("left", ""), ("", "right"), ("same", "same")])
def test_two_person_rehearsals_require_two_distinct_explicit_identities(left, right):
    with pytest.raises(ConfigError, match="must be distinct and set together"):
        Settings.from_env({**GOOD, "NOVA_LEFT_PARTICIPANT": left, "NOVA_RIGHT_PARTICIPANT": right})


def test_both_rehearsal_identities_are_read_without_exposing_them_in_errors():
    settings = Settings.from_env(
        {**GOOD, "NOVA_LEFT_PARTICIPANT": "left", "NOVA_RIGHT_PARTICIPANT": "right"}
    )
    assert settings.left_participant == "left" and settings.right_participant == "right"


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
        # The product owner's bound (2026-10-22): Nova ready within 60 s of the session
        # starting, the warm-up's 40 and the prep budget's 20 (measured: 29 to 30 s cold).
        assert (settings.warm_up, settings.prep_budget) == (40.0, 20.0)
        assert settings.warm_up + settings.prep_budget <= 60.0
        # Then up to 120 s of tries while the doors hold (decision 0011, 2026-10-24).
        assert settings.ready_retry == 120.0
        assert settings.distress_timeout == 2.0
        assert (settings.utterance_grace, settings.utterance_max_wait) == (0.6, 1.5)
        assert settings.ollama_base_url == "http://localhost:11434"

    def test_can_be_set_in_the_env_file(self):
        settings = Settings.from_env(
            {
                **GOOD,
                "NOVA_LIVE_TIMEOUT_SECONDS": "1.5",
                "NOVA_PREP_TIMEOUT_SECONDS": "90",
                "NOVA_PREP_BUDGET_SECONDS": "30",
                "NOVA_DISTRESS_TIMEOUT_SECONDS": "0.8",
                "NOVA_UTTERANCE_GRACE_SECONDS": "0.4",
                "NOVA_UTTERANCE_MAX_WAIT_SECONDS": "1",
                "NOVA_READY_RETRY_SECONDS": "45",
                "OLLAMA_BASE_URL": "http://gpu-box:11434",
            }
        )
        assert settings.ready_retry == 45.0
        assert (settings.live_timeout, settings.prep_timeout) == (1.5, 90.0)
        assert settings.prep_budget == 30.0
        assert settings.distress_timeout == 0.8
        assert (settings.utterance_grace, settings.utterance_max_wait) == (0.4, 1.0)
        assert settings.ollama_base_url == "http://gpu-box:11434"

    @pytest.mark.parametrize(
        "name",
        [
            "NOVA_LIVE_TIMEOUT_SECONDS",
            "NOVA_PREP_BUDGET_SECONDS",
            "NOVA_DISTRESS_TIMEOUT_SECONDS",
            "NOVA_UTTERANCE_GRACE_SECONDS",
            "NOVA_UTTERANCE_MAX_WAIT_SECONDS",
            "NOVA_READY_RETRY_SECONDS",
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


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NOVA_LLM", "gemma-secret-value"),
        ("NOVA_LLM", "openai/gpt-secret-value"),
        ("NOVA_VOICE", "secret-voice-value"),
        ("NOVA_STT", "secret-stt-value"),
        ("NOVA_LIVE_TIMEOUT_SECONDS", "secret-timeout-value"),
    ],
)
def test_a_config_error_names_the_setting_never_its_value(name, value):
    """ConfigError's promise: a value may be a key pasted into the wrong line."""
    with pytest.raises(ConfigError, match=name) as raised:
        Settings.from_env({**GOOD, name: value})
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("stt", ["deepgram/nova-3", "assemblyai/universal-streaming"])
def test_speech_recognition_is_provider_slash_model(stt):
    assert Settings.from_env({**GOOD, "NOVA_STT": stt}).stt == stt


@pytest.mark.parametrize("stt", ["deepgram", "/nova-3", "deepgram/", "deepgram/nova-3:en"])
def test_a_malformed_speech_recognition_setting_is_caught(stt):
    with pytest.raises(ConfigError, match="NOVA_STT"):
        Settings.from_env({**GOOD, "NOVA_STT": stt})


# --- Review of main at bfaed17 ---------------------------------------------------------------


@pytest.mark.parametrize("value", ["gemma-secret-value", "openai/gpt-secret-value"])
def test_a_malformed_classifier_setting_is_named_itself_not_nova_llm(value):
    with pytest.raises(ConfigError, match="NOVA_CLASSIFIER_LLM") as raised:
        Settings.from_env({**GOOD, "NOVA_CLASSIFIER_LLM": value})
    assert "secret" not in str(raised.value)


def test_set_pieces_requested_at_once_is_a_setting_one_by_default():
    assert Settings.from_env(GOOD).prep_parallel == 1
    assert Settings.from_env({**GOOD, "NOVA_PREP_PARALLEL": "4"}).prep_parallel == 4


@pytest.mark.parametrize("value", ["0", "-1", "two", "1.5", "secret-count"])
def test_a_malformed_prep_parallel_is_caught_without_its_value(value):
    with pytest.raises(ConfigError, match="NOVA_PREP_PARALLEL") as raised:
        Settings.from_env({**GOOD, "NOVA_PREP_PARALLEL": value})
    assert "secret" not in str(raised.value)


def test_docker_never_copies_an_env_file_from_any_folder():
    """Like .gitignore, a .dockerignore pattern without **/ matches only at the root."""
    ignore = (Path(__file__).parents[1] / ".dockerignore").read_text(encoding="utf-8")
    patterns = [line for line in ignore.splitlines() if ".env" in line and not line.startswith("#")]
    assert patterns and all(p.lstrip("!").startswith("**/") for p in patterns)


def test_docker_runs_the_tests_without_an_env_file():
    compose = (Path(__file__).parents[1] / "compose.yaml").read_text(encoding="utf-8")
    assert "required: false" in compose and "host.docker.internal:host-gateway" in compose


CLAUDE = {**GOOD, "NOVA_LLM": "anthropic/claude-haiku-5-5", "ANTHROPIC_API_KEY": "sk-test"}


@pytest.mark.parametrize(
    ("env", "approved"),
    [
        (GOOD, False),
        (CLAUDE, False),
        ({**CLAUDE, "NOVA_DIALOGUE_TO_PROVIDER": "true"}, True),
        ({**CLAUDE, "NOVA_DIALOGUE_TO_PROVIDER": "false"}, False),
    ],
)
def test_sending_a_live_date_s_turns_to_a_provider_is_a_setting_off_by_default(env, approved):
    """Decision 0010: who may receive them is the privacy policy's to say (nova/privacy.py,
    tests/test_privacy.py), from this and the model's endpoint."""
    assert Settings.from_env(env).dialogue_to_provider is approved


class TestApprovedLocalHosts:
    """Hosts besides loopback that count as local (decision 0010): none unless named."""

    def test_none_by_default_not_even_dockers_host(self):
        assert Settings.from_env(GOOD).approved_local_hosts == frozenset()

    def test_named_hosts_separated_by_commas(self):
        env = {**GOOD, "NOVA_APPROVED_LOCAL_HOSTS": " host.docker.internal, GPU-Box.lan ,"}
        hosts = Settings.from_env(env).approved_local_hosts
        assert hosts == {"host.docker.internal", "gpu-box.lan"}

    @pytest.mark.parametrize("value", ["*", "http://gpu.lan", "gpu.lan:11434", "gpu lan"])
    def test_only_bare_host_names(self, value):
        with pytest.raises(ConfigError, match="NOVA_APPROVED_LOCAL_HOSTS must be host names"):
            Settings.from_env({**GOOD, "NOVA_APPROVED_LOCAL_HOSTS": value})


class TestSdkRequestLogs:
    """ANTHROPIC_LOG or OPENAI_LOG at "debug" makes the SDK log every request, prompt and
    all: daters' words. Refused unless a run on made-up daters asks for it."""

    @pytest.mark.parametrize("name", ["ANTHROPIC_LOG", "OPENAI_LOG"])
    @pytest.mark.parametrize("value", ["debug", " DEBUG "])
    def test_refused(self, name, value):
        with pytest.raises(ConfigError, match=f"{name} at debug would log every prompt"):
            Settings.from_env({**CLAUDE, name: value})

    @pytest.mark.parametrize("value", ["info", "", "warning"])
    def test_levels_that_log_no_request_are_fine(self, value):
        assert not Settings.from_env({**CLAUDE, "ANTHROPIC_LOG": value}).synthetic_diagnostics

    def test_allowed_only_with_the_flag_for_synthetic_runs(self):
        env = {**CLAUDE, "ANTHROPIC_LOG": "debug", "NOVA_SYNTHETIC_DIAGNOSTICS": "true"}
        assert Settings.from_env(env).synthetic_diagnostics


def test_a_malformed_dialogue_setting_is_caught():
    with pytest.raises(ConfigError, match="NOVA_DIALOGUE_TO_PROVIDER must be true or false"):
        Settings.from_env({**CLAUDE, "NOVA_DIALOGUE_TO_PROVIDER": "maybe"})


def test_the_meaning_check_is_on_for_set_pieces_and_off_for_live_lines_by_default():
    """Decision 0005, launch requirement 1: the product owner's choice of 2026-10-23."""
    settings = Settings.from_env(GOOD)
    assert (settings.meaning_check, settings.meaning_live, settings.meaning_timeout) == (
        True,
        False,
        5.0,
    )


@pytest.mark.parametrize(
    ("values", "check", "live"),
    [
        ({"NOVA_MEANING_CHECK": "false"}, False, False),
        ({"NOVA_MEANING_LIVE": "true"}, True, True),
        ({"NOVA_MEANING_CHECK": "0", "NOVA_MEANING_LIVE": "1"}, False, True),
    ],
)
def test_the_meaning_check_can_be_turned_on_and_off(values, check, live):
    settings = Settings.from_env({**GOOD, **values})
    assert (settings.meaning_check, settings.meaning_live) == (check, live)


def test_a_malformed_meaning_setting_is_caught():
    with pytest.raises(ConfigError, match="NOVA_MEANING_LIVE must be true or false"):
        Settings.from_env({**GOOD, "NOVA_MEANING_LIVE": "sometimes"})
    with pytest.raises(ConfigError, match="NOVA_MEANING_TIMEOUT_SECONDS"):
        Settings.from_env({**GOOD, "NOVA_MEANING_TIMEOUT_SECONDS": "soon"})
