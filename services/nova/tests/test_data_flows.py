"""The data-flow inventory (nova/data_flows.py, decision 0010) under each configuration, and
the draft dater notice held to it: every third party the settings send to is named, and
said to receive the safety context or dialogue memory when it does; it stays marked as a
draft not shipped; and it claims nothing the repository can't show."""

import re
from pathlib import Path

import pytest

from nova import data_flows
from nova.config import Settings
from nova.data_flows import NOVA, UNKNOWN, flows, not_in_notice, received, third_parties
from nova.privacy import Data

BASE = {
    "LIVEKIT_URL": "wss://example.livekit.cloud",
    "LIVEKIT_API_KEY": "APIkey",
    "LIVEKIT_API_SECRET": "super-secret-value",
    "NOVA_LLM": "ollama/gemma4:26b",
}
CLAUDE = {"NOVA_LLM": "anthropic/claude-haiku-4-5", "ANTHROPIC_API_KEY": "sk-test-value"}
HOSTED_CLASSIFIER = {
    "NOVA_CLASSIFIER_LLM": "anthropic/claude-haiku-4-5",
    "ANTHROPIC_API_KEY": "sk-test-value",
}
REMOTE = {"OLLAMA_BASE_URL": "http://gpu.example:11434"}
SPEECH = {"LiveKit", "Deepgram", "Cartesia"}


def settings(**env: str) -> Settings:
    return Settings.from_env({**BASE, **env})


def to(s: Settings, recipient: str) -> frozenset[Data]:
    return received(s).get(recipient, frozenset())


# --- Who receives what --------------------------------------------------------------------------


def test_by_default_only_the_speech_services_are_third_parties():
    s = settings()
    assert third_parties(s) == SPEECH
    models = to(s, "Ollama at localhost")
    assert Data.DATE_CONTEXT in models and Data.TRANSCRIPT in models and Data.PROFILE in models
    assert Data.RAW_AUDIO not in models, "models never hear audio"


def test_daters_raw_audio_goes_to_livekit_and_speech_recognition_only():
    s = settings(**CLAUDE)
    audio = {who for who, data in received(s).items() if Data.RAW_AUDIO in data}
    assert audio == {"LiveKit Cloud", "LiveKit Inference, then Deepgram"}


def test_the_voice_receives_novas_text_never_a_daters_audio():
    voice = to(settings(), "LiveKit Inference, then Cartesia")
    assert Data.NOVA_TEXT in voice and Data.RAW_AUDIO not in voice


def test_a_hosted_model_receives_what_it_needs_but_not_this_date_until_approved():
    s = settings(**CLAUDE)
    assert third_parties(s) == SPEECH | {"Anthropic"}
    anthropic = to(s, "Anthropic")
    assert {Data.TRANSCRIPT, Data.PROFILE, Data.AUDIENCE, Data.NOVA_TEXT} <= anthropic
    assert Data.DATE_CONTEXT not in anthropic
    opted_in = settings(**CLAUDE, NOVA_DIALOGUE_TO_PROVIDER="true")
    assert Data.DATE_CONTEXT in to(opted_in, "Anthropic")


@pytest.mark.parametrize("live", ["false", "true"])
def test_a_hosted_classifier_never_gets_this_date_but_the_local_line_model_does(live):
    s = settings(**HOSTED_CLASSIFIER, NOVA_MEANING_LIVE=live)
    assert Data.DATE_CONTEXT in to(s, "Ollama at localhost")
    assert Data.SAFETY_CONTEXT in to(s, "Anthropic"), "the safety check's window, as frozen"
    assert Data.DATE_CONTEXT not in to(s, "Anthropic")


def test_with_the_opt_in_a_live_meaning_check_on_a_hosted_classifier_gets_this_date():
    s = settings(**HOSTED_CLASSIFIER, NOVA_MEANING_LIVE="true", NOVA_DIALOGUE_TO_PROVIDER="true")
    assert Data.DATE_CONTEXT in to(s, "Anthropic")


def test_an_ollama_on_another_machine_is_a_third_party_unless_approved():
    s = settings(**REMOTE)
    assert "Ollama at gpu.example" in third_parties(s)
    assert Data.DATE_CONTEXT not in to(s, "Ollama at gpu.example")
    approved = settings(**REMOTE, NOVA_APPROVED_LOCAL_HOSTS="gpu.example")
    assert third_parties(approved) == SPEECH
    assert Data.DATE_CONTEXT in to(approved, "Ollama at gpu.example")


