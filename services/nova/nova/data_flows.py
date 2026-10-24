"""Where Nova's data goes under a configuration: each flow, who receives it, what kind of data
it carries, and what's known about how long it's kept (decision 0010,
docs/privacy/nova-data-flows.md).

    uv run python -m nova.data_flows

prints it for services/nova/.env, with no secrets. The recipients come from the code that
sends to them: the models from nova/models.py, judged by the privacy policy
(nova/privacy.py); speech recognition and the voice from NOVA_STT and NOVA_VOICE, which the
agent reaches through LiveKit Inference (nova/agent.py, new_session). Retention and training
are UNKNOWN unless a contract or the provider's terms establish them, and nothing in this
repository does yet. Only what this repository runs is here: the app, recording, the
console and the rest of the backend send nothing yet.
"""

from dataclasses import dataclass

from nova.config import Settings, load_settings
from nova.models import build_classifier, build_model
from nova.privacy import Data, PrivacyPolicy, Recipient

UNKNOWN = "UNKNOWN"
NOVA = "Nova (this process)"
LIVEKIT = "LiveKit"
# The companies behind LiveKit Inference's speech models, by NOVA_STT's or NOVA_VOICE's
# provider; any other is named as written.
COMPANIES = {
    "assemblyai": "AssemblyAI",
    "cartesia": "Cartesia",
    "deepgram": "Deepgram",
    "elevenlabs": "ElevenLabs",
    "inworld": "Inworld",
    "rime": "Rime",
}


@dataclass(frozen=True)
class Flow:
    """companies: the third parties that receive it, as the notice must name them; none
    for Nova's own memory or an approved-local model."""

    source: str
    data: frozenset[Data]
    recipient: str
    purpose: str
    companies: tuple[str, ...] = ()
    when: str = "always"
    retention: str = UNKNOWN
    training: str = UNKNOWN


def flows(settings: Settings) -> list[Flow]:
    """Every flow out of a dater's microphone or Nova's process, under these settings."""
    policy = PrivacyPolicy.from_settings(settings)
    lines, checks = build_model(settings), build_classifier(settings)
    to_lines, to_checks = policy.recipient(lines), policy.recipient(checks)
    stt, voice = _company(settings.stt), _company(settings.voice)
    found = [
        Flow(
            "a dater's microphone",
            frozenset({Data.RAW_AUDIO, Data.METADATA}),
            "LiveKit Cloud",
            "carry the date's audio to Nova and the room",
            (LIVEKIT,),
        ),
        Flow(
            "a dater's microphone",
            frozenset({Data.RAW_AUDIO, Data.METADATA}),
            f"LiveKit Inference, then {stt}",
            "speech recognition",
            (LIVEKIT, stt),
            when="every frame from the session's start to its end, silences included, "
            "whether or not Nova is muted",
        ),
        Flow(
            "speech recognition",
            frozenset({Data.TRANSCRIPT, Data.SAFETY_CONTEXT, Data.DATE_CONTEXT}),
            NOVA,
            "replies, the safety checks, this date's context",
            retention="memory only: 15 s per dater for the checks; this date's last 8 turns "
            "until the date ends; never written down or logged",
            training="none",
        ),
        Flow(
            "Nova's lines",
            frozenset({Data.NOVA_TEXT, Data.PROFILE, Data.METADATA}),
            f"LiveKit Inference, then {voice}",
            "Nova's voice; a line can hold first names, card details, an audience question "
            "or what a dater said",
            (LIVEKIT, voice),
        ),
        Flow(
            "Nova's voice",
            frozenset({Data.RAW_AUDIO}),
            "LiveKit Cloud",
            "Nova on air",
            (LIVEKIT,),
        ),
        Flow(
            "the cards, audience questions, a dater's words to Nova, tonight's lines",
            frozenset({Data.PROFILE, Data.AUDIENCE, Data.TRANSCRIPT, Data.NOVA_TEXT}),
            to_lines.name,
            "write Nova's lines: set pieces before the date, live lines during it",
            _companies(to_lines),
        ),
        Flow(
            "conversation-derived safety context: each utterance a dater finishes, with "
            "both daters' words from the 15 s before it",
            frozenset({Data.SAFETY_CONTEXT}),
            to_checks.name,
            "the distress and conduct check",
            _companies(to_checks),
            when="every utterance, the whole date, Nova muted or not, wherever the classifier "
            "is; NOVA_DIALOGUE_TO_PROVIDER doesn't apply",
        ),
        Flow(
            "the moderator's mute and Nova's state",
            frozenset({Data.METADATA}),
            "LiveKit Cloud",
            "the moderator's control",
            (LIVEKIT,),
        ),
    ]
    if settings.meaning_check:
        found.append(
            Flow(
                "Nova's line, the cards with ages, what it answers",
                frozenset({Data.NOVA_TEXT, Data.PROFILE, Data.TRANSCRIPT, Data.AUDIENCE}),
                to_checks.name,
                "the meaning check",
                _companies(to_checks),
                when="set pieces before the date"
                + ("; live lines too" if settings.meaning_live else ""),
            )
        )
    if policy.allows(lines, Data.DATE_CONTEXT):
        found.append(_this_date(to_lines, "replies and silences that follow the date"))
    live_meaning = settings.meaning_check and settings.meaning_live
    if live_meaning and policy.allows(checks, Data.DATE_CONTEXT):
        found.append(_this_date(to_checks, "the meaning check on live lines"))
    return found


def received(settings: Settings) -> dict[str, frozenset[Data]]:
    """What each recipient other than Nova itself receives."""
    by: dict[str, set[Data]] = {}
    for flow in flows(settings):
        if flow.recipient != NOVA:
            by.setdefault(flow.recipient, set()).update(flow.data)
    return {recipient: frozenset(data) for recipient, data in by.items()}


def third_parties(settings: Settings) -> set[str]:
    return {company for flow in flows(settings) for company in flow.companies}


def not_in_notice(settings: Settings, notice: str) -> list[str]:
    """The third parties these settings send to that the notice doesn't name."""
    return sorted(company for company in third_parties(settings) if company not in notice)


def render(settings: Settings) -> str:
    """The flows as a table: names and settings only, never a secret."""
    rows = [
        f"- {flow.source} -> {flow.recipient}: {', '.join(sorted(flow.data))}. "
        f"For {flow.purpose}. When: {flow.when}. Retention: {flow.retention}. "
        f"Training: {flow.training}."
        for flow in flows(settings)
    ]
    parties = ", ".join(sorted(third_parties(settings))) or "none"
    return "\n".join([f"Third parties under these settings: {parties}", *rows])


def _this_date(to: Recipient, purpose: str) -> Flow:
    return Flow(
        "dialogue memory: this date's recent turns (decision 0008)",
        frozenset({Data.DATE_CONTEXT}),
        to.name,
        purpose,
        _companies(to),
        when="approved-local, or NOVA_DIALOGUE_TO_PROVIDER",
    )


def _companies(to: Recipient) -> tuple[str, ...]:
    """The third party behind a model: none on an approved-local endpoint; otherwise the
    provider, or for a model elsewhere, where it is."""
    return () if to.local else (to.name,)


def _company(speech: str) -> str:
    provider = speech.partition("/")[0]
    return COMPANIES.get(provider, provider)


def main() -> None:
    print(render(load_settings()))


if __name__ == "__main__":
    main()
