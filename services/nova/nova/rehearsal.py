"""One rehearsal date in LiveKit's Agents Playground, measured.

The show runner, Nova's lines and a voice, with the delay to Nova's first word recorded for
every line against the targets in docs/spike-results.md. Nothing here talks to LiveKit:
nova/agent.py connects Nova's voice (a Speaker) and passes in what happens in the room.

One browser plays both daters, so the chat box picks the seat that's speaking and stands in
for the app and the moderator console (Command). What daters say or type stays in memory;
the log and the report hold codes and numbers only.
"""

import asyncio
import contextlib
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from nova import script
from nova.cards import Pair, Seat
from nova.cues import AlertModerator, Cue, Moment, Say, StopSpeaking
from nova.decisions import Choice, DecisionError, InMemoryDecisionDesk
from nova.distress import Checked, DistressCheck, Verdict
from nova.events import Event, EventLog
from nova.lines import LIVE_REACTIONS, Line, LineWriter, Source
from nova.models import LineModel
from nova.on_air import OnAirCues, OnAirListener
from nova.questions import AudienceQuestion, QuestionStatus
from nova.show_runner import NoVotes, ShowRunner
from nova.timing import REHEARSAL, Timing
from nova.tonight import Tonight

# The lines left when the show finishes (the result, the outro, Next up) take well under
# this. If Nova's voice is stuck mid-line, the date still ends and the report is written.
FINAL_WAIT = 30.0


class Command(StrEnum):
    """Typed in the Playground's chat box, at any time: before the date starts, a command
    that needs a running date does nothing, except a question or an unlock, which the
    show runner keeps for the date."""

    MAYA = "maya"  # Maya is speaking from now on
    THEO = "theo"  # Theo is speaking from now on
    MOMENT = "moment"  # the speaking dater asks for a moment
    BACK = "back"  # and is back
    UNLOCK = "unlock"  # the room's gestures unlock the lightning round
    ASK = "ask"  # "ask <question>": a moderator-approved audience question
    REVEAL = "reveal"  # the speaking dater's private decision
    UNSEEN = "unseen"
    KEEP = "keep"  # moderator: keep waiting for a dater who isn't back
    RESUME = "resume"  # moderator: carry on without them
    END = "end"  # moderator: end the date
    LEAVE = "leave"  # the speaking dater leaves
    REPORT = "report"  # the speaking dater presses Report


def parse_command(text: str) -> tuple[Command, str] | None:
    word, _, rest = text.strip().partition(" ")
    try:
        command = Command(word.lower())
    except ValueError:
        return None
    rest = rest.strip()
    if command is Command.ASK and not rest:
        return None
    return command, rest


class Kind(StrEnum):
    PREPARED = "prepared"  # the words were ready without a model call during the date
    LIVE = "live"  # the model wrote it during the date
    REPLY = "reply"  # Nova's first line after a dater spoke to Nova


@dataclass(frozen=True)
class Spoken:
    """One line Nova played, and how long it took to start."""

    moment: Moment
    kind: Kind
    source: Source
    from_cue_ms: int | None  # None: Nova never started speaking it
    from_free_ms: int | None  # from the cue, or from when Nova finished the line before
    from_sentence_end_ms: int | None  # replies: from when the dater stopped speaking
    model_ms: int  # how long the line took to write, if the model wrote it


class Speaker(Protocol):
    """Nova's voice in the room."""

    async def speak(self, text: str) -> float | None:
        """Plays a line. Returns, once it has finished, the time (on the rehearsal's clock)
        its first audio played, or None if it never did."""
        ...

    def stop(self) -> None:
        """Cuts Nova off mid-line."""
        ...


@dataclass
class _Pending:
    say: Say
    kind: Kind
    cue_at: float
    sentence_end_at: float | None
    line: asyncio.Task[Line]
    hold: asyncio.Future[Verdict] | None = None  # a reply waiting for the distress check


