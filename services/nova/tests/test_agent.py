"""Nova's LiveKit agent: the glue around a rehearsal, tested with fakes. No LiveKit
connection: the real thing runs in the Agents Playground (docs/spike-results.md)."""

import asyncio
import json
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest
from livekit.agents import CloseEvent, CloseReason
from livekit.agents.voice import io as voice_io

from nova import agent
from nova.agent import RedactPii, SessionSpeaker, new_session, time_played_words, voice_parts
from nova.config import ConfigError, LlmChoice, Settings
from nova.moderator import ModeratorControl
from nova.privacy import PrivacyPolicy

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


async def test_daters_are_transcribed_while_nova_speaks(monkeypatch):
    """Nova's lines can't be interrupted, and by default LiveKit sends speech recognition
    silence for as long as such a line plays: "I want to stop" said over Nova would reach
    neither layer of the distress check. Only the echo-cancellation warm-up stays deaf."""
    monkeypatch.setenv("LIVEKIT_DEV_MODE", "1")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    session = new_session(SETTINGS)
    assert session.options.interruption["discard_audio_if_uninterruptible"] is False
    assert session.options.aec_warmup_duration == agent.AEC_WARMUP == 3.0


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


def keys(monkeypatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")


async def test_nova_s_voice_times_its_words_so_a_cut_line_goes_on_from_its_sentence(monkeypatch):
    """Decision 0004: Cartesia's word timestamps, through LiveKit's transcript synchronizer,
    say which words had played when a dater cut Nova off; without them nothing is guessed."""
    keys(monkeypatch)
    session = new_session(SETTINGS)  # the default voice: Cartesia
    assert session.tts.capabilities.aligned_transcript
    assert session.options.use_tts_aligned_transcript is True
    other = new_session(replace(SETTINGS, voice="deepgram/aura-2:odysseus"))
    assert not other.tts.capabilities.aligned_transcript
    assert agent.word_timings("deepgram/aura-2") == {}


class Sink(voice_io.AudioOutput):
    """The room's audio output, as RoomIO sets it once the session starts."""

    def __init__(self) -> None:
        super().__init__(label="sink", capabilities=voice_io.AudioOutputCapabilities(pause=True))

    async def capture_frame(self, frame) -> None:
        await super().capture_frame(frame)

    def flush(self) -> None:
        super().flush()

    def clear_buffer(self) -> None:
        pass


def chain(output) -> list:
    """An output and every one it passes on to (LiveKit puts a proxy between some)."""
    found = []
    while output is not None:
        found.append(output)
        output = output.next_in_chain
    return found


async def test_the_words_played_are_timed_with_nothing_published_to_the_room(monkeypatch):
    keys(monkeypatch)
    session = new_session(SETTINGS)
    sink = Sink()
    session.output.audio = sink
    assert time_played_words(session) is True
    audio = chain(session.output.audio)
    assert audio[0].label == "TranscriptSynchronizer" and audio[-1] is sink
    text = chain(session.output.transcription)
    assert text[-1].label == "TranscriptSynchronizer", "its text goes no further: nowhere"


async def test_without_word_timings_no_synchronizer_guesses_the_words(monkeypatch):
    keys(monkeypatch)
    session = new_session(replace(SETTINGS, voice="deepgram/aura-2:odysseus"))
    sink = Sink()
    session.output.audio = sink
    assert time_played_words(session) is False and session.output.audio is sink


@pytest.mark.parametrize(("timed", "words"), [(True, "Here is"), (False, None)])
def test_the_speaker_keeps_the_words_played_only_when_the_voice_timed_them(timed, words):
    speaker = SessionSpeaker(FakeSession(Clock()), timed=timed)
    speaker._on_played(SimpleNamespace(playback_position=1.25, synchronized_transcript="Here is"))
    assert (speaker.played, speaker.played_text) == (1.25, words)


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


class CutSession(FakeSession):
    """Each line is done at once without ever turning to speaking: cut before its first
    audio, or its voice failed."""

    def say(self, text: str, **options) -> FakeHandle:
        self.said.append((text, options))
        handle = FakeHandle()
        handle.played.set()
        return handle


async def test_a_line_cut_before_its_first_audio_is_done_at_once():
    """Waiting START_TIMEOUT (10 s) for audio that never comes would hold up the next line,
    which after a stop is the kind line."""
    clock = Clock()
    speaker = SessionSpeaker(CutSession(clock), clock=clock)
    assert await asyncio.wait_for(speaker.speak("Good evening, room."), 1.0) is None


# --- The entrypoint: how the session is set up and started --------------------------------


class StartedSession(FakeSession):
    """Records how the agent started it."""

    async def start(self, **options) -> None:
        self.started = options


class FakeRehearsal:
    """Stands in for the date itself: these tests are about the session around it."""

    last: FakeRehearsal | None = None

    def __init__(self, *args, **options) -> None:
        self.options = options
        self.listening = asyncio.Event()
        self.listening_at_the_start = False
        self.stopped_listening = False
        self.order: list[str] = []
        self.closed = False
        FakeRehearsal.last = self

    def close(self) -> None:
        """Its Night closes it, however the date ended (nova/show.py)."""
        self.order.append("close")
        self.closed = True

    async def listen(self) -> None:
        self.listening.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped_listening = True

    async def warm_up(self) -> None:
        self.order.append("warm_up")

    async def prepare(self) -> None:
        self.order.append("prepare")
        await self.listening.wait()  # a dater may speak while set pieces are prepared

    opens = True  # what the moderator chose, if Nova wasn't ready
    muted = False

    def mute(self) -> None:
        self.muted = True

    def unmute(self) -> None:
        self.muted = False

    def command(self, text: str, seat=None):
        from nova.rehearsal import Command

        if text not in ("mute", "unmute"):
            return None
        self.mute() if text == "mute" else self.unmute()
        return Command(text)

    async def hold_until_ready(self) -> bool:
        self.order.append("hold")
        return FakeRehearsal.opens

    async def run(self) -> None:
        self.order.append("run")
        self.listening_at_the_start = self.listening.is_set() and not self.stopped_listening
        assert not self.closed, "closed only once the date is over"


class FakeRoom:
    """The parts of rtc.Room the moderator's control path uses."""

    def __init__(self, *participants) -> None:
        self.name = "rehearsal"
        self.handlers: dict[str, list] = {}
        self.remote_participants = {p.identity: p for p in participants}
        self.sent: list[tuple[dict, list[str], str]] = []
        self.local_participant = SimpleNamespace(publish_data=self._publish)

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args) -> None:
        for handler in self.handlers.get(event, []):
            handler(*args)

    async def _publish(self, payload, *, reliable, destination_identities, topic) -> None:
        assert reliable, "a mute must not be lost"
        self.sent.append((json.loads(payload), list(destination_identities), topic))


