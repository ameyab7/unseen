"""Nova in a LiveKit room: one measured rehearsal date for the Agents Playground (task 5).

    uv run python -m nova.agent dev

Then open agents-playground.livekit.io, choose the project and connect. Nova warms the model
up, prepares its set pieces within the prep budget (NOVA_PREP_BUDGET_SECONDS), waits for
you, and runs one full-length date as the host of Maya and Theo (set NOVA_FULL_REHEARSAL
false for the compressed version). Configure both participant identities for two people
(each joins with a token from `uv run python -m nova.two_person`, and what each types is
their own seat's), or play both daters with one microphone. Start with "Nova, ..." to talk
to Nova, and
type commands in the chat box (nova/rehearsal.py, Command): maya, theo, moment, back,
unlock, ask <question>, reveal, unseen, keep, resume, end, leave, report, and for the
moderator, mute, unmute, and start (a date Nova isn't ready for). What daters say
and type is heard from the moment the session starts, before the date does, and never
raises: Nova acts on each utterance once the dater has finished it (nova/on_air.py). At
the end the report (codes and numbers only) is logged and saved in
services/nova/rehearsals/.

Privacy (CLAUDE.md, docs/privacy/nova-data-flows.md): beyond the room's own media server,
daters' audio goes only to speech recognition (LiveKit Inference, then the NOVA_STT
provider), every frame from the session's start to its end, Nova muted or not. Recording
is off, so the session uploads no recording, transcript or traces to LiveKit Cloud; what
LiveKit and the speech providers keep of what passes through them isn't known. No one in
the room can query the session for its history; LiveKit's own log fields that carry what
people said (lk.pii.*) are redacted; what daters say is never published to the room as
text; and what they say or type is never written down: it stays in memory, and reaches a
model only as nova/privacy.py allows. The agent hears real people, so it refuses to start
with any SDK logger that writes prompts (nova/privacy.py, check_for_real_people).
"""

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    CloseEvent,
    JobContext,
    TurnHandlingOptions,
    cli,
    inference,
    room_io,
)
from livekit.agents.voice.transcription import TranscriptSynchronizer

from nova.cards import Seat
from nova.config import Settings, load_settings
from nova.events import EventLog
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.models import build_classifier, build_model
from nova.moderator import TOPIC, ModeratorControl, is_moderator
from nova.privacy import PrivacyPolicy, check_for_real_people
from nova.rehearsal import Command, Rehearsal, render_report
from nova.show import Night
from nova.timing import FULL, REHEARSAL

logger = logging.getLogger("nova.agent")

# Recording off: LiveKit Cloud keeps no audio, transcripts or traces. Not a session host:
# otherwise any participant in the room can ask the session for its details and history.
START_OPTIONS: dict[str, Any] = {"record": False, "session_host": False}
# Daters' audio goes to speech recognition and nothing else. In dev mode LiveKit's defaults
# would also stream it to its cloud turn detector and its adaptive barge-in service. Nova
# never takes turns from speech and can't be talked over, so the local voice detector
# decides turns, and with interruptions off LiveKit never creates the barge-in detector.
# Daters are still heard while Nova speaks: by default LiveKit sends silence to speech
# recognition during a line that can't be interrupted, so "I want to stop" said over Nova
# would never be heard. The session has no language model, so these transcripts start no
# automatic reply; they reach the on-air listener like any other.
TURN_HANDLING: TurnHandlingOptions = {
    "turn_detection": "vad",
    "interruption": {"enabled": False, "discard_audio_if_uninterruptible": False},
}
# For the first seconds of Nova's speech in a session (the doors intro), LiveKit still sends
# speech recognition silence while the browser's echo cancellation settles, so Nova's own
# voice isn't transcribed as a dater's. Daters aren't heard in that window (decision 0005).
AEC_WARMUP = 3.0
REPORTS = Path(__file__).parents[1] / "rehearsals"  # never committed
REDACTED = "[redacted]"


class RedactPii(logging.Filter):
    """LiveKit marks log fields holding what people said or typed with an lk.pii. prefix.
    They're blanked before any record is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(vars(record)):
            if key.startswith("lk.pii"):
                setattr(record, key, REDACTED)
        return True


def redact_logs() -> None:
    """Adds the redaction to every root handler, once: calling it again adds nothing."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(existing, RedactPii) for existing in handler.filters):
            handler.addFilter(RedactPii())


def voice_parts(voice: str) -> tuple[str, str | None]:
    """ "provider/model:voice" -> ("provider/model", "voice")."""
    model, _, name = voice.partition(":")
    return model, name or None


def word_timings(model: str) -> dict[str, Any]:
    """Options asking a voice to time its words, for the ones that can through LiveKit
    Inference: Cartesia's timestamps. With them, a line a dater cut off goes on from the
    sentence it was cut in (nova/rehearsal.py, after_a_cut); any other voice gets none, and
    a cut is never placed in the words."""
    return {"add_timestamps": True} if model.split("/")[0] == "cartesia" else {}