class Rehearsal:
    def __init__(
        self,
        pair: Pair,
        *,
        model: LineModel,
        speaker: Speaker,
        voice: str,
        stt: str,
        clock: Callable[[], float] = time.monotonic,
        timing: Timing = REHEARSAL,
        next_pair: Pair | None = None,
        first_up: Seat | None = None,
        live_timeout: float = 2.0,
        prep_timeout: float = 60.0,
        distress_timeout: float = 2.0,
        events: EventLog | None = None,
    ) -> None:
        self.speaker = speaker
        self.voice, self.stt, self.model_name = voice, stt, model.name
        self._clock = clock
        self._events = events or EventLog()
        self._desk = InMemoryDecisionDesk()
        self._runner = ShowRunner(
            pair,
            desk=self._desk,
            poll=NoVotes(),
            timing=timing,
            next_pair=next_pair,
            first_up=first_up,
        )
        self._writer = LineWriter(
            model,
            pair,
            next_pair=next_pair,
            live_timeout=live_timeout,
            prep_timeout=prep_timeout,
            events=self._events,
            tonight=Tonight(),
        )
        self._on_air = OnAirListener(
            self._runner,
            DistressCheck(model, timeout=distress_timeout, events=self._events),
            deliver=self._take,
            clock=clock,
        )
        self._queue: asyncio.Queue[_Pending] = asyncio.Queue()
        self._started_at: float | None = None
        self._ended_at: float | None = None

        self.seat = Seat.LEFT
        self.cues: list[Cue] = []  # in memory only
        self.spoken: list[Spoken] = []
        self.alerts = 0
        self.characters_spoken = 0
        self.prep_ms = 0

    @property
    def finished(self) -> bool:
        return self._runner.finished

    @property
    def checks(self) -> list[Checked]:
        """Distress checks so far: verdict and time only."""
        return self._on_air.checks

    @property
    def listened_seconds(self) -> float:
        """How long speech recognition ran, for the credit estimate."""
        if self._started_at is None:
            return 0.0
        end = self._ended_at if self._ended_at is not None else self._clock()
        return end - self._started_at

    # --- The date ---------------------------------------------------------------------------

    async def prepare(self) -> None:
        started = self._clock()
        await self._writer.prepare()
        self.prep_ms = _ms(self._clock() - started)

    def start(self) -> None:
        self._started_at = self._clock()
        self._queue_cues(self._runner.start(self._started_at))

    def tick(self) -> None:
        self._queue_cues(self._runner.tick(self._clock()))

    async def run(self, *, every: float = 0.25, final_wait: float = FINAL_WAIT) -> None:
        """Runs the date to the end: ticks the show and speaks every line in order. Once the
        show has finished, the last lines get up to final_wait seconds to play."""
        self.start()
        speaking = asyncio.create_task(self._speak_forever())
        try:
            while not self.finished:
                self.tick()
                await asyncio.sleep(every)
            with contextlib.suppress(TimeoutError):  # Nova is stuck: the date ends anyway
                await asyncio.wait_for(self._queue.join(), final_wait)
        finally:
            speaking.cancel()
            self._on_air.cancel()
            self._ended_at = self._clock()

    async def speak_queued(self) -> None:
        """Speaks every line queued so far, in order."""
        while not self._queue.empty():
            await self._speak(self._queue.get_nowait())

    # --- What happens in the room -------------------------------------------------------------

    def command(self, text: str) -> Command | None:
        parsed = parse_command(text)
        if parsed is None:
            return None
        command, rest = parsed
        now, runner, seat = self._clock(), self._runner, self.seat
        match command:
            case Command.MAYA:
                self.seat = Seat.LEFT
            case Command.THEO:
                self.seat = Seat.RIGHT
            case Command.MOMENT:
                self._queue_cues(runner.need_a_moment(now, seat))
            case Command.BACK:
                self._queue_cues(runner.back(now, seat))
            case Command.UNLOCK:
                self._queue_cues(runner.unlock_lightning_round(now))
            case Command.ASK:
                question = AudienceQuestion(rest, QuestionStatus.APPROVED)
                self._queue_cues(runner.queue_question(now, question))
            case Command.REVEAL | Command.UNSEEN:
                if runner.started:
                    self._queue_cues(runner.tick(now))
                with contextlib.suppress(DecisionError):  # outside the window, or decided
                    self._desk.choose(seat, Choice(command.value))
            case Command.KEEP:
                self._queue_cues(runner.moderator_keep_waiting(now))
            case Command.RESUME:
                self._queue_cues(runner.moderator_resume(now))
            case Command.END:
                self._queue_cues(runner.moderator_end(now))
            case Command.LEAVE:
                self._queue_cues(runner.dater_left(now, seat))
            case Command.REPORT:
                self._queue_cues(runner.dater_reported(now, seat))
        self._events.emit(Event.COMMAND, command=command, seat=self.seat)
        return command

    def speech_started(self) -> None:
        """The voice detector heard the dater start speaking."""
        self._on_air.speech_started(self.seat)

    def speech_ended(self) -> None:
        """The voice detector heard the dater stop: the end of their sentence."""
        self._on_air.speech_ended(self.seat)

    def transcript(self, text: str) -> None:
        """A final transcript of what the speaking dater said, to Nova or not, heard by the
        on-air listener (nova/on_air.py): distress first, and a reply to Nova held until the
        distress verdict, then dropped if the dater wants to stop. Nova's first line after
        the dater spoke is measured from the end of their sentence, so a kind line after
        the check counts the check's time."""
        self._on_air.hear(self.seat, text)

    async def heard(self) -> None:
        """Waits for every distress check in flight, and what the show does with it."""
        await self._on_air.heard()

    # --- Internals --------------------------------------------------------------------------

    def _take(self, on_air: OnAirCues) -> None:
        self._queue_cues(on_air.cues, on_air.sentence_end, on_air.hold)

    def _queue_cues(
        self,
        cues: Iterable[Cue],
        sentence_end: float | None = None,
        hold: asyncio.Future[Verdict] | None = None,
    ) -> None:
        """hold: the distress check's verdict, which a reply waits for."""
        now = self._clock()
        reply_due = sentence_end is not None
        for cue in cues:
            self.cues.append(cue)
            if isinstance(cue, StopSpeaking):
                self._stop()
            if isinstance(cue, AlertModerator):
                self.alerts += 1
                self._events.emit(Event.ALERT, alert=cue.alert, seat=cue.seat)
            if not isinstance(cue, Say):
                continue
            if reply_due:
                kind, reply_due = Kind.REPLY, False
            elif cue.text is None and cue.moment in LIVE_REACTIONS:
                kind = Kind.LIVE
            else:
                kind = Kind.PREPARED
            line = asyncio.ensure_future(self._writer.line_for(cue))
            ends = sentence_end if kind is Kind.REPLY else None
            held = hold if cue.moment is Moment.REPLY else None
            self._queue.put_nowait(_Pending(cue, kind, now, ends, line, held))

    def _stop(self) -> None:
        """Nova stops mid-line, and nothing already queued is said."""
        while not self._queue.empty():
            self._queue.get_nowait().line.cancel()
            self._queue.task_done()
        self.speaker.stop()

    async def _speak_forever(self) -> None:
        while True:
            await self._speak(await self._queue.get())

    async def _speak(self, item: _Pending) -> None:
        try:
            picked = self._clock()
            verdict = await item.hold if item.hold is not None else None
            if verdict is Verdict.YES:
                item.line.cancel()  # the dater wants to stop: never answer them
                return
            line = await item.line
            if verdict in (Verdict.MAYBE, Verdict.UNCHECKED) and line.source is Source.FALLBACK:
                line = replace(line, text=script.TAKE_YOUR_TIME)  # never "I'll stay out of it"
            try:
                started = await self.speaker.speak(line.text)
            except Exception:  # the voice failed: this line never played, the date goes on
                started = None
                self._events.emit(Event.VOICE_ERROR, moment=line.moment)
            self.characters_spoken += len(line.text)
            spoken = Spoken(
                moment=line.moment,
                kind=item.kind,
                source=line.source,
                from_cue_ms=_since(item.cue_at, started),
                from_free_ms=_since(max(item.cue_at, picked), started),
                from_sentence_end_ms=_since(item.sentence_end_at, started),
                model_ms=line.ms,
            )
            self.spoken.append(spoken)
            self._events.emit(
                Event.SPOKEN,
                moment=spoken.moment,
                kind=spoken.kind,
                source=spoken.source,
                from_cue_ms=spoken.from_cue_ms,
                from_free_ms=spoken.from_free_ms,
                from_sentence_end_ms=spoken.from_sentence_end_ms,
                model_ms=spoken.model_ms,
            )
        except asyncio.CancelledError:
            if not item.line.cancelled():
                raise
        finally:
            self._queue.task_done()