class FakeContext:
    room = FakeRoom()

    async def wait_for_participant(self) -> None:
        pass

    def shutdown(self, reason: str = "") -> None:
        pass


async def test_two_person_inputs_are_pinned_to_their_tracks_and_closed(monkeypatch):
    from nova.cards import Seat

    left, right = StartedSession(Clock()), StartedSession(Clock())
    heard = []
    waited = []

    class Context(FakeContext):
        async def wait_for_participant(self, *, identity):
            waited.append(identity)

    class Date(FakeRehearsal):
        def speech_started(self, seat):
            heard.append(("start", seat))

        def speech_ended(self, seat):
            heard.append(("end", seat))

        def transcript(self, text, seat):
            heard.append((text, seat))

        def command(self, text, seat=None):
            heard.append((f"typed {text}", seat))
            return super().command(text, seat)

        async def run(self):
            left.handlers["user_state_changed"](
                SimpleNamespace(new_state="speaking", old_state="listening")
            )
            right.handlers["user_state_changed"](
                SimpleNamespace(new_state="speaking", old_state="listening")
            )
            right.handlers["user_input_transcribed"](
                SimpleNamespace(is_final=True, transcript="Right input")
            )
            left.handlers["user_input_transcribed"](
                SimpleNamespace(is_final=True, transcript="Left input")
            )
            # Each person's typing is their own seat's, whoever spoke last (Theo, here).
            for session in (left, right):
                chat = session.started["room_options"].text_input.text_input_cb
                chat(session, SimpleNamespace(text="reveal"))

    closed = []

    async def close_right():
        closed.append(True)

    right.aclose = close_right
    date = Date()
    await agent.run_date(
        Context(),
        left,
        SessionSpeaker(left),
        date,
        seats={Seat.LEFT: "dater-left", Seat.RIGHT: "dater-right"},
        other_session=right,
    )
    assert set(waited) == {"dater-left", "dater-right"}
    assert left.started["room_options"].participant_identity == "dater-left"
    options = right.started["room_options"]
    assert options.participant_identity == "dater-right"
    assert options.get_audio_output_options() is None
    assert options.get_text_output_options() is None
    assert options.get_text_input_options() is not None, "the right seat types its own commands"
    assert heard == [
        ("start", Seat.LEFT),
        ("start", Seat.RIGHT),
        ("Right input", Seat.RIGHT),
        ("Left input", Seat.LEFT),
        ("typed reveal", Seat.LEFT),
        ("typed reveal", Seat.RIGHT),
    ]
    assert closed == [True]


