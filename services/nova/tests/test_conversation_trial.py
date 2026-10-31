"""The local probe is synthetic-only and refuses remote recipients before any work."""

import json

import pytest
from test_rehearsal import FakeModel

from nova import conversation_trial
from nova.config import LlmChoice, Settings


class ProbeModel(FakeModel):
    name = "ollama/test"
    endpoint = "http://localhost:11434"


def configure(monkeypatch, model):
    settings = Settings(
        livekit_url="wss://example.invalid",
        livekit_api_key="test",
        livekit_api_secret="test",
        llm=LlmChoice("ollama", "test"),
    )
    monkeypatch.setattr(conversation_trial, "load_settings", lambda: settings)
    monkeypatch.setattr(conversation_trial, "build_model", lambda _: model)
    monkeypatch.setattr(conversation_trial, "build_classifier", lambda _: model)


async def test_remote_recipient_is_rejected_before_warmup_or_inference(monkeypatch):
    model = ProbeModel()
    model.endpoint = "https://remote.invalid"
    configure(monkeypatch, model)
    with pytest.raises(ValueError, match="approved-local"):
        await conversation_trial.trial()
    assert model.prompts == [] and model.checked == []


async def test_probe_sends_only_fixed_synthetic_examples_and_reports_no_dialogue(monkeypatch):
    model = ProbeModel()
    configure(monkeypatch, model)

    async def warm(*args, **kwargs):
        pass

    monkeypatch.setattr(conversation_trial, "warm_up", warm)
    report = await conversation_trial.trial(rounds=1)
    assert model.checked == [conversation_trial.SENTENCE] * 6, "3 alone, 3 with a prefix"
    assert report["synthetic_only"] and not report["safety_recall_measured"]
    assert report["human_audio_acceptance"] == "pending"
    assert report["unchecked_probes"] == 0
    assert report["prefixes"]["attempts"] == 3 and "passed" in report["gate"]
    rendered = json.dumps(report)
    assert "pancakes" not in rendered and "hiking" not in rendered


PASSING = {
    "unchecked_probes": 0,
    "unexpected_probe_verdicts": 0,
    "safety_probes_with_prefix": {"p99_ms": 1500},
}


@pytest.mark.parametrize(
    ("change", "failing"),
    [
        ({}, False),
        ({"unchecked_probes": 1}, True),
        ({"unexpected_probe_verdicts": 2}, True),
        ({"safety_probes_with_prefix": {"p99_ms": 1501}}, True),
    ],
)
def test_the_gate_fails_on_a_timeout_a_changed_verdict_or_a_slow_tail(change, failing):
    """Three quarters of the 2 s distress timeout: 1,500 ms for the slowest 1%."""
    failures = conversation_trial.gate(PASSING | change, timeout=2.0)
    assert bool(failures) is failing


def test_a_failing_gate_exits_non_zero_with_the_report(monkeypatch, capsys):
    async def failing(rounds):
        assert rounds == 2
        return {"gate": {"passed": False, "failures": ["1 safety checks went unchecked"]}}

    monkeypatch.setattr(conversation_trial, "trial", failing)
    with pytest.raises(SystemExit) as exit_:
        conversation_trial.main(["--rounds", "2"])
    assert exit_.value.code == 1 and "unchecked" in capsys.readouterr().out