def time_played_words(session: Any) -> bool:
    """Once the session has started: Nova's audio goes through LiveKit's transcript
    synchronizer, so each line's playback_finished event carries the words that had played,
    paced by the voice's word timestamps against the audio the room was sent. Its text goes
    nowhere (no next output): nothing is published to the room, and the words stay in
    memory. True if the voice times its words; without timings the synchronizer only
    estimates from a speaking rate, so it isn't installed and SessionSpeaker never uses
    one."""
    tts, output = getattr(session, "tts", None), getattr(session, "output", None)
    timed = (
        tts is not None
        and tts.capabilities.aligned_transcript
        and session.options.use_tts_aligned_transcript is True
    )
    if not timed or output is None or output.audio is None:
        return False
    synchronizer = TranscriptSynchronizer(next_in_chain_audio=output.audio, next_in_chain_text=None)
    output.audio = synchronizer.audio_output
    output.transcription = synchronizer.text_output
    return True


class SessionSpeaker:
    """Nova's voice through the agent session. Lines play one at a time and can't be talked
    over; a line's first audio is when the agent's state turns to speaking."""

    START_TIMEOUT = 10.0

    def __init__(
        self, session: Any, *, clock: Callable[[], float] = time.monotonic, timed: bool = False
    ) -> None:
        self._session = session
        self._clock = clock
        self._speaking = asyncio.Event()
        self._started_at: float | None = None
        # Seconds of the last line that played, as the room's audio output counts them
        # (PlaybackFinishedEvent.playback_position: the audio handed to WebRTC, not a guess).
        # None until the output has said.
        self.played: float | None = None
        # The words of the last line that had played when it finished or was cut, only when
        # the voice times its words (timed: time_played_words); otherwise None, and the
        # rehearsal never places a cut in the words. In memory only, never logged.
        self.played_text: str | None = None
        self.timed = timed
        self._watching: Any = None
        session.on("agent_state_changed", self._on_state)

    def _on_state(self, event: Any) -> None:
        if event.new_state == "speaking" and not self._speaking.is_set():
            self._started_at = self._clock()
            self._speaking.set()

    def _on_played(self, event: Any) -> None:
        self.played = float(event.playback_position)
        if self.timed:
            self.played_text = getattr(event, "synchronized_transcript", None)

    def _watch_playback(self) -> None:
        """The room's audio output exists once the session has started; without one, the
        rehearsal tells the sentence from how long the line had played instead."""
        audio = getattr(getattr(self._session, "output", None), "audio", None)
        if audio is not None and audio is not self._watching:
            audio.on("playback_finished", self._on_played)
            self._watching = audio

    async def speak(self, text: str) -> float | None:
        """Waits for the line's first audio or the end of its playout, whichever comes first,
        then for the playout. A line cut before its first audio, or whose voice failed, is
        done at once: waiting START_TIMEOUT for audio that will never come would hold up
        the line after it, which may be the kind line. Never started: None."""
        self._speaking.clear()
        self._started_at = None
        self.played = self.played_text = None
        self._watch_playback()
        handle = self._session.say(text, allow_interruptions=False, add_to_chat_ctx=False)
        # wait_for_playout() shields the speech, so cancelling this never cancels the line.
        playout = asyncio.ensure_future(handle.wait_for_playout())
        speaking = asyncio.ensure_future(self._speaking.wait())
        try:
            await asyncio.wait(
                {playout, speaking}, timeout=self.START_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
            )
            started = self._started_at
            await playout
            return started
        finally:
            speaking.cancel()
            playout.cancel()

    def stop(self) -> None:
        self._session.interrupt(force=True)


def connect_moderators(room: Any, control: ModeratorControl) -> Callable[[], None]:
    """The console's control path (nova/moderator.py): each data message on the moderator
    topic goes to the control, and its answer to every moderator in the room; each moderator
    who joins, comes back or is made one gets the state at once. A command from anyone else
    gets nothing back. Returns broadcast(), which sends the state to every moderator, for a
    mute changed another way (the rehearsal's chat)."""

    def moderators() -> list[str]:
        return [p.identity for p in room.remote_participants.values() if is_moderator(p.attributes)]

    def send(payload: bytes, to: list[str]) -> None:
        if not to:
            return
        sending = asyncio.ensure_future(
            room.local_participant.publish_data(
                payload, reliable=True, destination_identities=to, topic=TOPIC
            )
        )
        sending.add_done_callback(_sent)

    def on_data(packet: Any) -> None:
        who = getattr(packet, "participant", None)
        if packet.topic != TOPIC or who is None:
            return
        reply = control.handle(who.identity, who.attributes, packet.data)
        if reply is not None:
            send(reply, moderators())

    def on_joined(participant: Any) -> None:
        reply = control.joined(participant.attributes)
        if reply is not None:
            send(reply, [participant.identity])

    room.on("data_received", on_data)
    room.on("participant_connected", on_joined)
    room.on("participant_attributes_changed", lambda _changed, participant: on_joined(participant))
    for participant in list(room.remote_participants.values()):  # moderators already here
        on_joined(participant)
    return lambda: send(control.state(), moderators())