async def rehearse(monkeypatch, tmp_path, session: StartedSession | None = None) -> StartedSession:
    """Runs the agent's entrypoint with LiveKit's session, the model and the date faked."""
    session = session or StartedSession(Clock())
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


async def test_recording_is_off_so_nothing_reaches_livekit_cloud_observability(
    monkeypatch, tmp_path
):
    """LiveKit Cloud can keep a session's audio, transcripts and traces. CLAUDE.md: never
    send transcripts anywhere, so every session starts with recording off."""
    assert (await rehearse(monkeypatch, tmp_path)).started["record"] is False


async def test_the_agent_redacts_what_people_said_before_any_log_handler_writes_it(
    monkeypatch, tmp_path
):
    kept: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = kept.append  # type: ignore[method-assign]
    logging.getLogger().addHandler(handler)
    try:
        await rehearse(monkeypatch, tmp_path)
        logging.getLogger("livekit.agents.test").warning(
            "skipping", extra={"lk.pii.user_input": "Nova, I work at Northwind Bank"}
        )
    finally:
        logging.getLogger().removeHandler(handler)
    assert kept[-1].__dict__["lk.pii.user_input"] == "[redacted]"


async def test_no_one_in_the_room_can_query_the_session(monkeypatch, tmp_path):
    """LiveKit lets any participant in the room ask a hosted session for its details and
    history, with no identity check. The session isn't hosted."""
    assert (await rehearse(monkeypatch, tmp_path)).started["session_host"] is False


async def test_daters_are_heard_from_the_start_of_the_session_until_the_date_is_over(
    monkeypatch, tmp_path
):
    """Utterances close as time passes (nova/on_air.py), so something must tick them while
    set pieces are prepared, before the date's own loop starts."""
    await rehearse(monkeypatch, tmp_path)
    rehearsal = FakeRehearsal.last
    assert rehearsal is not None and rehearsal.listening_at_the_start
    await asyncio.sleep(0)
    assert rehearsal.stopped_listening
    assert rehearsal.options["utterance_grace"] == SETTINGS.utterance_grace
    assert rehearsal.options["utterance_max_wait"] == SETTINGS.utterance_max_wait


async def test_the_model_is_warmed_up_before_set_pieces_are_prepared(monkeypatch, tmp_path):
    """A cold model takes 20-28 s to load: the warm-up takes that, not the prep budget."""
    await rehearse(monkeypatch, tmp_path)
    rehearsal = FakeRehearsal.last
    assert rehearsal is not None
    assert rehearsal.order == ["warm_up", "prepare", "hold", "run", "close"]
    assert rehearsal.options["prep_budget"] == SETTINGS.prep_budget


def test_redaction_is_added_once_however_often_the_agent_starts():
    handler = logging.Handler()
    logging.getLogger().addHandler(handler)
    try:
        agent.redact_logs()
        agent.redact_logs()
        assert sum(isinstance(f, RedactPii) for f in handler.filters) == 1
    finally:
        logging.getLogger().removeHandler(handler)


async def test_the_sessions_date_is_opened_through_a_night_and_closed_when_it_ends(
    monkeypatch, tmp_path
):
    """A rehearsal is a night of one date (nova/show.py, decision 0011): the date gets the
    night's memory, the retry window from the settings, and is closed after the session's
    date, before the report."""
    await rehearse(monkeypatch, tmp_path)
    date = FakeRehearsal.last
    assert date is not None and date.closed and date.order[-1] == "close"
    assert "tonight" in date.options and date.options["ready_retry"] == SETTINGS.ready_retry


async def test_a_date_that_never_opens_is_closed_too(monkeypatch, tmp_path):
    monkeypatch.setattr(FakeRehearsal, "opens", False)
    await rehearse(monkeypatch, tmp_path)
    date = FakeRehearsal.last
    assert date is not None and "run" not in date.order and date.closed


