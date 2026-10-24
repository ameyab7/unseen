"""One rehearsal date in LiveKit's Agents Playground, measured.

The show runner, Nova's lines and a voice, with the delay to Nova's first word recorded for
every line against the targets in docs/spike-results.md. Nothing here talks to LiveKit:
nova/agent.py connects Nova's voice (a Speaker) and passes in what happens in the room.

One browser plays both daters, so the chat box picks the seat that's speaking and stands in
for the app and the moderator console (Command). What daters say stays in memory only as
long as the on-air listener needs it (nova/on_air.py); the cues the rehearsal keeps carry
none of it. The log and the report hold codes and numbers only, and never a dater's
private decision.
"""

import asyncio
import contextlib
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from nova import script
from nova.cards import Pair, Seat
from nova.config import Settings
from nova.cues import (
    AlertModerator,
    Cue,
    ModeratorAlert,
    Moment,
    QuestionSkipped,
    Say,
    StopSpeaking,
)
from nova.decisions import Choice, DecisionError, InMemoryDecisionDesk
from nova.distress import Checked, DistressCheck, Verdict
from nova.events import Event, EventLog
from nova.lines import LIVE_REACTIONS, Line, LineWriter, Source
from nova.measure import ms
from nova.models import LineModel, Warmed, warm_up
from nova.on_air import Closed, OnAirCues, OnAirListener, ReplyAction
from nova.priority import SafetyFirst
from nova.questions import AudienceQuestion, QuestionStatus
from nova.show_runner import SPEECH_CAP, NoVotes, ShowRunner, waits_for_speech
from nova.timing import REHEARSAL, Timing
from nova.tonight import Tonight

# The lines left when the show finishes (the result, the outro, Next up) take well under
# this. If Nova's voice is stuck mid-line, the date still ends and the report is written.
FINAL_WAIT = 30.0
# A final transcript this soon after the voice detector heard a dater stop is still theirs.
LATE_FINAL = 2.0
# A dater who starts speaking this soon after Nova handed a line to its voice began at the
# same moment as Nova (after a silence, both took the floor): Nova stops, lets them finish
# and says the line again, once. Its voice takes about half a second to start, and the
# voice detector as long to hear the dater, so neither could have known.
YIELD_WINDOW = 1.5


class Command(StrEnum):
    """Typed in the Playground's chat box, at any time: before the date starts, a command
    that needs a running date does nothing, except a question or an unlock, which the
    show runner keeps for the date, and end or leave, which end it at once."""

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
    START = "start"  # moderator: open a date Nova isn't ready for, with the approved fallbacks


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
    # Replies: from when the dater stopped speaking to when Nova acted on what they said
    # (the utterance's grace, with any wait for the last transcript). Part of
    # from_sentence_end_ms.
    grace_ms: int | None = None


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
    hold: asyncio.Future[ReplyAction] | None = None  # a reply: waits for the distress check
    closed_at: float | None = None  # replies: when Nova acted on what the dater said
    # Set when Nova is told to stop while this is the line being spoken or waited for.
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    voiced_at: float | None = None  # when the line went to Nova's voice, while it plays
    yielded: bool = False  # Nova stopped it for a dater who began at the same moment


