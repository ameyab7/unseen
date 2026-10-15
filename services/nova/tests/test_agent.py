"""Nova's LiveKit agent: the glue around a rehearsal, tested with fakes. No LiveKit
connection: the real thing runs in the Agents Playground (docs/spike-results.md)."""

import asyncio
import logging
from types import SimpleNamespace

from nova import agent
from nova.agent import START_OPTIONS, RedactPii, SessionSpeaker, new_session, voice_parts
from nova.config import LlmChoice, Settings

SETTINGS = Settings(
    livekit_url="wss://example.invalid",
    livekit_api_key="test-key",
    livekit_api_secret="test-secret",
    llm=LlmChoice("ollama", "gemma"),
)


async def test_daters_audio_goes_to_speech_recognition_and_no_other_livekit_service(monkeypatch):
    """Every rehearsal runs in dev mode, where LiveKit's defaults would also stream daters'
    audio to its cloud turn detector and to its adaptive barge-in service. Nova never takes
    turns from speech and can't be talked over, so the local voice detector decides turns
    and interruptions are off, which means LiveKit never creates the barge-in detector."""
    monkeypatch.setenv("LIVEKIT_DEV_MODE", "1")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    session = new_session(SETTINGS)
    assert session.turn_detection == "vad", "not the cloud turn detector"
    assert session.options.interruption["enabled"] is False


def test_recording_is_off_so_nothing_reaches_livekit_cloud_observability():
    """LiveKit Cloud can keep a session's audio, transcripts and traces. CLAUDE.md: never
    send transcripts anywhere, so every session starts with recording off."""
    assert START_OPTIONS["record"] is False


def test_what_people_said_or_typed_is_redacted_from_every_log_record():
    record = logging.LogRecord("livekit.agents", logging.WARNING, "x", 1, "skipping", (), None)
    record.__dict__["lk.pii.user_input"] = "Nova, I work at Northwind Bank"
    record.__dict__["speech_id"] = "abc"
    assert RedactPii().filter(record) is True
    assert record.__dict__["lk.pii.user_input"] == "[redacted]"
    assert record.__dict__["speech_id"] == "abc", "only marked fields"


def test_the_voice_setting_splits_into_model_and_voice():
    assert voice_parts("cartesia/sonic-3:abc-123") == ("cartesia/sonic-3", "abc-123")
    assert voice_parts("deepgram/aura-2") == ("deepgram/aura-2", None)


class FakeHandle:
    def __init__(self) -> None:
        self.played = asyncio.Event()

    async def wait_for_playout(self) -> None:
        await self.played.wait()


class FakeSession:
    """Turns to speaking a moment after each line is handed over, then finishes playing."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.handlers: dict = {}
        self.said: list[tuple[str, dict]] = []
        self.interrupts: list[bool] = []

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def say(self, text: str, **options) -> FakeHandle:
        self.said.append((text, options))
        handle = FakeHandle()
        loop = asyncio.get_running_loop()

        def speaking():
            self.clock.now += 0.25
            self.handlers["agent_state_changed"](SimpleNamespace(new_state="speaking"))
            handle.played.set()

        loop.call_soon(speaking)
        return handle

    def interrupt(self, *, force: bool = False):
        self.interrupts.append(force)


class Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


async def test_a_line_starts_when_the_agent_turns_to_speaking_and_cant_be_talked_over():
    clock = Clock()
    session = FakeSession(clock)
    speaker = SessionSpeaker(session, clock=clock)
    started = await speaker.speak("Good evening, room.")
    assert started == 100.25
    assert session.said == [
        ("Good evening, room.", {"allow_interruptions": False, "add_to_chat_ctx": False})
    ]


async def test_stopping_cuts_nova_off_mid_line():
    clock = Clock()
    session = FakeSession(clock)
    SessionSpeaker(session, clock=clock).stop()
    assert session.interrupts == [True]


# --- The entrypoint: how the session is set up and started --------------------------------


class StartedSession(FakeSession):
    """Records how the agent started it."""

    async def start(self, **options) -> None:
        self.started = options


class FakeRehearsal:
    """Stands in for the date itself: these tests are about the session around it."""

    def __init__(self, *args, **options) -> None:
        pass

    async def prepare(self) -> None:
        pass

    async def run(self) -> None:
        pass


class FakeContext:
    room = SimpleNamespace(name="rehearsal")

    async def wait_for_participant(self) -> None:
        pass

    def shutdown(self, reason: str = "") -> None:
        pass


async def rehearse(monkeypatch, tmp_path) -> StartedSession:
    """Runs the agent's entrypoint with LiveKit's session, the model and the date faked."""
    session = StartedSession(Clock())
    monkeypatch.setattr(agent, "load_settings", lambda: SETTINGS)
    monkeypatch.setattr(agent, "new_session", lambda settings: session)
    monkeypatch.setattr(agent, "build_model", lambda settings: SimpleNamespace(name="fake"))
    monkeypatch.setattr(agent, "Rehearsal", FakeRehearsal)
    monkeypatch.setattr(agent, "render_report", lambda rehearsal: "codes and numbers")
    monkeypatch.setattr(agent, "REPORTS", tmp_path)
    await agent.rehearse(FakeContext())
    return session


async def test_daters_transcripts_are_never_published_to_the_room(monkeypatch, tmp_path):
    """By default LiveKit publishes what each dater says, as text, to everyone in the room,
    and listeners join this room in task 7. Off until captions are designed on purpose."""
    options = (await rehearse(monkeypatch, tmp_path)).started["room_options"]
    assert options.get_text_output_options() is None
    assert options.get_text_input_options() is not None, "chat commands still come in"


async def test_no_one_in_the_room_can_query_the_session(monkeypatch, tmp_path):
    """LiveKit lets any participant in the room ask a hosted session for its details and
    history, with no identity check. The session isn't hosted."""
    assert (await rehearse(monkeypatch, tmp_path)).started["session_host"] is False