# --- Targets (docs/spike-results.md) ------------------------------------------------------

TARGETS = (
    (Kind.PREPARED, 1000, "prepared lines within 1 s of their cue"),
    (Kind.LIVE, 2000, "live reactions within 2 s of their cue"),
    (Kind.REPLY, 2500, "replies within 2.5 s of the end of the dater's sentence"),
)


@dataclass(frozen=True)
class TargetResult:
    name: str
    limit_ms: int
    count: int
    median_ms: int | None
    worst_ms: int | None
    passed: bool | None  # None: no lines of this kind in the rehearsal


def targets(spoken: Iterable[Spoken], *, from_free: bool = False) -> list[TargetResult]:
    """Each target passes only if every line of its kind meets it. from_free measures
    prepared lines and live reactions from when Nova was free instead of from the cue."""
    spoken = list(spoken)
    results = []
    for kind, limit, name in TARGETS:
        values = [_measure(s, from_free) for s in spoken if s.kind is kind]
        known = [v for v in values if v is not None]
        results.append(
            TargetResult(
                name,
                limit,
                len(values),
                round(statistics.median(known)) if known else None,
                max(known) if known else None,
                None if not values else len(known) == len(values) and max(known) <= limit,
            )
        )
    return results


def _measure(spoken: Spoken, from_free: bool) -> int | None:
    if spoken.kind is Kind.REPLY:
        return spoken.from_sentence_end_ms
    return spoken.from_free_ms if from_free else spoken.from_cue_ms