class Rehearsal:
    def __init__(
        self,
        pair: Pair,
        *,
        model: LineModel,
        speaker: Speaker,
        classifier: LineModel | None = None,
        voice: str,
        stt: str,
        clock: Callable[[], float] = time.monotonic,
        timing: Timing = REHEARSAL,
        next_pair: Pair | None = None,
        first_up: Seat | None = None,
        live_timeout: float = Settings.live_timeout,
        prep_timeout: float = Settings.prep_timeout,
        prep_budget: float = Settings.prep_budget,
        prep_parallel: int | None = Settings.prep_parallel,
        warm_up: float = Settings.warm_up,
        distress_timeout: float = Settings.distress_timeout,
        utterance_grace: float = Settings.utterance_grace,
        utterance_max_wait: float = Settings.utterance_max_wait,
        events: EventLog | None = None,
    ) -> None:
        self.speaker = speaker
        self._model, self._classifier = model, classifier
        # Safety checks before lines on a model both use (nova/priority.py): two Ollama
        # models share one server, which serves one request at a time.
        both = {(classifier or model).name.split("/")[0], model.name.split("/")[0]}
        self._gate = SafetyFirst() if both == {"ollama"} else None
        self.voice, self.stt, self.model_name = voice, stt, model.name
        self.prep_budget = prep_budget
        self.warm_up_deadline = warm_up  # the warm-up's one deadline (NOVA_WARM_UP_SECONDS)
        self._distress_timeout = distress_timeout
        self.utterance_grace, self.utterance_max_wait = utterance_grace, utterance_max_wait
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
            paced=True,  # every line is reported as it finishes (_speak, _stop)
        )
        self._turn_gap = timing.turn_gap
        self._writer = LineWriter(
            model,
            pair,
            next_pair=next_pair,
            live_timeout=live_timeout,
            prep_timeout=prep_timeout,
            prep_budget=prep_budget,
            prep_parallel=prep_parallel,
            events=self._events,
            tonight=Tonight(),
            gate=self._gate,
        )
        self._on_air = OnAirListener(
            self._runner,
            DistressCheck(
                classifier or model,
                timeout=distress_timeout,
                events=self._events,
                gate=self._gate,
            ),
            deliver=self._take,
            clock=clock,
            grace=utterance_grace,
            max_wait=utterance_max_wait,
        )
        self._queue: asyncio.Queue[_Pending] = asyncio.Queue()
        self._current: _Pending | None = None  # the line Nova is speaking or waiting for
        self._talk_over_grace = timing.talk_over_grace
        self._quiet = asyncio.Event()  # nobody is speaking
        self._quiet.set()
        self._listening_since: float | None = None  # speech recognition ran from here
        self._started_at: float | None = None
        self._ended_at: float | None = None

        self.seat = Seat.LEFT
        self._speaking: Seat | None = None  # who the voice detector last heard start
        # Who the voice detector last heard stop, and when: their late final is theirs.
        self._ended: tuple[Seat, float] | None = None
        # Every cue the date gave, for the report and the tests, without the words in them:
        # what a dater said to Nova and the audience's questions go to the line writer only.
        self.cues: list[Cue] = []
        self.spoken: list[Spoken] = []
        self.alerts = 0
        self.characters_spoken = 0
        self.nova_seconds = 0.0  # how long Nova's voice played, measured
        self.prep_ms = 0
        self.warm_up_ms: int | None = None
        self.warmed: list[tuple[str, Warmed]] = []  # each model warmed up: what for, and how
        # Nova not ready to open the date: the moderator's choice, and how long it took.
        self._decided = asyncio.Event()
        self._start_anyway = False
        self.held_for: float | None = None

    @property
    def finished(self) -> bool:
        return self._runner.finished

    @property
    def prep_budget_exhausted(self) -> bool:
        """The prep budget ran out, so some set pieces use their fallback."""
        return self._writer.budget_exhausted

    @property
    def checks(self) -> list[Checked]:
        """Distress checks so far: verdict and time only."""
        return self._on_air.checks

    @property
    def utterances(self) -> list[Closed]:
        """Utterances Nova acted on so far: layer and wait only."""
        return self._on_air.closed

    @property
    def date_seconds(self) -> float:
        """How long the date ran, from its start: for airtime and rates per minute."""
        return self._since(self._started_at)

    @property
    def stt_seconds(self) -> float:
        """How long speech recognition ran, for the credit estimate: from when the session
        started listening, through the warm-up and preparation, to the end."""
        since = self._listening_since
        return self._since(self._started_at if since is None else since)

    def _since(self, start: float | None) -> float:
        if start is None:
            return 0.0
        end = self._ended_at if self._ended_at is not None else self._clock()
        return end - start

    # --- The date ---------------------------------------------------------------------------

    async def warm_up(self, *, deadline: float | None = None) -> None:
        """A tiny request to the model before anything else (nova/models.py, warm_up), held
        up to one deadline, so a cold model loads before prepare() and its budget start; and
        to the distress check's own model too, at the same time, if it's another model, so
        its first checks don't time out on the load. The same model is warmed once: twice,
        one after the other, held a date for two warm-up timeouts when the model hung. A
        warm-up that fails says so in the report and the log; the date still prepares within
        its budget, and anything not ready uses its approved fallback."""
        deadline = self.warm_up_deadline if deadline is None else deadline
        warming = [self._writer.warm_up(deadline=deadline)]
        separate = self._classifier is not None and self._classifier.name != self._model.name
        if separate:
            warming.append(self._warm_classifier(deadline))
        warmed = await asyncio.gather(*warming)
        self.warm_up_ms = warmed[0].ms
        self.warmed = list(zip(("model", "classifier"), warmed, strict=False))

    async def _warm_classifier(self, deadline: float) -> Warmed:
        assert self._classifier is not None
        warmed = await warm_up(self._classifier, deadline=deadline)
        self._events.emit(
            Event.WARMUP,
            ms=warmed.ms,
            error=warmed.error,
            attempts=warmed.attempts,
            classifier=True,
        )
        return warmed

    async def prepare(self) -> None:
        started = self._clock()
        await self._writer.prepare()
        self.prep_ms = ms(self._clock() - started)

    @property
    def ready(self) -> bool:
        """Nova can open the date: every model it uses warmed up, and its set pieces aren't
        all fallbacks. Otherwise the doors hold for the moderator (hold_until_ready)."""
        warmed = all(warmed.error is None for _, warmed in self.warmed)
        return warmed and not self._writer.all_fallbacks

    @property
    def started(self) -> bool:
        return self._started_at is not None

    async def hold_until_ready(self) -> bool:
        """Whether to open the date (decision 0005; the product owner's bound, 2026-10-22:
        Nova ready within 60 s of the session starting, the warm-up's 40 s and the prep
        budget's 20). Ready: at once. Not ready: the moderator is alerted (NOVA_NOT_READY)
        and the doors hold until they choose. `start` opens it with the approved fallbacks,
        `end` ends it before it opens. Never a silent date of fallbacks, and never an
        automatic wait with nobody told."""
        if self.ready:
            return True
        held = self._clock()
        self._queue_cues([AlertModerator(ModeratorAlert.NOVA_NOT_READY)])
        await self._decided.wait()
        self.held_for = self._clock() - held
        return self._start_anyway

    def start(self) -> None:
        self._started_at = self._clock()
        self._queue_cues(self._runner.start(self._started_at))

    def tick(self) -> None:
        """The show's beats due by now, once it has started, and any utterance that has
        closed, at any time."""
        if self._runner.started:
            self._queue_cues(self._runner.tick(self._clock()))
        self._on_air.tick()

    async def listen(self) -> None:
        """Acts on what daters say as soon as each utterance closes, until cancelled.
        nova/agent.py runs it from the start of the session, so a dater is heard on time
        while set pieces are prepared and after the date, when nothing else ticks."""
        self._listening()
        await self._on_air.listen()

    async def run(self, *, every: float = 0.25, final_wait: float = FINAL_WAIT) -> None:
        """Runs the date to the end: ticks the show and speaks every line in order. Once the
        show has finished, the last lines get up to final_wait seconds to play. Then what a
        dater was still saying is acted on, and the checks in flight get the distress
        timeout to finish (nova/on_air.py, finish)."""
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
            await self._on_air.finish(self._distress_timeout + 0.5)
            self._writer.finish()
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
                self._decided.set()  # before the date: it ends, never opens
            case Command.START:
                self._start_anyway = True
                self._decided.set()
            case Command.LEAVE:
                self._queue_cues(runner.dater_left(now, seat))
            case Command.REPORT:
                self._queue_cues(runner.dater_reported(now, seat))
        if command in (Command.REVEAL, Command.UNSEEN):
            # The command is the decision itself: no seat's choice is ever in the log.
            self._events.emit(Event.COMMAND, command=None, seat=None)
        else:
            self._events.emit(Event.COMMAND, command=command, seat=self.seat)
        return command

    def speech_started(self) -> None:
        """The voice detector heard the dater start speaking."""
        self._listening()
        self._speaking = self.seat
        self._events.emit(Event.SPEECH, seat=self.seat, started=True)
        self._on_air.speech_started(self.seat)
        self._heard_who_speaks()
        self._yield_if_both_began()

    def speech_ended(self) -> None:
        """The voice detector heard the dater stop: the end of their sentence. It ends the
        seat that started speaking, even if the chat has chosen the other one since."""
        seat, self._speaking = self._speaking or self.seat, None
        self._ended = seat, self._clock()
        self._events.emit(Event.SPEECH, seat=seat, started=False)
        self._on_air.speech_ended(seat)
        self._heard_who_speaks()

    def transcript(self, text: str) -> None:
        """A final transcript of what the speaking dater said, to Nova or not. It joins
        their utterance, which the on-air listener (nova/on_air.py) acts on once it closes:
        distress first, and a reply to Nova held until the distress verdict, then dropped if
        the dater wants to stop. Nova's first line after the dater spoke is measured from
        the end of their sentence, so the grace and a check before a kind line count.

        A final arrives a moment after the voice detector hears its words end, so it belongs
        to whoever was speaking, or who stopped within LATE_FINAL seconds, even if the chat
        has chosen the other seat since; otherwise to the seat the chat chose."""
        self._listening()
        self._on_air.hear(self._transcript_seat(), text)

    async def heard(self) -> None:
        """Waits for every distress check in flight, and what the show does with it."""
        await self._on_air.heard()

    # --- Internals --------------------------------------------------------------------------

    def _listening(self) -> None:
        if self._listening_since is None:
            self._listening_since = self._clock()

    def _yield_if_both_began(self) -> None:
        """A dater began as Nova did: Nova stops at once, never talking over them, and says
        the line once they've finished (_speak). Only a line that waits for a dater anyway
        (show_runner.waits_for_speech), never a reply, a safety line or the result, and only
        once per line."""
        item = self._current
        if (
            item is None
            or item.voiced_at is None
            or item.yielded
            or item.kind is Kind.REPLY
            or not waits_for_speech(item.say)
            or self._clock() - item.voiced_at > YIELD_WINDOW
        ):
            return
        item.yielded = True
        self.speaker.stop()

    async def _after_the_dater(self, item: _Pending) -> None:
        """After a yield: until nobody has spoken for the turn gap (a pause between a dater's
        sentences isn't the end of what they're saying: Nova came back in on one), or Nova is
        told to stop; at most SPEECH_CAP."""
        deadline = self._clock() + SPEECH_CAP
        while not item.stopped.is_set() and self._clock() < deadline:
            ended = self._ended[1] if self._ended is not None else None
            if self._quiet.is_set() and (ended is None or self._clock() - ended >= self._turn_gap):
                return
            await asyncio.sleep(0.05)

    def _heard_who_speaks(self) -> None:
        if self._runner.speaking:
            self._quiet.clear()
        else:
            self._quiet.set()

    def _transcript_seat(self) -> Seat:
        if self._speaking is not None:
            return self._speaking
        if self._ended is not None:
            seat, at = self._ended
            if self._clock() - at <= LATE_FINAL:
                return seat
        return self.seat

    def _take(self, on_air: OnAirCues) -> None:
        self._queue_cues(on_air.cues, on_air.sentence_end, on_air.hold, on_air.closed_at)

    def _queue_cues(
        self,
        cues: Iterable[Cue],
        sentence_end: float | None = None,
        hold: asyncio.Future[ReplyAction] | None = None,
        closed_at: float | None = None,
    ) -> None:
        """hold: what the distress check decides for a reply, which waits for it.
        closed_at: when Nova acted on what the dater said."""
        now = self._clock()
        reply_due = sentence_end is not None
        for cue in cues:
            self.cues.append(replace(cue, detail=None) if isinstance(cue, Say) else cue)
            if isinstance(cue, StopSpeaking):
                self._stop()
            if isinstance(cue, AlertModerator):
                self.alerts += 1
                self._events.emit(
                    Event.ALERT, alert=cue.alert, seat=cue.seat, category=cue.category
                )
            if isinstance(cue, QuestionSkipped):
                self._events.emit(Event.QUESTION_SKIPPED, reason=cue.reason)
            if not isinstance(cue, Say):
                continue
            if reply_due and (cue.moment is Moment.REPLY or cue.text == script.DISTRESS_PAUSE):
                kind, reply_due = Kind.REPLY, False  # the line that answers the dater
            elif cue.text is None and cue.moment in LIVE_REACTIONS:
                kind = Kind.LIVE
            else:
                kind = Kind.PREPARED
            line = asyncio.ensure_future(self._writer.line_for(cue))
            ends = sentence_end if kind is Kind.REPLY else None
            closed = closed_at if kind is Kind.REPLY else None
            held = hold if cue.moment is Moment.REPLY else None
            self._queue.put_nowait(_Pending(cue, kind, now, ends, line, held, closed))
        current = self._current
        if current is not None and self._after_its_hold(current):
            # The hold ended while Nova covered it: stopped mid-line, so "And we're back."
            # or the date's next line comes at once, never after a line about waiting.
            current.stopped.set()
            current.line.cancel()
            self.speaker.stop()

    def _stop(self) -> None:
        """Nova stops mid-line, and nothing already queued is said: not the line the
        speaking task has already picked up either, whether it's playing, waiting for the
        model or waiting for a distress verdict. What comes after the stop is said at once."""
        while not self._queue.empty():
            dropped = self._queue.get_nowait()
            dropped.line.cancel()
            self._runner.line_done(self._clock(), dropped.say)
            self._queue.task_done()
        if self._current is not None:
            self._current.stopped.set()
            self._current.line.cancel()
        self.speaker.stop()

    def _after_its_hold(self, item: _Pending) -> bool:
        """A line that covers a hold (talk to the room while held, "Bear with us"), once
        the hold is over: the dater is back, or the moderator resumed or ended the date.
        It's never said, or stopped mid-line if it's playing; the date goes on (decision
        0004)."""
        say = item.say
        covers = say.moment is Moment.BEAR_WITH_US or (
            say.moment is Moment.ROOM_TALK and say.number is not None
        )
        return covers and not self._runner.held

    async def _speak_forever(self) -> None:
        while True:
            await self._speak(await self._queue.get())

    async def _unless_stopped[T](self, item: _Pending, waited: asyncio.Future[T]) -> T | None:
        """What waited gives (the line, or the distress verdict), or None if Nova is told to
        stop first, or the line was cancelled. It never awaits waited itself, so cancelling
        the speaking task never cancels the line, and nothing needs to swallow its own
        cancellation."""
        if not waited.done():
            stop = asyncio.ensure_future(item.stopped.wait())
            try:
                await asyncio.wait({waited, stop}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                stop.cancel()
        if item.stopped.is_set() or waited.cancelled():
            return None
        return waited.result()

    async def _polite(self, item: _Pending) -> float | None:
        """A line that waits for a dater to finish (show_runner.waits_for_speech) waits here
        too, up to the talk-over grace: the runner holds a beat's lines only if someone is
        speaking when the beat fires, and a dater may start while the line before plays.
        Returns when the wait ended, if there was one."""
        if item.kind is Kind.REPLY or not waits_for_speech(item.say) or self._quiet.is_set():
            return None
        quiet = asyncio.ensure_future(self._quiet.wait())
        stop = asyncio.ensure_future(item.stopped.wait())
        try:
            await asyncio.wait(
                {quiet, stop}, timeout=self._talk_over_grace, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            quiet.cancel()
            stop.cancel()
        return self._clock()

    async def _speak(self, item: _Pending) -> None:
        self._current = item
        try:
            picked = self._clock()
            action: ReplyAction | None = ReplyAction.SPEAK
            if item.hold is not None:  # a reply: what the distress check decides for it
                action = await self._unless_stopped(item, item.hold)
            if action is None or item.stopped.is_set():
                return
            if self._after_its_hold(item):
                item.line.cancel()
                return
            if action is ReplyAction.DROP:
                item.line.cancel()  # never answered (nova/on_air.py, reply_after)
                return
            if action is ReplyAction.KIND_LINE:
                # The line signed off for this case, whatever the model wrote (decision 0005).
                item.line.cancel()
                line = Line(script.TAKE_YOUR_TIME, Source.FALLBACK, Moment.REPLY)
            else:
                written = await self._unless_stopped(item, item.line)
                if written is None or self._after_its_hold(item):
                    return
                line = written
            if not line.text:  # Nova says nothing for this cue
                return
            cue_at = item.cue_at
            waited = await self._polite(item)
            if item.stopped.is_set():
                return
            if waited is not None:  # measured from when the dater finished, as the runner does
                cue_at = picked = max(cue_at, waited)
            try:
                item.voiced_at = self._clock()
                started = await self.speaker.speak(line.text)
                item.voiced_at = None
                if item.yielded and not item.stopped.is_set():
                    # Both began at once: the dater goes first, then Nova says it again.
                    self._events.emit(Event.YIELDED, moment=line.moment)
                    await self._after_the_dater(item)
                    if item.stopped.is_set():
                        return
                    started = await self.speaker.speak(line.text)
                if started is not None:
                    self.nova_seconds += max(0.0, self._clock() - started)
            except Exception:  # the voice failed: this line never played, the date goes on
                started = None
                self._events.emit(Event.VOICE_ERROR, moment=line.moment)
            if started is None and item.stopped.is_set():
                return  # cut before its first word, on purpose: not a line that never played
            if started is not None:  # only what played is billed, counted and remembered
                self.characters_spoken += len(line.text)
                self._writer.played(item.say, line)
            spoken = Spoken(
                moment=line.moment,
                kind=item.kind,
                source=line.source,
                from_cue_ms=_since(cue_at, started),
                from_free_ms=_since(max(cue_at, picked), started),
                from_sentence_end_ms=_since(item.sentence_end_at, started),
                model_ms=line.ms,
                grace_ms=_since(item.sentence_end_at, item.closed_at),
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
                grace_ms=spoken.grace_ms,
            )
        except asyncio.CancelledError:
            item.line.cancel()
            raise
        finally:
            self._current = None
            self._runner.line_done(self._clock(), item.say)  # the next dater's turn waits
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
    cost = estimate_cost(r.stt, r.stt_seconds, r.voice, r.characters_spoken)
    budget = (
        f" The prep budget ({r.prep_budget:g} s) ran out: every set piece not ready by then "
        "used its fallback."
        if r.prep_budget_exhausted
        else ""
    )
    warm_up = "".join(_warm_up_text(what, warmed) for what, warmed in r.warmed)
    lines = [
        f"Nova rehearsal: voice {r.voice}, model {r.model_name}, speech recognition {r.stt}",
        f"{warm_up}Set pieces prepared in {r.prep_ms / 1000:.1f} s.{budget} {len(r.spoken)} "
        f"lines spoken, {r.alerts} moderator alerts.",
        "",
        f"  {'moment':<20}{'kind':<10}{'source':<10}{'from cue':>10}{'from free':>11}"
        f"{'from sentence end':>19}{'grace':>9}{'model':>8}",
    ]
    for s in r.spoken:
        lines.append(
            f"  {s.moment:<20}{s.kind:<10}{s.source:<10}{ms_text(s.from_cue_ms):>10}"
            f"{ms_text(s.from_free_ms):>11}{ms_text(s.from_sentence_end_ms):>19}"
            f"{ms_text(s.grace_ms):>9}{ms_text(s.model_ms or None):>8}"
        )
    lines += ["", _utterance_row(r), _airtime_row(r)]
    if r.held_for is not None:
        lines.append(
            f"Nova wasn't ready: the doors held {r.held_for:.1f} s for the moderator, who "
            + ("opened the date with the approved fallbacks." if r.started else "ended it.")
        )
    lines += [
        "",
        "Targets, as set (from the cue; replies from the end of the dater's sentence, grace "
        "included)",
    ]
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
            + f"), {_per_minute(len(ms), r.date_seconds)} a minute, median "
            f"{round(statistics.median(ms))} ms, worst {max(ms)} ms",
        ]
    lines += ["", "Fallback rate per moment"]
    tried = Counter(s.moment for s in r.spoken)
    fell_back = Counter(s.moment for s in r.spoken if s.source is Source.FALLBACK)
    lines += [f"  {m:<20}{fell_back[m]} of {tried[m]}" for m in Moment if tried[m]]
    lines += [
        "",
        f"Estimated credit used: ${cost:.3f} (speech recognition "
        f"{r.stt_seconds / 60:.1f} min, voice {r.characters_spoken} characters)",
    ]
    return "\n".join(lines)


def _warm_up_text(what: str, warmed: Warmed) -> str:
    """The warm-up of the model (or the classifier): a failure says so, never "warmed up"."""
    name = "Model" if what == "model" else "Distress check's model"
    tries = f"{warmed.attempts} attempt" + ("s" if warmed.attempts != 1 else "")
    if warmed.error is not None:
        return f"{name} warm-up failed ({warmed.error}) after {warmed.ms / 1000:.1f} s, {tries}. "
    return f"{name} warmed up in {warmed.ms / 1000:.1f} s ({tries}). "


def _utterance_row(r: Rehearsal) -> str:
    settings = (
        f"(grace {r.utterance_grace:g} s after the last transcript, at most "
        f"{r.utterance_max_wait:g} s after the dater stopped)"
    )
    waits = [ms(u.waited) for u in r.utterances if u.waited is not None]
    if not r.utterances:
        return f"Utterances: none {settings}"
    instant = sum(u.instant for u in r.utterances)
    waited = (
        f"; Nova acted a median {round(statistics.median(waits))} ms after the dater stopped, "
        f"worst {max(waits)} ms"
        if waits
        else ""
    )
    return (
        f"Utterances: {len(r.utterances)} ({instant} by the instant layer), "
        f"{_per_minute(len(r.utterances), r.date_seconds)} a minute{waited} {settings}"
    )


def _airtime_row(r: Rehearsal) -> str:
    """The brand book keeps Nova's share of the date well under a fifth."""
    total = r.date_seconds
    share = f"{r.nova_seconds / total:.0%}" if total > 0 else "-"
    return (
        f"Nova's airtime: {r.nova_seconds:.0f} s of a {total:.0f} s date, {share} "
        "(brand book: well under a fifth)"
    )


def _target_row(t: TargetResult) -> str:
    verdict = {True: "PASSED", False: "FAILED", None: "no lines"}[t.passed]
    return (
        f"  {t.name}: {verdict} ({t.count} lines, median {ms_text(t.median_ms)}, worst "
        f"{ms_text(t.worst_ms)})"
    )


def _since(start: float | None, end: float | None) -> int | None:
    return None if start is None or end is None else ms(end - start)


def ms_text(ms: int | None) -> str:
    """A time for a report: "450 ms", or "-" if there's none."""
    return "-" if ms is None else f"{ms} ms"


def _per_minute(count: int, seconds: float) -> str:
    return f"{count / (seconds / 60):.1f}" if seconds > 0 else "-"