def test_dockers_host_is_a_third_party_unless_approved():
    docker = {"OLLAMA_BASE_URL": "http://host.docker.internal:11434"}
    assert "Ollama at host.docker.internal" in third_parties(settings(**docker))
    approved = settings(**docker, NOVA_APPROVED_LOCAL_HOSTS="host.docker.internal")
    assert third_parties(approved) == SPEECH


# --- Conversation-derived safety context is not dialogue memory -------------------------------

LOCAL_CLASSIFIER = {**CLAUDE, "NOVA_CLASSIFIER_LLM": "ollama/gemma4:e4b"}
APPROVED = {**REMOTE, "NOVA_APPROVED_LOCAL_HOSTS": "gpu.example"}
CHECKS = {
    # name: (settings, who receives the safety context, whether that's a third party)
    "local Ollama for both": ({}, "Ollama at localhost", False),
    "local lines, hosted classifier": (HOSTED_CLASSIFIER, "Anthropic", True),
    "hosted lines, local classifier": (LOCAL_CLASSIFIER, "Ollama at localhost", False),
    "hosted, no separate classifier": (CLAUDE, "Anthropic", True),
    "remote Ollama": (REMOTE, "Ollama at gpu.example", True),
    "remote Ollama, approved": (APPROVED, "Ollama at gpu.example", False),
}


def getting(s: Settings, kind: Data) -> set[str]:
    """Who, besides Nova itself, receives this kind of data."""
    return {who for who, data in received(s).items() if kind in data}


@pytest.mark.parametrize("config", CHECKS)
def test_the_safety_context_goes_to_the_classifier_wherever_it_is(config):
    env, receiver, third_party = CHECKS[config]
    s = settings(**env)
    assert getting(s, Data.SAFETY_CONTEXT) == {receiver}
    assert (receiver in third_parties(s)) is third_party
    (safety,) = [f for f in flows(s) if f.recipient != NOVA and Data.SAFETY_CONTEXT in f.data]
    assert safety.data == {Data.SAFETY_CONTEXT}, "never dialogue memory"
    assert "Nova muted or not" in safety.when
    assert "NOVA_DIALOGUE_TO_PROVIDER doesn't apply" in safety.when


@pytest.mark.parametrize("config", CHECKS)
@pytest.mark.parametrize("live", ["false", "true"])
def test_the_opt_in_moves_dialogue_memory_never_the_safety_context(config, live):
    env = CHECKS[config][0]
    off = settings(**env, NOVA_MEANING_LIVE=live, NOVA_DIALOGUE_TO_PROVIDER="false")
    on = settings(**env, NOVA_MEANING_LIVE=live, NOVA_DIALOGUE_TO_PROVIDER="true")
    assert getting(off, Data.SAFETY_CONTEXT) == getting(on, Data.SAFETY_CONTEXT)
    assert getting(off, Data.DATE_CONTEXT) <= getting(on, Data.DATE_CONTEXT)
    hosted = {who for who in getting(on, Data.DATE_CONTEXT) if who in third_parties(on)}
    assert not hosted & getting(off, Data.DATE_CONTEXT), "a provider only with the opt-in"


@pytest.mark.parametrize("env", [{}, CLAUDE, HOSTED_CLASSIFIER, REMOTE])
def test_retention_and_training_are_unknown_outside_novas_own_memory(env):
    """Nothing in the repository establishes a provider's terms: never guessed."""
    for flow in flows(settings(**env)):
        if flow.recipient == NOVA:
            assert flow.retention.startswith("memory only")
        else:
            assert (flow.retention, flow.training) == (UNKNOWN, UNKNOWN), flow


def test_speech_recognition_carries_on_while_nova_is_muted():
    """The audit and the notice say so: if a mute ever stops the microphone's audio, both
    must change with it."""
    (microphone,) = [f for f in flows(settings()) if f.purpose == "speech recognition"]
    assert "whether or not Nova is muted" in microphone.when
    nova = Path(__file__).parents[1] / "nova"
    for path in nova.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"set_audio_enabled|audio_enabled\s*=", text), path.name


def test_the_printed_flows_hold_no_secret(monkeypatch, capsys):
    monkeypatch.setattr(data_flows, "load_settings", lambda: settings(**CLAUDE))
    data_flows.main()
    out = capsys.readouterr().out
    assert "Anthropic" in out and "UNKNOWN" in out
    assert "super-secret-value" not in out and "sk-test-value" not in out