def _sent(sending: asyncio.Future[None]) -> None:
    if not sending.cancelled() and sending.exception() is not None:
        logger.warning("moderator state not sent: %s", type(sending.exception()).__name__)


def new_session(settings: Settings) -> AgentSession:
    model, voice = voice_parts(settings.voice)
    voice_options: dict[str, Any] = {"voice": voice} if voice else {}
    if timings := word_timings(model):
        voice_options["extra_kwargs"] = timings
    return AgentSession(
        stt=inference.STT(settings.stt, language="en"),
        tts=inference.TTS(model, **voice_options),
        turn_handling=TURN_HANDLING,
        aec_warmup_duration=AEC_WARMUP,
        # The words the voice timed reach SessionSpeaker through time_played_words.
        use_tts_aligned_transcript=bool(timings),
    )


async def run_date(
    ctx: JobContext,
    session: AgentSession,
    speaker: SessionSpeaker,
    rehearsal: Rehearsal,
    *,
    seats: dict[Seat, str] | None = None,
    other_session: AgentSession | None = None,
) -> str | None:
    """One date in the session's room: the moderator's control, the daters heard from
    the session's start, Nova warmed up and ready (or held for the moderator, decision
    0011), then the date itself. If the session closes first (the one person playing both
    daters left, or the job shut down), the date ends there: nobody is left to hear it.
    Returns why the session closed then, a LiveKit code; None if the date ran its course."""

    def on_user_state(event: Any) -> None:
        seat = Seat.LEFT if seats else None
        if event.new_state == "speaking":
            if seat is None:
                rehearsal.speech_started()
            else:
                rehearsal.speech_started(seat)
        elif event.old_state == "speaking":
            if seat is None:
                rehearsal.speech_ended()
            else:
                rehearsal.speech_ended(seat)

    def on_transcript(event: Any) -> None:
        if event.is_final:
            if seats:
                rehearsal.transcript(event.transcript, Seat.LEFT)
            else:
                rehearsal.transcript(event.transcript)

    # The moderator's mute from the console (launch requirement 2, nova/moderator.py).
    control = ModeratorControl(rehearsal, events=EventLog())
    tell_moderators = connect_moderators(ctx.room, control)

    def typed(text: str, seat: Seat | None) -> None:
        if rehearsal.command(text, seat) in (Command.MUTE, Command.UNMUTE):
            tell_moderators()  # the console shows what is true, however it changed

    # LiveKit hands each session only its own participant's chat: with two people, what each
    # types is their own (their moment, report or decision), never the seat that spoke last.
    def on_chat(_session: AgentSession, event: room_io.TextInputEvent) -> None:
        typed(event.text, Seat.LEFT if seats else None)

    def on_other_chat(_session: AgentSession, event: room_io.TextInputEvent) -> None:
        typed(event.text, Seat.RIGHT)

    # Why the session closed, once it has.
    closing: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    def on_close(event: CloseEvent) -> None:
        if not closing.done():
            closing.set_result(event.reason.value)

    session.on("user_state_changed", on_user_state)
    session.on("user_input_transcribed", on_transcript)
    session.on("close", on_close)
    if other_session is not None:

        def on_other_state(event: Any) -> None:
            if event.new_state == "speaking":
                rehearsal.speech_started(Seat.RIGHT)
            elif event.old_state == "speaking":
                rehearsal.speech_ended(Seat.RIGHT)

        def on_other_transcript(event: Any) -> None:
            if event.is_final:
                rehearsal.transcript(event.transcript, Seat.RIGHT)

        other_session.on("user_state_changed", on_other_state)
        other_session.on("user_input_transcribed", on_other_transcript)
        other_session.on("close", on_close)

    async def get_ready() -> None:
        await rehearsal.warm_up()  # a cold model loads here, not inside the prep budget
        await rehearsal.prepare()

    async def the_date() -> None:
        if seats:
            await asyncio.gather(
                *(ctx.wait_for_participant(identity=who) for who in seats.values())
            )
        else:
            await ctx.wait_for_participant()
        await preparing
        # Not ready (a model that didn't warm up, every set piece a fallback): the
        # moderator is alerted and chooses, never a silent date of fallbacks (decision 0005).
        if await rehearsal.hold_until_ready():
            await rehearsal.run()

    # Daters are heard on time from their first word, while set pieces are still written.
    listening = asyncio.create_task(rehearsal.listen())
    preparing = asyncio.create_task(get_ready())
    try:
        await session.start(
            agent=Agent(instructions="Nova's lines come from the show runner; no replies here."),
            room=ctx.room,
            room_options=room_io.RoomOptions(
                **({"participant_identity": seats[Seat.LEFT]} if seats else {}),
                text_input=room_io.TextInputOptions(text_input_cb=on_chat),
                # Otherwise LiveKit publishes what daters say, as text, to everyone in the room.
                text_output=False,
            ),
            **START_OPTIONS,
        )
        speaker.timed = time_played_words(session)
        if other_session is not None:
            assert seats is not None
            await other_session.start(
                agent=Agent(instructions="Input only; Nova's show runner controls speech."),
                room=ctx.room,
                room_options=room_io.RoomOptions(
                    participant_identity=seats[Seat.RIGHT],
                    text_input=room_io.TextInputOptions(text_input_cb=on_other_chat),
                    text_output=False,
                    audio_output=False,
                ),
                **START_OPTIONS,
            )
        dating = asyncio.create_task(the_date())
        try:
            await asyncio.wait({dating, closing}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            dating.cancel()  # a date cut short still finishes its checks (Rehearsal.run)
            await asyncio.gather(dating, return_exceptions=True)
        if dating.cancelled():  # only ever by the session closing first
            return closing.result()
        dating.result()  # the date's own error, if it had one
        return None
    finally:
        listening.cancel()
        preparing.cancel()  # a session that ended while Nova still got ready
        await asyncio.gather(listening, preparing, return_exceptions=True)
        if other_session is not None:
            await other_session.aclose()


server = AgentServer()


@server.rtc_session()
async def rehearse(ctx: JobContext) -> None:
    redact_logs()
    settings = load_settings()
    check_for_real_people(settings)  # the log levels as LiveKit set them for this job
    session = new_session(settings)
    speaker = SessionSpeaker(session)
    # A rehearsal is a night of one date (nova/show.py): the date's context is its own,
    # and closed when the date ends, however it ends.
    night = Night(make=Rehearsal)
    with night.date(
        REHEARSAL_PAIR,
        model=build_model(settings),
        classifier=build_classifier(settings),
        speaker=speaker,
        voice=settings.voice,
        stt=settings.stt,
        timing=replace(
            FULL if settings.full_rehearsal else REHEARSAL,
            conversation_overrun=settings.conversation_overrun,
        ),
        conversational=settings.conversational_turns,
        live_bridges=settings.live_bridges,
        input_streams=2 if settings.left_participant and settings.right_participant else 1,
        next_pair=NEXT_PAIR,
        live_timeout=settings.live_timeout,
        prep_timeout=settings.prep_timeout,
        prep_budget=settings.prep_budget,
        prep_parallel=settings.prep_parallel,
        warm_up=settings.warm_up,
        distress_timeout=settings.distress_timeout,
        utterance_grace=settings.utterance_grace,
        utterance_max_wait=settings.utterance_max_wait,
        privacy=PrivacyPolicy.from_settings(settings),  # real daters: the privacy notice
        meaning_check=settings.meaning_check,
        meaning_live=settings.meaning_live,
        meaning_timeout=settings.meaning_timeout,
        ready_retry=settings.ready_retry,
    ) as rehearsal:
        if settings.left_participant and settings.right_participant:
            other = AgentSession(
                stt=inference.STT(settings.stt, language="en"),
                turn_handling=TURN_HANDLING,
                aec_warmup_duration=AEC_WARMUP,
            )
            closed = await run_date(
                ctx,
                session,
                speaker,
                rehearsal,
                seats={
                    Seat.LEFT: settings.left_participant,
                    Seat.RIGHT: settings.right_participant,
                },
                other_session=other,
            )
        else:
            closed = await run_date(ctx, session, speaker, rehearsal)

    report = render_report(rehearsal)
    if closed is not None:
        report += f"\nThe session closed ({closed}) before the date's end: the date ended there."
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{datetime.now():%Y-%m-%d-%H%M%S}.txt"
    path.write_text(report + "\n", encoding="utf-8")
    logger.info("rehearsal report saved to %s\n%s", path, report)
    ctx.shutdown("rehearsal finished")


def main() -> None:
    settings = load_settings()
    check_for_real_people(settings)
    # The LiveKit CLI and its speech models read these from the environment.
    os.environ.setdefault("LIVEKIT_URL", settings.livekit_url)
    os.environ.setdefault("LIVEKIT_API_KEY", settings.livekit_api_key)
    os.environ.setdefault("LIVEKIT_API_SECRET", settings.livekit_api_secret)
    cli.run_app(server)


if __name__ == "__main__":
    main()