# --- Credit -----------------------------------------------------------------------------

# LiveKit Inference, Build plan prices on 2026-10-13. An unlisted model is priced at the
# dearest rate, so the estimate errs high.
STT_PER_MINUTE = {
    "deepgram/nova-3": 0.0048,
    "deepgram/flux": 0.0065,
    "assemblyai/universal-streaming": 0.0025,
}
TTS_PER_CHARACTER = {
    "cartesia/sonic-3": 50e-6,
    "inworld/inworld-tts-1.5-max": 35e-6,
    "inworld/inworld-tts-1.5-mini": 15e-6,
    "deepgram/aura-2": 30e-6,
    "rime/mistv3": 30e-6,
    "rime/coda": 50e-6,
    "xai/tts-1": 15e-6,
}


def estimate_cost(stt: str, stt_seconds: float, voice: str, characters: int) -> float:
    return stt_seconds / 60 * price(STT_PER_MINUTE, stt) + characters * price(
        TTS_PER_CHARACTER, voice
    )


def price(prices: dict[str, float], model: str) -> float:
    model = model.partition(":")[0]
    matches = [rate for name, rate in prices.items() if model.startswith(name)]
    return matches[0] if matches else max(prices.values())


# --- The report ---------------------------------------------------------------------------


def render_report(rehearsal: Rehearsal) -> str:
    """Codes and numbers only: never what anyone said, typed or heard."""
    r = rehearsal
    cost = estimate_cost(r.stt, r.listened_seconds, r.voice, r.characters_spoken)
    lines = [
        f"Nova rehearsal: voice {r.voice}, model {r.model_name}, speech recognition {r.stt}",
        f"Set pieces prepared in {r.prep_ms / 1000:.1f} s. {len(r.spoken)} lines spoken, "
        f"{r.alerts} moderator alerts.",
        "",
        f"  {'moment':<20}{'kind':<10}{'source':<10}{'from cue':>10}{'from free':>11}"
        f"{'from sentence end':>19}{'model':>8}",
    ]
    for s in r.spoken:
        lines.append(
            f"  {s.moment:<20}{s.kind:<10}{s.source:<10}{ms_text(s.from_cue_ms):>10}"
            f"{ms_text(s.from_free_ms):>11}{ms_text(s.from_sentence_end_ms):>19}"
            f"{ms_text(s.model_ms or None):>8}"
        )
    lines += ["", "Targets, as set (from the cue; replies from the end of the dater's sentence)"]
    lines += [_target_row(t) for t in targets(r.spoken)]
    lines += ["", "The same from when Nova was free (suggestion 1; not how targets are judged)"]
    lines += [_target_row(t) for t in targets(r.spoken, from_free=True)]
    if r.checks:
        verdicts = Counter(c.verdict for c in r.checks)
        ms = [c.ms for c in r.checks]
        lines += [
            "",
            f"Distress checks: {len(ms)} ("
            + ", ".join(f"{v} {verdicts[v]}" for v in Verdict if verdicts[v])
            + f"), {_per_minute(len(ms), r.listened_seconds)} a minute, median "
            f"{round(statistics.median(ms))} ms, worst {max(ms)} ms",
        ]
    lines += ["", "Fallback rate per moment"]
    tried = Counter(s.moment for s in r.spoken)
    fell_back = Counter(s.moment for s in r.spoken if s.source is Source.FALLBACK)
    lines += [f"  {m:<20}{fell_back[m]} of {tried[m]}" for m in Moment if tried[m]]
    lines += [
        "",
        f"Estimated credit used: ${cost:.3f} (speech recognition "
        f"{r.listened_seconds / 60:.1f} min, voice {r.characters_spoken} characters)",
    ]
    return "\n".join(lines)


def _target_row(t: TargetResult) -> str:
    verdict = {True: "PASSED", False: "FAILED", None: "no lines"}[t.passed]
    return (
        f"  {t.name}: {verdict} ({t.count} lines, median {ms_text(t.median_ms)}, worst "
        f"{ms_text(t.worst_ms)})"
    )


def _since(start: float | None, end: float | None) -> int | None:
    return None if start is None or end is None else _ms(end - start)


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def ms_text(ms: int | None) -> str:
    """A time for a report: "450 ms", or "-" if there's none."""
    return "-" if ms is None else f"{ms} ms"


def _per_minute(count: int, seconds: float) -> str:
    return f"{count / (seconds / 60):.1f}" if seconds > 0 else "-"