# --- The draft notice --------------------------------------------------------------------------

_PARENTS = Path(__file__).resolve().parents
NOTICE = _PARENTS[3] / "docs" / "privacy" / "dater-notice-draft.md" if len(_PARENTS) > 3 else None
needs_notice = pytest.mark.skipif(
    NOTICE is None or not NOTICE.exists(),
    reason="docs/ isn't in this checkout (the Docker image holds services/nova only)",
)


def notice() -> str:
    assert NOTICE is not None
    return NOTICE.read_text(encoding="utf-8")


@needs_notice
def test_the_notice_stays_a_draft_not_shipped():
    assert "**DRAFT / NOT SHIPPED.**" in notice().splitlines()[2]


@needs_notice
@pytest.mark.parametrize(
    "env",
    [{}, CLAUDE, HOSTED_CLASSIFIER, {**CLAUDE, "NOVA_DIALOGUE_TO_PROVIDER": "true"}],
)
def test_the_notice_names_every_third_party_these_settings_send_to(env):
    assert not_in_notice(settings(**env), notice()) == []


@needs_notice
def test_a_configuration_the_notice_doesnt_cover_is_caught():
    """An Ollama on someone else's machine isn't in the notice: it must be, first."""
    assert not_in_notice(settings(**REMOTE), notice()) == ["Ollama at gpu.example"]


@needs_notice
@pytest.mark.parametrize(
    "claim",
    [
        r"never (?:be )?(?:stored|saved|kept|recorded)",
        r"never leaves?",
        r"(?:don't|do not|never|won't|will not) (?:share|sell)",
        r"\bdeleted\b",
        r"\b\d+ (?:hours?|days?|weeks?|months?|years?)\b",
        r"\b(?:anonymi[sz]ed|encrypted|secure|compliant|GDPR)\b",
    ],
)
def test_the_notice_claims_nothing_the_repository_cant_show(claim):
    text = notice().split("## The notice", 1)[1]
    assert not re.search(claim, text, re.IGNORECASE), claim


SAYS = {
    # What the notice must say, in a paragraph naming the company, for each kind it gets.
    Data.SAFETY_CONTEXT: "in the 15 seconds before it",
    Data.DATE_CONTEXT: "earlier on the date",
}


def paragraphs() -> list[str]:
    body = notice().split("## The notice", 1)[1]
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", body) if p.strip()]


def undisclosed(s: Settings) -> list[str]:
    """Each third party that receives the safety context or dialogue memory under these
    settings, without a paragraph of the notice that names it and says so."""
    missing = []
    for who, data in sorted(received(s).items()):
        for kind, words in SAYS.items():
            told = any(who in p and words in p for p in paragraphs())
            if kind in data and who in third_parties(s) and not told:
                missing.append(f"{who}: {kind}")
    return missing


@needs_notice
@pytest.mark.parametrize("config", [c for c in CHECKS if c != "remote Ollama"])
@pytest.mark.parametrize("opted_in", [False, True])
def test_the_notice_discloses_who_gets_the_safety_context_and_dialogue_memory(config, opted_in):
    extra = {"NOVA_MEANING_LIVE": "true", "NOVA_DIALOGUE_TO_PROVIDER": "true"} if opted_in else {}
    assert undisclosed(settings(**CHECKS[config][0], **extra)) == []


@needs_notice
def test_a_safety_context_transfer_the_notice_doesnt_cover_is_caught():
    assert undisclosed(settings(**REMOTE)) == [
        "Ollama at gpu.example: conversation-derived safety context"
    ]


@needs_notice
def test_the_notice_keeps_the_safety_context_with_the_checks_not_the_lines():
    """The two models are disclosed apart: the one that writes the lines never gets the
    safety context, and the one that runs the checks gets it for the whole date."""
    (lines,) = [p for p in paragraphs() if p.startswith("[IF ANTHROPIC WRITES THE LINES")]
    (checks,) = [p for p in paragraphs() if p.startswith("[IF ANTHROPIC RUNS THE CHECKS")]
    assert SAYS[Data.SAFETY_CONTEXT] not in lines
    assert SAYS[Data.SAFETY_CONTEXT] in checks and "including while Nova is muted" in checks


@needs_notice
def test_the_notice_says_what_is_still_to_confirm():
    text = notice()
    for owed in ("how long it keeps", "train AI models", "who is responsible for your data"):
        assert owed in text
    assert "while Nova is\nmuted" in text or "while Nova is muted" in text
