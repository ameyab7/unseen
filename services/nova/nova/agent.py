"""Nova in a LiveKit room: one measured rehearsal date for the Agents Playground (task 5).

    uv run python -m nova.agent dev

Then open agents-playground.livekit.io, choose the project and connect. Nova warms the model
up, prepares its set pieces within the prep budget (NOVA_PREP_BUDGET_SECONDS), waits for
you, and runs one rehearsal date (about three minutes) as the host
of Maya and Theo. You play both daters: speak, start with "Nova, ..." to talk to Nova, and
type commands in the chat box (nova/rehearsal.py, Command): maya, theo, moment, back,
unlock, ask <question>, reveal, unseen, keep, resume, end, leave, report. What daters say
and type is heard from the moment the session starts, before the date does, and never
raises: Nova acts on each utterance once the dater has finished it (nova/on_air.py). At
the end the report (codes and numbers only) is logged and saved in
services/nova/rehearsals/.

Privacy (CLAUDE.md): daters' audio goes to speech recognition and no other LiveKit service;
recording is off, so LiveKit Cloud keeps no audio, transcripts or traces; no one in the
room can query the session for its history; LiveKit's own log fields that carry what
people said (lk.pii.*) are redacted; what daters say is never published to the room as
text; and what they say or type stays in memory.
"""

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    cli,
    inference,
    room_io,
)

from nova.config import Settings, load_settings
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.models import build_model
from nova.rehearsal import Rehearsal, render_report
from nova.timing import REHEARSAL

logger = logging.getLogger("nova.agent")

# Recording off: LiveKit Cloud keeps no audio, transcripts or traces. Not a session host:
# otherwise any participant in the room can ask the session for its details and history.
START_OPTIONS: dict[str, Any] = {"record": False, "session_host": False}
# Daters' audio goes to speech recognition and nothing else. In dev mode LiveKit's defaults
# would also stream it to its cloud turn detector and its adaptive barge-in service. Nova
# never takes turns from speech and can't be talked over, so the local voice detector
# decides turns, and with interruptions off LiveKit never creates the barge-in detector.
TURN_HANDLING: TurnHandlingOptions = {"turn_detection": "vad", "interruption": {"enabled": False}}
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
    redact = RedactPii()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redact)


def voice_parts(voice: str) -> tuple[str, str | None]:
    """ "provider/model:voice" -> ("provider/model", "voice")."""
    model, _, name = voice.partition(":")
    return model, name or None


class SessionSpeaker:
    """Nova's voice through the agent session. Lines play one at a time and can't be talked
    over; a line's first audio is when the agent's state turns to speaking."""

    START_TIMEOUT = 10.0

    def __init__(self, session: Any, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._session = session
        self._clock = clock
        self._speaking = asyncio.Event()
        self._started_at: float | None = None
        session.on("agent_state_changed", self._on_state)

    def _on_state(self, event: Any) -> None:
        if event.new_state == "speaking" and not self._speaking.is_set():
            self._started_at = self._clock()
            self._speaking.set()

    async def speak(self, text: str) -> float | None:
        self._speaking.clear()
        self._started_at = None
        handle = self._session.say(text, allow_interruptions=False, add_to_chat_ctx=False)
        with contextlib.suppress(TimeoutError):  # never started: measured as a miss
            await asyncio.wait_for(self._speaking.wait(), self.START_TIMEOUT)
        started = self._started_at
        await handle.wait_for_playout()
        return started

    def stop(self) -> None:
        self._session.interrupt(force=True)


def new_session(settings: Settings) -> AgentSession:
    model, voice = voice_parts(settings.voice)
    return AgentSession(
        stt=inference.STT(settings.stt, language="en"),
        tts=inference.TTS(model, voice=voice) if voice else inference.TTS(model),
        turn_handling=TURN_HANDLING,
    )


server = AgentServer()


@server.rtc_session()
async def rehearse(ctx: JobContext) -> None:
    redact_logs()
    settings = load_settings()
    session = new_session(settings)
    rehearsal = Rehearsal(
        REHEARSAL_PAIR,
        model=build_model(settings),
        speaker=SessionSpeaker(session),
        voice=settings.voice,
        stt=settings.stt,
        timing=REHEARSAL,
        next_pair=NEXT_PAIR,
        live_timeout=settings.live_timeout,
        prep_timeout=settings.prep_timeout,
        prep_budget=settings.prep_budget,
        distress_timeout=settings.distress_timeout,
        utterance_grace=settings.utterance_grace,
        utterance_max_wait=settings.utterance_max_wait,
    )

    def on_user_state(event: Any) -> None:
        if event.new_state == "speaking":
            rehearsal.speech_started()
        elif event.old_state == "speaking":
            rehearsal.speech_ended()

    def on_transcript(event: Any) -> None:
        if event.is_final:
            rehearsal.transcript(event.transcript)

    def on_chat(_session: AgentSession, event: room_io.TextInputEvent) -> None:
        rehearsal.command(event.text)

    session.on("user_state_changed", on_user_state)
    session.on("user_input_transcribed", on_transcript)

    async def get_ready() -> None:
        await rehearsal.warm_up()  # a cold model loads here, not inside the prep budget
        await rehearsal.prepare()

    # Daters are heard on time from their first word, while set pieces are still written.
    listening = asyncio.create_task(rehearsal.listen())
    preparing = asyncio.create_task(get_ready())
    try:
        await session.start(
            agent=Agent(instructions="Nova's lines come from the show runner; no replies here."),
            room=ctx.room,
            room_options=room_io.RoomOptions(
                text_input=room_io.TextInputOptions(text_input_cb=on_chat),
                # Otherwise LiveKit publishes what daters say, as text, to everyone in the room.
                text_output=False,
            ),
            **START_OPTIONS,
        )
        await ctx.wait_for_participant()
        await preparing
        await rehearsal.run()
    finally:
        listening.cancel()

    report = render_report(rehearsal)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{datetime.now():%Y-%m-%d-%H%M%S}.txt"
    path.write_text(report + "\n", encoding="utf-8")
    logger.info("rehearsal report saved to %s\n%s", path, report)
    ctx.shutdown("rehearsal finished")


def main() -> None:
    settings = load_settings()
    # The LiveKit CLI and its speech models read these from the environment.
    os.environ.setdefault("LIVEKIT_URL", settings.livekit_url)
    os.environ.setdefault("LIVEKIT_API_KEY", settings.livekit_api_key)
    os.environ.setdefault("LIVEKIT_API_SECRET", settings.livekit_api_secret)
    cli.run_app(server)


if __name__ == "__main__":
    main()