def closes_the_session(monkeypatch, session: StartedSession, step: str) -> None:
    """The person playing both daters leaves during `step` of the date (prepare or run):
    LiveKit closes the session, and the step would otherwise never end on its own."""

    async def step_cut_short(self) -> None:
        self.order.append(step)
        reason = CloseReason.PARTICIPANT_DISCONNECTED
        session.handlers["close"](CloseEvent(reason=reason))
        try:
            await asyncio.Event().wait()
        finally:
            self.order.append(f"{step} cut short")

    monkeypatch.setattr(FakeRehearsal, step, step_cut_short)


def report(tmp_path) -> str:
    (path,) = tmp_path.glob("*.txt")
    return path.read_text(encoding="utf-8")


async def test_the_date_ends_as_soon_as_the_session_closes_and_the_report_says_why(
    monkeypatch, tmp_path
):
    """Until 2026-10-26, a dater who left had the date run on to an empty room, Nova's
    lines and model requests included, until LiveKit cancelled the job about 35 s later,
    and no report was written."""
    session = StartedSession(Clock())
    closes_the_session(monkeypatch, session, "run")
    await asyncio.wait_for(rehearse(monkeypatch, tmp_path, session), 1.0)
    date = FakeRehearsal.last
    assert date is not None and date.order[-3:] == ["run", "run cut short", "close"]
    await asyncio.sleep(0)
    assert date.stopped_listening
    assert "The session closed (participant_disconnected) before the date's end" in report(tmp_path)


async def test_a_session_that_closes_while_nova_gets_ready_never_opens_the_date(
    monkeypatch, tmp_path
):
    session = StartedSession(Clock())
    closes_the_session(monkeypatch, session, "prepare")
    await asyncio.wait_for(rehearse(monkeypatch, tmp_path, session), 1.0)
    date = FakeRehearsal.last
    assert date is not None and date.order == ["warm_up", "prepare", "prepare cut short", "close"]
    assert "(participant_disconnected)" in report(tmp_path)


async def test_a_date_that_runs_its_course_says_nothing_of_the_session_closing(
    monkeypatch, tmp_path
):
    await rehearse(monkeypatch, tmp_path)
    assert "session closed" not in report(tmp_path)


async def test_an_error_in_the_date_still_ends_the_job_and_the_date_is_closed(
    monkeypatch, tmp_path
):
    async def broken(self) -> None:
        self.order.append("run")
        raise RuntimeError("the date broke")

    monkeypatch.setattr(FakeRehearsal, "run", broken)
    with pytest.raises(RuntimeError, match="the date broke"):
        await rehearse(monkeypatch, tmp_path)
    date = FakeRehearsal.last
    await asyncio.sleep(0)
    assert date is not None and date.closed and date.stopped_listening


async def test_a_session_makes_one_rehearsal_with_the_privacy_policy_its_settings_give(
    monkeypatch, tmp_path
):
    """One Rehearsal a session, so one DateContext a date (decision 0008), and every request
    it sends goes through the policy from the settings (decision 0010): who may receive a
    real date's recent turns is decided per model, at each request (tests/test_privacy.py)."""
    made: list[FakeRehearsal] = []
    real = FakeRehearsal.__init__

    def counted(self, *args, **options) -> None:
        real(self, *args, **options)
        made.append(self)

    monkeypatch.setattr(FakeRehearsal, "__init__", counted)
    await rehearse(monkeypatch, tmp_path)
    assert len(made) == 1 and made[0].options["privacy"] == PrivacyPolicy()
    approved = replace(
        SETTINGS,
        llm=LlmChoice("anthropic", "claude-haiku-5-5"),
        dialogue_to_provider=True,
        approved_local_hosts=frozenset({"gpu.lan"}),
    )
    monkeypatch.setattr(agent, "build_classifier", lambda settings: SimpleNamespace(name="fake"))
    session = StartedSession(Clock())
    monkeypatch.setattr(agent, "load_settings", lambda: approved)
    monkeypatch.setattr(agent, "new_session", lambda settings: session)
    await agent.rehearse(FakeContext())
    assert len(made) == 2 and made[1].options["privacy"] == PrivacyPolicy(
        dialogue_to_provider=True, approved_hosts=frozenset({"gpu.lan"})
    )


async def test_a_session_never_starts_with_logs_that_would_write_prompts(monkeypatch, tmp_path):
    """The agent hears real people: diagnostics meant for synthetic runs are refused before
    the session starts, as is any SDK logger at DEBUG (nova/privacy.py)."""
    diagnostics = replace(SETTINGS, synthetic_diagnostics=True)
    session = StartedSession(Clock())
    monkeypatch.setattr(agent, "load_settings", lambda: diagnostics)
    monkeypatch.setattr(agent, "new_session", lambda settings: session)
    with pytest.raises(ConfigError, match="never the agent"):
        await agent.rehearse(FakeContext())
    assert getattr(session, "started", None) is None, "no session was started"


async def test_a_date_nova_isnt_ready_for_never_opens_unless_the_moderator_says(
    monkeypatch, tmp_path
):
    """Decision 0005: the doors hold (Rehearsal.hold_until_ready) and the report is still
    written; a moderator who ends it gets no date at all."""
    monkeypatch.setattr(FakeRehearsal, "opens", False)
    await rehearse(monkeypatch, tmp_path)
    rehearsal = FakeRehearsal.last
    assert rehearsal is not None and rehearsal.order == ["warm_up", "prepare", "hold", "close"]
    assert list(tmp_path.glob("*.txt")), "the report is written all the same"


# --- The moderator's mute from the console (launch requirement 2) ---------------------------------

MODERATOR = SimpleNamespace(identity="mod-1", attributes={"role": "moderator"})
DATER_PARTICIPANT = SimpleNamespace(identity="dater-1", attributes={"role": "dater"})


def mute_packet(
    who, kind: str = "mute", seq: int = 1, session: str = "s1", topic: str = "unseen.moderator"
):
    payload = json.dumps({"type": kind, "seq": seq, "session": session}).encode()
    return SimpleNamespace(topic=topic, participant=who, data=payload)


async def test_a_moderators_mute_reaches_nova_and_every_moderator_hears_the_state():
    target = FakeRehearsal()
    room = FakeRoom(MODERATOR, DATER_PARTICIPANT)
    agent.connect_moderators(room, ModeratorControl(target, session="s1"))
    await asyncio.sleep(0)
    assert room.sent == [
        (
            {"type": "nova_state", "session": "s1", "muted": False, "seq": 0, "result": "state"},
            ["mod-1"],
            "unseen.moderator",
        ),
    ], "a moderator already in the room is told at once; the dater never is"
    room.emit("data_received", mute_packet(MODERATOR))
    await asyncio.sleep(0)
    assert (
        target.muted and room.sent[-1][0]["result"] == "applied" and room.sent[-1][1] == ["mod-1"]
    )


async def test_a_daters_mute_message_is_ignored_and_answered_with_nothing():
    target = FakeRehearsal()
    room = FakeRoom(MODERATOR, DATER_PARTICIPANT)
    agent.connect_moderators(room, ModeratorControl(target, session="s1"))
    await asyncio.sleep(0)
    before = len(room.sent)
    room.emit("data_received", mute_packet(DATER_PARTICIPANT))
    room.emit("data_received", mute_packet(MODERATOR, topic="lk.chat"))
    room.emit(
        "data_received", SimpleNamespace(topic="unseen.moderator", participant=None, data=b"{}")
    )
    await asyncio.sleep(0)
    assert not target.muted and len(room.sent) == before


async def test_a_moderator_who_comes_back_is_told_the_mute_still_stands():
    target = FakeRehearsal()
    room = FakeRoom(MODERATOR)
    agent.connect_moderators(room, ModeratorControl(target, session="s1"))
    room.emit("data_received", mute_packet(MODERATOR))
    del room.remote_participants["mod-1"]  # the console's connection drops
    room.emit("participant_connected", MODERATOR)  # and comes back
    await asyncio.sleep(0)
    assert target.muted and room.sent[-1][0] == {
        "type": "nova_state",
        "session": "s1",
        "muted": True,
        "seq": 0,
        "result": "state",
    }


async def test_someone_made_a_moderator_mid_session_is_told_the_state():
    room = FakeRoom()
    agent.connect_moderators(room, ModeratorControl(FakeRehearsal(), session="s1"))
    room.emit("participant_attributes_changed", {"role": "moderator"}, MODERATOR)
    await asyncio.sleep(0)
    assert room.sent[-1][1] == ["mod-1"]


async def test_the_agent_tells_the_consoles_when_the_chat_mutes_nova(monkeypatch, tmp_path):
    room = FakeRoom(MODERATOR)
    monkeypatch.setattr(FakeContext, "room", room)
    session = await rehearse(monkeypatch, tmp_path)
    await asyncio.sleep(0)
    assert len(room.sent) == 1, "the moderator in the room heard the state when the session began"
    on_chat = session.started["room_options"].text_input.text_input_cb
    on_chat(session, SimpleNamespace(text="mute"))
    await asyncio.sleep(0)
    assert room.sent[-1][0]["muted"] is True and room.sent[-1][0]["result"] == "state"
