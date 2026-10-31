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
import itertools
import math
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from nova import script
from nova.cards import Pair, Seat
from nova.config import Settings
from nova.cues import (
    AlertModerator,
    Cue,
    DecisionWindowOpened,
    ModeratorAlert,
    Moment,
    QuestionSkipped,
    Say,
    Segment,
    SegmentStarted,
    StopSpeaking,
)
from nova.date_context import DateContext, acknowledges
from nova.decisions import Choice, DecisionError, InMemoryDecisionDesk
from nova.distress import Checked, CheckError, DistressCheck, Verdict
from nova.events import Event, EventLog
from nova.floor import FINAL_LEAD, LATE_FINAL
from nova.lines import LIVE_REACTIONS, Line, LineWriter, Source
from nova.meaning import MeaningCheck
from nova.measure import ms
from nova.models import WARM_UP_ATTEMPTS, LineModel, Warmed, WarmUpError, warm_up
from nova.on_air import Closed, OnAirCues, OnAirListener, ReplyAction
from nova.priority import SafetyFirst
from nova.privacy import Data, PrivacyPolicy, Recipient
from nova.questions import AudienceQuestion, QuestionStatus
from nova.show_runner import (
    ANNOUNCES,
    SPEECH_CAP,
    Deferral,
    FinalSeen,
    NoVotes,
    Phase,
    ShowRunner,
    TransitionSeen,
    waits_for_speech,
)
from nova.timing import REHEARSAL, Timing
from nova.tonight import Tonight

# The lines left when the show finishes (the result, the outro, Next up) take well under
# this. If Nova's voice is stuck mid-line, the date still ends and the report is written.
FINAL_WAIT = 30.0
# A dater who starts speaking this soon after Nova handed a line to its voice began at the
# same moment as Nova (after a silence, both took the floor): Nova stops at once, lets them
# finish and says the line again, once. Its voice takes about half a second to start, and
# the voice detector as long to hear the dater, so neither could have known.
YIELD_WINDOW = 1.5
# Later in a line, a dater who starts and keeps going this long stops Nova too (decision
# 0004), so a "mm-hm" or a cough doesn't; Nova goes on once they've finished (after_a_cut).
YIELD_HOLD = 0.3
# A line stops for daters up to this many times: a dater who starts again, or the other
# one, is never talked over just because Nova stopped once already. Past it, speech that
# keeps coming back (a TV the voice detector hears) no longer stops the line, which plays
# out; and the waits after a line's yields share one SPEECH_CAP.
MAX_YIELDS = 3
# Cut where the words that played aren't known (a voice without word timings), a line cut
# this early is said again from its start, so at most this much is heard twice; cut later,
# the rest is dropped rather than resumed from a guess at where the cut came.
RESTART_WITHIN = 2.0
# A sentence of a line, with the punctuation that ends it.
SENTENCES = re.compile(r"(?<=[.?!…])\s+")
# Pauses between tries while the doors hold for a model that isn't ready, the last one
# repeated, within NOVA_READY_RETRY_SECONDS (Settings.ready_retry): bounded, never a loop.
HOLD_BACKOFF = (2.0, 4.0, 8.0, 16.0)
# Warm-up failures that may pass, and so are tried again while the doors hold: a model
# still loading or a server still starting. Never one that can't (a model not pulled, a bad
# key, a refusal, a failure it can't tell), which the moderator hears of at once.
MAY_PASS = frozenset({WarmUpError.TIMEOUT, WarmUpError.UNAVAILABLE})


class Opened(StrEnum):
    """How a date's start was decided (hold_until_ready): the date-start outcome."""

    READY = "ready"  # Nova was ready in time: it opened at once
    RECOVERED = "recovered"  # not in time, but ready while the doors held: it opened then
    MODERATOR = "moderator"  # the moderator opened it with the approved fallbacks
    ENDED = "ended"  # the moderator ended it before it opened


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
    MUTE = "mute"  # moderator: Nova stops at once and says nothing until unmuted
    UNMUTE = "unmute"  # moderator: Nova speaks again, from the next line the date asks for


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


class Then(StrEnum):
    """What Nova did with a line a dater cut off (decision 0004)."""

    RESUMED = "resumed"  # from the sentence it was cut in, placed by the words that played
    RESTARTED = "restarted"  # said again from its start: cut early, the words played unknown
    DROPPED = "dropped"  # the rest never said: cut later with the words unknown, or at the end
    SUPERSEDED = "superseded"  # a newer line was waiting (a reply, "Let's keep it kind.")
    STALE = "stale"  # a reply the dater who asked has moved on from: the rest never said
    STOPPED = "stopped"  # Nova was told to stop meanwhile (a hold, a report, the date ending)


@dataclass(frozen=True)
class Yield:
    """One time Nova stopped a line for a dater (decision 0004): how long from when the voice
    detector heard them start to when Nova's line had stopped, how much of what it was
    saying had played, the sentence it was cut in (0 is the first; None when the words that
    played aren't known) of how many, and what Nova did next. When the dater began and when
    Nova was told to stop are on the rehearsal's clock, for the yield rehearsal
    (nova/yield_live.py). Never any words."""

    moment: Moment
    stop_ms: int
    played_ms: int
    sentence: int | None
    sentences: int
    then: Then
    began: float = 0.0
    cut: float = 0.0

    @property
    def resumed(self) -> bool:
        """Nova said the line, or the rest of it, after the dater."""
        return self.then in (Then.RESUMED, Then.RESTARTED)


class Prefix(StrEnum):
    """What became of an optional live prefix (NOVA_LIVE_BRIDGES, decision 0012)."""

    SAID = "said"  # it led into the question, as one line
    OBSOLETE = "obsolete"  # new speech came first: the question alone
    REJECTED = "rejected"  # it failed its checks or took too long: the question alone


@dataclass(frozen=True)
class Delivery:
    """How a scheduled transition's line went (decision 0012), on the rehearsal's clock:
    queued when the runner committed it; heard, from the first audio of the delivery that
    said it to its end or cut (None: never said, or not all of it); then, after its latest
    cut. Never any words."""

    moment: Moment
    about: Seat | None
    due: float  # when its pending age counts from (Say.due)
    queued: float
    heard: tuple[float, float] | None
    yields: int
    then: Then | None
    done: float
    prefix: Prefix | None = None  # an optional live prefix's fate, if one was tried


class Speaker(Protocol):
    """Nova's voice in the room. After each line a voice may also say how much of it played:
    `played` (seconds) and `played_text` (the words, only from a voice with word timings).
    Without them the rehearsal goes by its own clock, and never places a cut in the words."""

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
    cut: bool = False  # Nova stopped what it was saying for a dater, and hasn't gone on yet
    yields: int = 0  # how often it stopped for a dater: at most MAX_YIELDS
    yield_began: float | None = None  # when that dater began, as the voice detector heard
    cut_at: float | None = None  # when Nova was told to stop for them
    resume_by: float | None = None  # the waits after this line's yields end by then
    queued_at_cut: int = 0  # lines queued so far when it was cut: any after are newer
    revision: int | None = None  # optional host work belongs to this floor revision
    bridge_obsolete: bool = False
    # What the room heard of it (decision 0012): from the first audio of the delivery that
    # said it to its end, or to the cut when the rest is rightly never said (Then.DROPPED).
    # None if it never was said, or not all of it: stopped, superseded, never voiced.
    heard: tuple[float, float] | None = None
    cut_then: Then | None = None  # what Nova did after its latest cut
    prefix: Prefix | None = None  # a bridged question's prefix, once its line is written
    # Conversational replies: what the distress check decided, and whether the dater who
    # asked has moved on since (said more, and not only an acknowledgement).
    action: ReplyAction | None = None
    stale: bool = False


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
        conversational: bool = False,
        live_bridges: bool = False,
        input_streams: int = 1,
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
        yield_hold: float = YIELD_HOLD,
        live_context: bool = True,
        tonight: Tonight | None = None,
        ready_retry: float = Settings.ready_retry,
        retry_backoff: Sequence[float] = HOLD_BACKOFF,
        privacy: PrivacyPolicy | None = None,
        meaning_check: bool = Settings.meaning_check,
        meaning_live: bool = Settings.meaning_live,
        meaning_timeout: float = Settings.meaning_timeout,
    ) -> None:
        """live_context: replies and silence prompts see this date's context (decision 0008)
        and the arc's segment (decision 0005). False runs them as before both, for the date
        benchmark's baseline (nova/benchmark.py); a real show always runs with them.
        tonight: what Nova has said tonight, shared by every date of the show (its Night,
        nova/show.py); None, a memory of this date's own. ready_retry: how long the doors may
        hold while Nova tries again to get ready (NOVA_READY_RETRY_SECONDS), retry_backoff
        the pauses between tries.
        privacy: who may receive what (nova/privacy.py, PrivacyPolicy.from_settings for real
        daters); None, the strictest. Every request the lines, the meaning check and the
        distress check send goes through it. Where this date's turns may not go, they stay
        out of the prompt; the context is still made, one for the date, and read by the
        checks on Nova's lines."""
        self.speaker = speaker
        self.privacy = privacy or PrivacyPolicy()
        self._model, self._classifier = model, classifier
        # Every request through the policy: the line model's, and the classifier's for the
        # meaning and distress checks.
        guarded = self.privacy.guard(model)
        checks = self.privacy.guard(classifier) if classifier is not None else guarded
        self._checks_model = checks
        # Where this date's turns may go, by who receives them: for the report.
        self.date_context_to: dict[str, tuple[Recipient, bool]] = {
            "lines": (guarded.recipient, self.privacy.allows(guarded, Data.DATE_CONTEXT))
        }
        if meaning_check and (meaning_live or live_bridges):
            self.date_context_to["live meaning checks"] = (
                checks.recipient,
                self.privacy.allows(checks, Data.DATE_CONTEXT),
            )
        # Safety checks before lines on a model both use (nova/priority.py): two Ollama
        # models share one server, which serves one request at a time.
        both = {(classifier or model).name.split("/")[0], model.name.split("/")[0]}
        self._gate = SafetyFirst() if both == {"ollama"} else None
        self.voice, self.stt, self.model_name = voice, stt, model.name
        if input_streams not in (1, 2):
            raise ValueError("a rehearsal has one or two speech-recognition inputs")
        self.input_streams = input_streams
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
            conversational=conversational,
            observe=self._observe,
        )
        self.timing = timing  # the running order's, for the report
        self._turn_gap = timing.turn_gap
        # This date's context (decision 0008): the listener adds the daters' checked turns,
        # the writer reads it for replies and silences, and both clear it at the end.
        context = DateContext() if live_context else None
        self._context = context
        self._live_context = live_context
        self._writer = LineWriter(
            guarded,
            pair,
            next_pair=next_pair,
            live_timeout=live_timeout,
            prep_timeout=prep_timeout,
            prep_budget=prep_budget,
            prep_parallel=prep_parallel,
            events=self._events,
            tonight=tonight or Tonight(),
            context=context,
            privacy=self.privacy,
            gate=self._gate,
            # Nova's lines checked for meaning on the classifier's model, after its checks.
            meaning=MeaningCheck(
                checks, timeout=meaning_timeout, events=self._events, gate=self._gate
            )
            if meaning_check
            else None,
            meaning_live=meaning_live,
        )
        self._on_air = OnAirListener(
            self._runner,
            DistressCheck(
                checks,
                timeout=distress_timeout,
                events=self._events,
                gate=self._gate,
            ),
            deliver=self._take,
            clock=clock,
            grace=utterance_grace,
            max_wait=utterance_max_wait,
            context=context,
        )
        self._queue: asyncio.Queue[_Pending] = asyncio.Queue()
        self._queued = 0  # lines queued so far: which waiting lines came after a cut
        self._current: _Pending | None = None  # the line Nova is speaking or waiting for
        self._optional: list[_Pending] = []  # silence prompts and prefixes new speech voids
        self._replies: list[_Pending] = []  # conversational replies, until said or dropped
        self._live_bridges = live_bridges
        self._bridge_count = 0
        self._last_bridge_at = -float("inf")
        self._talk_over_grace = timing.talk_over_grace
        self._quiet = asyncio.Event()  # nobody is speaking
        self._quiet.set()
        self._listening_since: float | None = None  # speech recognition ran from here
        self._started_at: float | None = None
        self._ended_at: float | None = None

        self.seat = Seat.LEFT
        self._seat_override: Seat | None = None
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
        self.yields: list[Yield] = []  # each line Nova stopped for a dater, numbers only
        # Every line that played, in order, words and all: in memory for the date benchmark
        # (nova/benchmark.py), never logged or written anywhere.
        self.lines: list[Line] = []
        # Lines Nova started while a dater was speaking (not replies, which answer them, nor
        # safety lines, which never wait): the benchmark's talk-over count.
        self.talked_over = 0
        # Times a dater started speaking while Nova's voice played (each stops Nova only if
        # they keep going, decision 0004), and how long at least one dater was speaking.
        self.started_under_nova = 0
        self.dater_seconds = 0.0
        self._anyone_since: float | None = None
        # The running order as it happened, seconds from the date's start; each last word's
        # time from Nova finishing its prompt to the next turn's cue; the decision window.
        self.segments: list[tuple[Segment, float]] = []
        self.last_words: list[tuple[Seat, float]] = []
        self._last_word_done: tuple[Seat, float] | None = None
        self._window_opened_at: float | None = None
        self.window_seconds: float | None = None
        # Decision 0012's diagnostics, codes and numbers only: each change in a due
        # transition, each final and the speech it was taken to say, how each transition's
        # line went, and each time the voice detector heard a dater stop.
        self.transitions: list[TransitionSeen] = []
        self.finals: list[FinalSeen] = []
        self.deliveries: list[Delivery] = []
        self.stops: list[tuple[Seat, float]] = []
        self._yield_hold = yield_hold
        # When each dater speaking now began, as the voice detector heard (two people can
        # overlap); and when each last began, which outlasts their speech for late finals.
        self._speech_since: dict[Seat, float] = {}
        self._last_began: dict[Seat, float] = {}
        self._holds: set[asyncio.Task[None]] = set()  # mid-line starts, waiting out the hold
        # The moderator's mute (launch requirement 2): how often, and the lines never said.
        self.muted = False
        self.mutes = 0
        self.unsaid = 0
        # Nova not ready to open the date: the moderator's choice, and how long it took.
        self._decided = asyncio.Event()
        self._start_anyway = False
        self.held_for: float | None = None
        # How the start was decided, and what it took: tries while the doors held (warm-ups
        # and set pieces written again), and how long from the warm-up to the decision.
        self.ready_retry, self._retry_backoff = ready_retry, tuple(retry_backoff)
        self.opened: Opened | None = None
        self.hold_retries = 0
        self.reprepared = 0
        self._startup_began: float | None = None
        self.startup_ms: int | None = None
        self.closed = False

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
        return self.input_streams * self._since(self._started_at if since is None else since)

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
        if self._startup_began is None:
            self._startup_began = self._clock()
        warming = [self._writer.warm_up(deadline=deadline)]
        separate = self._classifier is not None and self._classifier.name != self._model.name
        if separate:
            warming.append(self._warm_classifier(deadline))
        warmed = await asyncio.gather(*warming)
        self.warm_up_ms = warmed[0].ms
        self.warmed = list(zip(("model", "classifier"), warmed, strict=False))

    async def _warm_classifier(
        self, deadline: float, *, attempts: int = WARM_UP_ATTEMPTS, hold: bool = False
    ) -> Warmed:
        warmed = await warm_up(self._checks_model, deadline=deadline, attempts=attempts)
        self._events.emit(
            Event.WARMUP,
            ms=warmed.ms,
            error=warmed.error,
            attempts=warmed.attempts,
            classifier=True,
            hold=hold,
        )
        return warmed

    async def prepare(self, *, like: Rehearsal | None = None) -> None:
        """like: another rehearsal of the same pair whose set pieces to use, unwritten (the
        date benchmark's two versions of a run, nova/benchmark.py)."""
        if self._startup_began is None:
            self._startup_began = self._clock()
        if like is not None:
            self._writer.prepare_from(like._writer)
            self.prep_ms = like.prep_ms
            return
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
        """Whether to open the date (decisions 0005 and 0011; the product owner's bound,
        2026-10-22: Nova ready within 60 s of the session starting, the warm-up's 40 s and
        the prep budget's 20). Ready: at once. Not ready: the moderator is alerted
        (NOVA_NOT_READY) and the doors hold, while Nova tries again for up to ready_retry
        seconds (2026-10-24), but only a failure that may pass (MAY_PASS):
        - ready after all: the moderator is told (NOVA_READY) and the date opens;
        - not, or a failure that can't pass: the moderator is told (NOVA_STILL_NOT_READY),
          the tries stop, and the doors hold until they choose.
        `start` opens it with the approved fallbacks, `end` ends it before it opens, at any
        time, and the moderator's choice always wins. Never a silent date of fallbacks, and
        never a wait with nobody told or with no end but the moderator."""
        if self.ready:
            return self._open(Opened.READY)
        held = self._clock()
        self._queue_cues([AlertModerator(ModeratorAlert.NOVA_NOT_READY)])
        trying = asyncio.create_task(self._recover())
        deciding = asyncio.create_task(self._decided.wait())
        try:
            await asyncio.wait({trying, deciding}, return_when=asyncio.FIRST_COMPLETED)
            if not deciding.done() and trying.result():
                self._queue_cues([AlertModerator(ModeratorAlert.NOVA_READY)])
                return self._open(Opened.RECOVERED, held)
            if not deciding.done():
                self._queue_cues([AlertModerator(ModeratorAlert.NOVA_STILL_NOT_READY)])
                await deciding
        finally:
            for task in (trying, deciding):
                task.cancel()
            await asyncio.gather(trying, deciding, return_exceptions=True)
        return self._open(Opened.MODERATOR if self._start_anyway else Opened.ENDED, held)

    async def _recover(self) -> bool:
        """Tries again, while the doors hold, until Nova is ready or ready_retry runs out:
        each model that failed in a way that may pass is warmed again after a pause from
        HOLD_BACKOFF, one request held for what's left (a load is never cut short), then the
        set pieces that fell back are written again. False at once for a failure that can't
        pass. Never past its deadline: the pause, the warm-up and the prep budget all fit."""
        try:
            deadline = time.monotonic() + self.ready_retry
            last = self._retry_backoff[-1]
            pauses = itertools.chain(self._retry_backoff, itertools.repeat(last))
            lines_were_cold = False
            for pause in pauses:
                if self.ready:
                    return True
                failing = [i for i, (_, warmed) in enumerate(self.warmed) if warmed.error]
                if any(self.warmed[i][1].error not in MAY_PASS for i in failing):
                    return False
                if time.monotonic() + pause >= deadline:
                    return False
                await asyncio.sleep(pause)
                for i in failing:
                    what, _ = self.warmed[i]
                    left = deadline - time.monotonic()
                    if what == "model":
                        lines_were_cold = True
                        warmed = await self._writer.warm_up(deadline=left, attempts=1, hold=True)
                    else:
                        warmed = await self._warm_classifier(left, attempts=1, hold=True)
                    self.warmed[i] = (what, warmed)
                    self.hold_retries += 1
                if any(warmed.error for _, warmed in self.warmed):
                    continue
                left = deadline - time.monotonic()
                if (lines_were_cold or self._writer.all_fallbacks) and left > 0:
                    lines_were_cold = False
                    await self._writer.prepare(again=True, budget=min(self.prep_budget, left))
                    self.reprepared += 1
            return self.ready
        except Exception:  # a try that breaks is a try that failed: the moderator decides
            return False

    def _open(self, how: Opened, held: float | None = None) -> bool:
        """Records how the start was decided, and whether the date opens."""
        now = self._clock()
        self.opened = how
        if held is not None:
            self.held_for = now - held
        if self._startup_began is not None:
            self.startup_ms = ms(now - self._startup_began)
        fallbacks, pieces = self._writer.fallbacks
        self._events.emit(
            Event.OPENED,
            how=how,
            ms=self.startup_ms,
            held_ms=None if self.held_for is None else ms(self.held_for),
            retries=self.hold_retries,
            reprepared=self.reprepared,
            fallbacks=fallbacks,
            set_pieces=pieces,
        )
        return how is not Opened.ENDED

    def close(self) -> None:
        """The date is over, however it ended (its Night calls this, nova/show.py): the
        checks still in flight stop, the set pieces it never said leave tonight's memory,
        and its context is emptied and closed, so nothing said on it reaches another date.
        Safe to call again."""
        if self.closed:
            return
        self.closed = True
        # LiveKit may already have closed the voice session. Its interrupt() then
        # raises RuntimeError; queued work must still be canceled and date memory
        # cleared so a disconnect cannot prevent the report from being saved.
        with contextlib.suppress(RuntimeError):
            self._stop()
        self._on_air.cancel()
        self._writer.finish()
        if self._context is not None:
            self._context.close()

    def mute(self) -> None:
        """The moderator's mute (launch requirement 2): Nova stops mid-line, nothing it
        had queued is said, and nothing more until unmute(), safety lines included: the
        moderator has the date. The date goes on: the show clock, every check, and every
        alert to the moderator; the fallback segment doesn't play. Muting twice is one
        mute."""
        if self.muted:
            return
        self.muted = True
        self.mutes += 1
        self._stop()
        self._events.emit(Event.MUTED, muted=True)

    def unmute(self) -> None:
        """Nova speaks again, from the next line the date asks for: nothing said while
        muted comes back."""
        if not self.muted:
            return
        self.muted = False
        self._events.emit(Event.MUTED, muted=False)

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
        speaking = asyncio.create_task(self.speak_lines())
        try:
            while not self.finished:
                self.tick()
                await asyncio.sleep(self._until_next_tick(every))
            with contextlib.suppress(TimeoutError):  # Nova is stuck: the date ends anyway
                await asyncio.wait_for(self._queue.join(), final_wait)
        finally:
            speaking.cancel()
            await self._on_air.finish(self._distress_timeout + 0.5)
            self._writer.finish()
            self._ended_at = self._clock()

    def _until_next_tick(self, every: float) -> float:
        """every, or less when a due transition's brief opening comes sooner (decision 0012):
        one shorter than every would otherwise often fall between two ticks."""
        now = self._clock()
        opening = self._runner.next_opening(now) if self._runner.started else None
        return every if opening is None else min(every, max(0.0, opening - now))

    async def speak_queued(self) -> None:
        """Speaks every line queued so far, in order."""
        while not self._queue.empty():
            await self._speak(self._queue.get_nowait())

    def say(self, moment: Moment, text: str) -> None:
        """Queues a fixed line, as a beat would, with the date not started: for the yield
        rehearsal (nova/yield_live.py), whose test lines go through the same speaking path
        as a date's, yields and all. A date never calls it."""
        self._queue_cues([Say(moment, text=text)])

    async def lines_done(self) -> None:
        """Waits until every line queued so far has been said, cut off or dropped."""
        await self._queue.join()

    # --- What happens in the room -------------------------------------------------------------

    def command(self, text: str, seat: Seat | None = None) -> Command | None:
        """seat: who typed it, when each person has their own input (a two-person
        rehearsal), so a moment, a report, leaving or a decision is always their own, never
        the seat that spoke last. None: the seat the chat chose (one person plays both)."""
        parsed = parse_command(text)
        if parsed is None:
            return None
        command, rest = parsed
        typed = seat
        now, runner, seat = self._clock(), self._runner, typed or self.seat
        match command:
            case Command.MAYA:
                self.seat = Seat.LEFT
                self._seat_override = self.seat
            case Command.THEO:
                self.seat = Seat.RIGHT
                self._seat_override = self.seat
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
            case Command.MUTE:
                self.mute()
            case Command.UNMUTE:
                self.unmute()
            case Command.LEAVE:
                self._queue_cues(runner.dater_left(now, seat))
            case Command.REPORT:
                self._queue_cues(runner.dater_reported(now, seat))
        if command in (Command.REVEAL, Command.UNSEEN):
            # The command is the decision itself: no seat's choice is ever in the log.
            self._events.emit(Event.COMMAND, command=None, seat=None)
        else:
            self._events.emit(Event.COMMAND, command=command, seat=typed or self.seat)
        return command

    def speech_started(self, seat: Seat | None = None) -> None:
        """The voice detector heard the dater start speaking."""
        self._listening()
        self.seat = seat or self._seat_override or self._runner.expected_speaker or self.seat
        self._speaking = self.seat
        self._speech_since[self.seat] = self._last_began[self.seat] = self._clock()
        self._events.emit(
            Event.SPEECH, seat=self.seat, started=True, at_ms=self._at_ms(self._clock())
        )
        if self._current is not None and self._current.voiced_at is not None:
            self.started_under_nova += 1
        self._on_air.speech_started(self.seat)
        if self._runner.conversational:
            self._cancel_optional(force=True)
        self._heard_who_speaks()
        self._maybe_yield(self.seat)

    def speech_ended(self, seat: Seat | None = None) -> None:
        """The voice detector heard the dater stop: the end of their sentence. It ends the
        seat that started speaking, even if the chat has chosen the other one since; with
        two inputs, only the seat that stopped, while the other may still be speaking."""
        seat = seat or self._speaking or self.seat
        if self._speaking is seat:
            self._speaking = None
        self._speech_since.pop(seat, None)
        self._ended = seat, self._clock()
        self.stops.append(self._ended)
        self._events.emit(Event.SPEECH, seat=seat, started=False, at_ms=self._at_ms(self._clock()))
        self._on_air.speech_ended(seat)
        self._heard_who_speaks()

    def transcript(self, text: str, seat: Seat | None = None) -> None:
        """A final transcript of what the speaking dater said, to Nova or not. It joins
        their utterance, which the on-air listener (nova/on_air.py) acts on once it closes:
        distress first, and a reply to Nova held until the distress verdict, then dropped if
        the dater wants to stop. Nova's first line after the dater spoke is measured from
        the end of their sentence, so the grace and a check before a kind line count.

        A final arrives a moment after the voice detector hears its words end, so it belongs
        to whoever was speaking, or who stopped within LATE_FINAL seconds, even if the chat
        has chosen the other seat since; otherwise to the seat the chat chose."""
        self._listening()
        seat = seat or self._transcript_seat()
        self._on_air.hear(seat, text)
        if self._runner.conversational:
            self._cancel_optional()
            self._moved_on(seat, text)

    async def heard(self) -> None:
        """Waits for every distress check in flight, and what the show does with it."""
        await self._on_air.heard()

    # --- Internals --------------------------------------------------------------------------

    def _listening(self) -> None:
        if self._listening_since is None:
            self._listening_since = self._clock()

    def _may_yield(self, item: _Pending | None) -> bool:
        """Only a line playing now that waits for a dater anyway
        (show_runner.waits_for_speech), never a safety line or the result, and up to
        MAX_YIELDS times a line, the rest it goes on with included, and not once the waits
        after its yields have used up their SPEECH_CAP: a TV that keeps starting and stopping
        can't hold one line back for ever. A reply only in conversational mode, and only one
        Nova is answering with: the kind line is said through, as it always was."""
        return (
            item is not None
            and item.voiced_at is not None
            and not item.cut
            and (self._runner.conversational or item.yields < MAX_YIELDS)
            and (
                self._runner.conversational
                or item.resume_by is None
                or self._clock() < item.resume_by
            )
            and not item.stopped.is_set()
            and (
                item.kind is not Kind.REPLY
                or (item in self._replies and item.action is ReplyAction.SPEAK)
            )
            and waits_for_speech(item.say)
        )

    def _maybe_yield(self, seat: Seat) -> None:
        """A dater began speaking while Nova plays a line. If they began as Nova did (within
        YIELD_WINDOW of the line, or what it goes on with, going to its voice), Nova stops at
        once; later, once they've kept going for the hold, so a "mm-hm" or a cough never
        stops Nova. Either way Nova goes on once they've finished (_after_the_cut)."""
        item = self._current
        if not self._may_yield(item):
            return
        assert item is not None and item.voiced_at is not None
        # The same reading as speech_started's: the hold checks this stretch of speech is
        # unbroken by comparing the two, and a second reading of a real clock never matches
        # (the yield rehearsal found no dater ever stopped Nova after its first 1.5 s).
        began = self._speech_since.get(seat, self._clock())
        if began - item.voiced_at <= YIELD_WINDOW:
            self._yield(item, began)
            return
        try:
            hold = asyncio.ensure_future(self._yield_after_the_hold(item, seat, began))
        except RuntimeError:  # no event loop running: nothing plays, so nothing to stop
            return
        self._holds.add(hold)
        hold.add_done_callback(self._holds.discard)

    async def _yield_after_the_hold(self, item: _Pending, seat: Seat, began: float) -> None:
        await asyncio.sleep(self._yield_hold)
        # The same dater's stretch of speech, unbroken: the other one's "mm" meanwhile
        # neither ends it nor stands in for it.
        still = self._speech_since.get(seat) == began
        if still and self._current is item and self._may_yield(item):
            self._yield(item, began)

    def _yield(self, item: _Pending, began: float) -> None:
        item.cut, item.yields = True, item.yields + 1
        item.yield_began, item.cut_at = began, self._clock()
        item.queued_at_cut = self._queued
        if item.resume_by is None:
            item.resume_by = item.cut_at + SPEECH_CAP
        self.speaker.stop()

    async def _after_the_cut(
        self, item: _Pending, moment: Moment, text: str, started: float | None
    ) -> str | None:
        """A dater cut Nova off in `text`: they go first, then Nova goes on as after_a_cut
        says, unless it was told to stop meanwhile or a line queued after the cut is waiting
        (a reply to what the dater said, "Let's keep it kind."), which goes first instead.
        Logged and reported as numbers only. Returns what to say next, or None."""
        played = getattr(self.speaker, "played", None)
        if played is None:  # the voice doesn't say: from its first audio to the cut
            cut = item.cut_at if item.cut_at is not None else self._clock()
            played = max(0.0, cut - started) if started is not None else 0.0
        words = getattr(self.speaker, "played_text", None)
        then, rest, sentence, sentences = after_a_cut(text, played, words)
        began = item.yield_began if item.yield_began is not None else self._clock()
        stop_ms = ms(self._clock() - began)
        await self._after_the_dater(item)
        if item.stopped.is_set():
            then = Then.STOPPED
        elif item.stale:
            then = Then.STALE
        elif rest and self._queued > item.queued_at_cut and not self._queue.empty():
            then = Then.SUPERSEDED
        cut = item.cut_at if item.cut_at is not None else began
        cut_off = Yield(moment, stop_ms, ms(played), sentence, sentences, then, began, cut)
        self.yields.append(cut_off)
        self._events.emit(
            Event.YIELDED,
            moment=moment,
            stop_ms=stop_ms,
            played_ms=ms(played),
            sentence=sentence,
            sentences=sentences,
            then=then,
        )
        item.cut, item.cut_then = False, then
        if item.say.bridge and item.bridge_obsolete and then in (Then.RESUMED, Then.RESTARTED):
            item.prefix = Prefix.OBSOLETE  # the question goes on alone
            base = await self._writer.line_for(replace(item.say, bridge=False))
            return base.text or None
        return rest if then in (Then.RESUMED, Then.RESTARTED) else None

    async def _after_the_dater(self, item: _Pending) -> None:
        """After a yield: until nobody has spoken for the turn gap (a pause between a dater's
        sentences isn't the end of what they're saying: Nova came back in on one), or for a
        held-back transition's briefer opening (_opening), or Nova is
        told to stop; at most SPEECH_CAP from the line's first yield, shared by all of them.
        A reply is owed to the dater who asked, so it doesn't wait on the floor's opportunity,
        and one they've moved on from ends the wait at once."""
        deadline = item.resume_by if item.resume_by is not None else self._clock() + SPEECH_CAP
        while (
            not item.stopped.is_set()
            and not item.stale
            and (self._runner.conversational or self._clock() < deadline)
        ):
            ended = self._ended[1] if self._ended is not None else None
            now = self._clock()
            floor_free = (
                not self._runner.conversational
                or item.kind is Kind.REPLY
                or not self._runner.floor.waiting(now)
            )
            if (
                self._quiet.is_set()
                and floor_free
                and (ended is None or now - ended >= self._opening(item, now))
            ):
                return
            await asyncio.sleep(0.05)

    def _opening(self, item: _Pending, now: float) -> float:
        """The quiet a line waits for after the last dater: the turn gap, or the briefer
        opening a scheduled transition gets once the conversation has held it back (its
        line's due, ShowRunner.quiet_needed, decision 0012). The same before a yield's
        resumption: what already played is accounted for by after_a_cut, as before."""
        if not self._runner.conversational:
            return self._turn_gap
        return self._runner.quiet_needed(now, item.say.due, self._turn_gap)

    async def _room_for_reply(self, item: _Pending) -> bool:
        """A conversational reply waits while a dater speaks (the other one, usually: the one
        who asked has just stopped), and the turn gap after them, but no longer than the
        talk-over grace: by then the room has moved on, and the reply is dropped rather than
        said over them. False when it's dropped, or the dater who asked has moved on."""
        deadline = self._clock() + self._talk_over_grace
        while not item.stopped.is_set() and not item.stale:
            now = self._clock()
            if self._quiet.is_set():
                if self._ended is None:
                    return True
                seat, at = self._ended
                if seat is item.say.about or now - at >= self._turn_gap:
                    return True
            if now >= deadline:
                return False
            await asyncio.sleep(0.05)
        return False

    def _moved_on(self, seat: Seat, text: str) -> None:
        """The dater who spoke to Nova started speaking again after their reply was queued
        and said more than an acknowledgement ("ha", "right"): the reply answers what they've
        moved on from, so it's dropped, or stops if it's playing. Never the kind line
        (decision 0005), and never because of the other dater, whom a reply waits for
        instead (_room_for_reply). A late final of the same speech changes nothing."""
        began = self._last_began.get(seat)
        if began is None or acknowledges(text):
            return
        for item in self._replies:
            # Speech that began before the reply was queued is what the reply answers.
            if item.say.about is not seat or item.stale or began < item.cue_at:
                continue
            item.stale = True
            playing = self._current is item and item.voiced_at is not None and not item.cut
            if playing and item.action is ReplyAction.SPEAK:
                self.speaker.stop()

    def _heard_who_speaks(self) -> None:
        now = self._clock()
        if self._runner.speaking:
            self._quiet.clear()
            if self._anyone_since is None:
                self._anyone_since = now
        else:
            self._quiet.set()
            if self._anyone_since is not None:
                self.dater_seconds += now - self._anyone_since
                self._anyone_since = None

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

    def _cancel_optional(self, *, force: bool = False) -> None:
        """New speech invalidates optional host work (a silence prompt, a prefix), never a
        dater's safety request, and never a reply on its own (_moved_on decides that)."""
        revision = self._runner.floor.revision
        for item in self._optional:
            if not force and item.revision == revision:
                continue
            if item.say.bridge:
                # Discard only the prefix: the required question is still owed.
                item.bridge_obsolete = True
                item.line.cancel()
                if self._current is item and item.voiced_at is not None and not item.cut:
                    self._yield(item, self._clock())
                continue
            item.stopped.set()
            item.line.cancel()
            if self._current is item and item.voiced_at is not None:
                self.speaker.stop()

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
            if (
                isinstance(cue, Say)
                and self._live_bridges
                and self._runner.conversational
                and not self._runner.held
                and not self._runner.speaking
                and cue.away is None
                and self._bridge_count < 3
                and now - self._last_bridge_at >= 30
                and self.date_seconds > 0
                and self.nova_seconds / self.date_seconds < 0.15
                and self._context is not None
                and len(self._context) > 0
                and (
                    (cue.moment is Moment.FIRST_QUESTION and cue.about is not self._runner.first_up)
                    or cue.moment is Moment.FROM_THE_ROOM
                    or (cue.moment is Moment.LAST_WORD and cue.about is self._runner.first_up)
                )
            ):
                cue = replace(cue, bridge=True)
                self._bridge_count += 1
                self._last_bridge_at = now
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
            if isinstance(cue, SegmentStarted) and self._live_context:
                self._writer.entered(cue.segment)  # where the date is, for the arc
            self._measure(now, cue)
            if not isinstance(cue, Say):
                continue
            if self.muted:  # never said, and the date's turns never wait for it
                self.unsaid += 1
                self._line_done(cue)
                continue
            if reply_due and (cue.moment is Moment.REPLY or cue.text == script.DISTRESS_PAUSE):
                kind, reply_due = Kind.REPLY, False  # the line that answers the dater
            elif cue.bridge or (cue.text is None and cue.moment in LIVE_REACTIONS):
                kind = Kind.LIVE
            else:
                kind = Kind.PREPARED
            line = asyncio.ensure_future(self._writer.line_for(cue))
            ends = sentence_end if kind is Kind.REPLY else None
            closed = closed_at if kind is Kind.REPLY else None
            held = hold if cue.moment is Moment.REPLY else None
            item = _Pending(cue, kind, now, ends, line, held, closed)
            if self._runner.conversational and (cue.moment is Moment.SILENCE or cue.bridge):
                item.revision = self._runner.floor.revision
                self._optional.append(item)
            elif self._runner.conversational and kind is Kind.REPLY and cue.moment is Moment.REPLY:
                self._replies.append(item)
            self._queue.put_nowait(item)
            self._queued += 1
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
            if dropped in self._optional:
                self._optional.remove(dropped)
            if dropped in self._replies:
                self._replies.remove(dropped)
            self._line_done(dropped.say, dropped)
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

    async def speak_lines(self) -> None:
        """Speaks every line as it's queued, in order, until cancelled: run() does for a
        date, and the yield rehearsal (nova/yield_live.py) for its test lines."""
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
        if item.kind is Kind.REPLY or not waits_for_speech(item.say):
            return None
        if (
            self._runner.conversational
            and not self._runner.held
            and (item.say.moment in ANNOUNCES or item.say.bridge)
        ):
            # Checked again right before the voice: a dater may have started since the
            # runner found the opening, and then this waits for the next one.
            waited = False
            while not item.stopped.is_set():
                now = self._clock()
                ended = self._ended[1] if self._ended else None
                if (
                    self._quiet.is_set()
                    and not self._runner.floor.waiting(now)
                    and (ended is None or now - ended >= self._opening(item, now))
                ):
                    return now if waited else None
                await asyncio.sleep(0.05)
                waited = True
            return self._clock()
        if self._quiet.is_set():
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
            if item.revision is not None and item.revision != self._runner.floor.revision:
                item.line.cancel()
                if not item.say.bridge:
                    return
                item.bridge_obsolete = True
            picked = self._clock()
            action: ReplyAction | None = ReplyAction.SPEAK
            if item.hold is not None:  # a reply: what the distress check decides for it
                action = await self._unless_stopped(item, item.hold)
            item.action = action
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
                if item.say.bridge and item.bridge_obsolete and not item.stopped.is_set():
                    written = await self._writer.line_for(replace(item.say, bridge=False))
                if written is None or self._after_its_hold(item):
                    return
                line = written
                if item.say.bridge:
                    item.prefix = (
                        Prefix.OBSOLETE
                        if item.bridge_obsolete
                        else Prefix.SAID
                        if line.prefixed
                        else Prefix.REJECTED
                    )
            if not line.text:  # Nova says nothing for this cue
                return
            # A conversational answer waits for the room, briefly, never the kind line.
            if (
                item in self._replies
                and action is ReplyAction.SPEAK
                and not await self._room_for_reply(item)
            ):
                return
            cue_at = item.cue_at
            # A bridged question whose prefix went stale waits like any other (_polite): for
            # a transition the conversation has held back, the brief opening.
            waited = await self._polite(item)
            if item.stopped.is_set():
                return
            if waited is not None:  # measured from when the dater finished, as the runner does
                cue_at = picked = max(cue_at, waited)
            waits = item.kind is Kind.LIVE or waits_for_speech(item.say)
            if item.kind is not Kind.REPLY and waits and not self._quiet.is_set():
                self.talked_over += 1
            try:
                first: float | None = None  # when the line's first audio played
                text, again = line.text, False
                while True:
                    if again:
                        self.characters_spoken += len(text)  # said again, so billed again
                    item.voiced_at = self._clock()
                    started = await self.speaker.speak(text)
                    item.voiced_at = None
                    if started is not None:
                        first = started if first is None else first
                        self.nova_seconds += max(0.0, self._clock() - started)
                    if not item.cut or item.stopped.is_set():
                        if started is not None and not item.cut and not item.stopped.is_set():
                            item.heard = (started, self._clock())  # said to its end
                        break
                    # The dater goes first, then Nova goes on, or doesn't (after_a_cut).
                    rest = await self._after_the_cut(item, line.moment, text, started)
                    if rest is None:
                        if item.cut_then is Then.DROPPED and started is not None:
                            # Heard up to the cut, and nothing will be added (after_a_cut).
                            cut = item.cut_at if item.cut_at is not None else self._clock()
                            item.heard = (started, cut)
                        break
                    text, again = rest, True
                started = first
            except Exception:  # the voice failed: this line never played, the date goes on
                started = None
                self._events.emit(Event.VOICE_ERROR, moment=line.moment)
            if started is None and item.stopped.is_set():
                return  # cut before its first word, on purpose: not a line that never played
            if started is not None:  # only what played is billed, counted and remembered
                self.characters_spoken += len(line.text)
                self._writer.played(item.say, line)
                self.lines.append(line)
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
            if item in self._optional:
                self._optional.remove(item)
            if item in self._replies:
                self._replies.remove(item)
            self._line_done(item.say, item)  # the next dater's turn waits
            self._queue.task_done()

    def _line_done(self, say: Say, item: _Pending | None = None) -> None:
        """Tells the show runner a line is over (said, cut or dropped), and what the room
        heard of it: a question it never heard all of owes nobody an answer. A last word's
        prompt starts that dater's protected time, measured up to the next turn's cue."""
        now = self._clock()
        if say.moment is Moment.LAST_WORD and say.about is not None:
            self._last_word_done = (say.about, now)
        heard = item.heard if item is not None else None
        self._runner.line_done(
            now,
            say,
            said=heard is not None,
            heard_from=heard[0] if heard is not None else None,
            heard_until=heard[1] if heard is not None else None,
        )
        if say.due is not None:
            self._delivered(now, say, item)

    def _delivered(self, now: float, say: Say, item: _Pending | None) -> None:
        """A scheduled transition's line is over: how it went (decision 0012)."""
        assert say.due is not None
        heard = item.heard if item is not None else None
        then = item.cut_then if item is not None else None
        yields = item.yields if item is not None else 0
        queued = item.cue_at if item is not None else now
        prefix = item.prefix if item is not None else None
        self.deliveries.append(
            Delivery(say.moment, say.about, say.due, queued, heard, yields, then, now, prefix)
        )
        self._events.emit(
            Event.DELIVERED,
            moment=say.moment,
            about=say.about,
            said=heard is not None,
            heard_ms=self._at_ms(heard[0]) if heard is not None else None,
            heard_until_ms=self._at_ms(heard[1]) if heard is not None else None,
            yields=yields,
            then=then,
            prefix=prefix,
            at_ms=self._at_ms(now),
        )

    def _observe(self, seen: TransitionSeen | FinalSeen) -> None:
        """The runner's diagnostics (decision 0012), into the log and the report: codes and
        numbers, times in ms from the start of the date. Never any words."""
        if isinstance(seen, FinalSeen):
            self.finals.append(seen)
            self._events.emit(
                Event.FINAL,
                seat=seen.seat,
                at_ms=self._at_ms(seen.at),
                speech_ms=self._at_ms(seen.began),
                after_stop_ms=_since(seen.stopped, seen.at),
                move=seen.move,
                latest=seen.latest,
                hint=seen.hint,
                answered=seen.answered,
            )
            return
        self.transitions.append(seen)
        self._events.emit(
            Event.TRANSITION,
            phase=seen.phase,
            beat=seen.beat,
            number=seen.number,
            at_ms=self._at_ms(seen.at),
            due_ms=self._at_ms(seen.due),
            age_ms=ms(seen.age),
            reason=seen.reason,
            quiet_ms=None if seen.quiet is None else ms(seen.quiet),
        )

    def _at_ms(self, at: float | None) -> int | None:
        """A wall time as ms from the start of the date, for the log."""
        return _since(self._started_at, at)

    def _measure(self, now: float, cue: Cue) -> None:
        """The running order as it happened, each last word's time and the decision window,
        for the report: numbers only."""
        if isinstance(cue, SegmentStarted):
            self.segments.append((cue.segment, self._since(self._started_at)))
            if cue.segment is Segment.RESULT and self._window_opened_at is not None:
                self.window_seconds = now - self._window_opened_at
        elif isinstance(cue, DecisionWindowOpened):
            self._window_opened_at = now
        elif isinstance(cue, Say) and cue.moment in (Moment.LAST_WORD, Moment.TIME):
            if self._last_word_done is not None:
                seat, done = self._last_word_done
                self.last_words.append((seat, now - done))
                self._last_word_done = None


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


def after_a_cut(
    text: str, played: float, played_text: str | None
) -> tuple[Then, str, int | None, int]:
    """What Nova says once a dater who cut it off in `text` has finished (decision 0004),
    after `played` seconds of it, and `played_text`, the words that had played, if the voice
    knows them. Known: the sentence the first unplayed word is in, and every one after it,
    so nothing the room heard whole is said twice; every word played, nothing. Unknown: the
    whole line again if under RESTART_WITHIN seconds had played, otherwise nothing, never a
    guess at the sentence. Returns what Nova does, what it says, the sentence it was cut in
    (0 is the first; None if unknown) and how many there are."""
    parts = [part for part in SENTENCES.split(text.strip()) if part] or [text]
    if played_text is None:
        if played < RESTART_WITHIN:
            return Then.RESTARTED, text, None, len(parts)
        return Then.DROPPED, "", None, len(parts)
    heard, words = len(played_text.split()), 0
    for k, part in enumerate(parts):
        words += len(part.split())
        if heard < words:
            return Then.RESUMED, " ".join(parts[k:]), k, len(parts)
    return Then.DROPPED, "", len(parts) - 1, len(parts)


def _context_text(r: Rehearsal) -> str:
    """Who receives this date's turns: recipients from the settings, never what was said."""
    to = [f"{role} ({who.name})" for role, (who, allowed) in r.date_context_to.items() if allowed]
    kept = [f"{role} ({who.name})" for role, (who, ok) in r.date_context_to.items() if not ok]
    parts = ["this date's turns go to " + ", ".join(to)] if to else []
    if kept:
        parts.append(
            ("they stay out of " if to else "this date's turns stay out of ")
            + ", ".join(kept)
            + ": not approved local, and NOVA_DIALOGUE_TO_PROVIDER isn't set"
        )
    return "; ".join(parts)


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
        f"Nova rehearsal: voice {r.voice}, model {r.model_name}, speech recognition {r.stt}; "
        + _context_text(r),
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
    lines += ["", _utterance_row(r), _airtime_row(r), _interruptions_row(r)]
    lines += _running_order_rows(r)
    lines += _transition_rows(r)
    if r.finals or r.stops:
        lines.append(_finals_row(r))
    if r._runner.conversational:
        fates = Counter(d.prefix for d in r.deliveries if d.prefix is not None)
        outcomes = ", ".join(f"{fates[p]} {p}" for p in Prefix if fates[p])
        lines.append(
            f"Conversation: {r._runner.conversation_delay:.1f} s extra waiting; "
            f"{r._runner._recovered_delay:.1f} s recovered from optional time; "
            f"{r._bridge_count} optional prefixes attempted"
            + (f" ({outcomes})" if outcomes else "")
            + f" (enabled: {r._live_bridges})."
        )
    if r.yields:
        lines.append(_yield_row(r))
    if r.opened is not None:
        lines.append(_opened_text(r))
    if r.mutes:
        lines.append(f"Nova muted {r.mutes} times by the moderator: {r.unsaid} lines not said.")
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
            f"{round(statistics.median(ms))} ms, p95 {_percentile(ms, 95)} ms, p99 "
            f"{_percentile(ms, 99)} ms, worst {max(ms)} ms, "
            f"{sum(c.error is CheckError.TIMEOUT for c in r.checks)} timed out",
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


def _opened_text(r: Rehearsal) -> str:
    """How the date's start was decided (decision 0011): numbers and codes only."""
    fallbacks, pieces = r._writer.fallbacks
    took = f"{r.startup_ms / 1000:.1f} s from the warm-up" if r.startup_ms is not None else ""
    pieces_text = f"{fallbacks} of {pieces} set pieces approved fallbacks"
    if r.opened is Opened.READY:
        return f"Nova was ready {took}; {pieces_text}."
    held = f"the doors held {r.held_for or 0:.1f} s, {r.hold_retries} warm-up tries and " + (
        f"{r.reprepared} rewrites of set pieces"
    )
    how = {
        Opened.RECOVERED: "ready after all, and the date opened",
        Opened.MODERATOR: "the moderator opened the date with the approved fallbacks",
        Opened.ENDED: "the moderator ended it before it opened",
    }[r.opened]
    return f"Nova wasn't ready in time: {held}; then {how} ({took}); {pieces_text}."


def _warm_up_text(what: str, warmed: Warmed) -> str:
    """The warm-up of the model (or the classifier): a failure says so, never "warmed up"."""
    name = "Model" if what == "model" else "Distress check's model"
    tries = f"{warmed.attempts} attempt" + ("s" if warmed.attempts != 1 else "")
    if warmed.error is not None:
        return f"{name} warm-up failed ({warmed.error}) after {warmed.ms / 1000:.1f} s, {tries}. "
    return f"{name} warmed up in {warmed.ms / 1000:.1f} s ({tries}). "


def _yield_row(r: Rehearsal) -> str:
    """How often Nova stopped for a dater, and how fast: from when the voice detector heard
    them start to when Nova's line had stopped, as the agent measures it (the room hears it
    a little later, after the network)."""
    stops = sorted(y.stop_ms for y in r.yields)
    then = Counter(y.then for y in r.yields)
    placed = sum(y.sentence is not None for y in r.yields)
    later = sum(y.then is Then.RESUMED and (y.sentence or 0) > 0 for y in r.yields)
    return (
        f"Nova stopped for a dater {len(stops)} times: from the dater starting to Nova "
        f"stopped, median {round(statistics.median(stops))} ms, worst {stops[-1]} ms; then "
        + ", ".join(f"{t} {then[t]}" for t in Then if then[t])
        + f"; the words that played known {placed} times, resumed from a later sentence "
        f"{later} times."
    )


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
    """The brand book keeps Nova's share of the date well under a fifth: of the elapsed
    date, and of the time anyone was speaking (decision 0012)."""
    total = r.date_seconds
    share = f"{r.nova_seconds / total:.0%}" if total > 0 else "-"
    spoken = r.nova_seconds + r.dater_seconds
    of_speech = (
        f"; {r.nova_seconds / spoken:.0%} of the time anyone spoke "
        f"(the daters {r.dater_seconds:.0f} s)"
        if spoken > 0
        else ""
    )
    return (
        f"Nova's airtime: {r.nova_seconds:.0f} s of a {total:.0f} s date, {share} "
        f"(brand book: well under a fifth){of_speech}"
    )


def _interruptions_row(r: Rehearsal) -> str:
    """Who talked over whom: a dater starting while Nova spoke (which stops Nova only if
    they keep going, decision 0004), and Nova starting a line over a dater."""
    return (
        f"Interruptions: a dater started speaking while Nova spoke {r.started_under_nova} "
        f"times, and Nova stopped for them {len(r.yields)} times; Nova started a line while "
        f"a dater was speaking {r.talked_over} times."
    )


def _running_order_rows(r: Rehearsal) -> list[str]:
    """The running order as it happened, each last word and the decision window: what a
    full-length rehearsal checks (decision 0012)."""
    rows: list[str] = []
    if r.segments:
        rows.append(
            "Segments, seconds from the start: "
            + ", ".join(f"{segment} {at:.1f}" for segment, at in r.segments)
        )
    if r.last_words:
        given = ", ".join(f"{seat} {seconds:.1f} s" for seat, seconds in r.last_words)
        rows.append(
            f"Last words, from Nova finishing the prompt to the next turn: {given} (at "
            f"least {r.timing.minimum_last_word:g} s unless passed)"
        )
    if r.window_seconds is not None:
        rows.append(
            f"Decision window: {r.window_seconds:.1f} s (the running order's "
            f"{r.timing.decision_window:g} s)"
        )
    return rows


def _transition_rows(r: Rehearsal) -> list[str]:
    """Each scheduled transition (decision 0012), seconds from the start: when it came due
    and went, after how long (room holds left out) and the quiet it needed; what held it
    back, for how long; and whether the room heard its line, when, and its yields."""
    if not r.transitions:
        return []
    start = r._started_at or 0.0
    rows = ["Transitions, seconds from the start:"]
    groups: list[list[TransitionSeen]] = []
    for seen in r.transitions:
        if seen.phase is Phase.DUE or not groups:
            groups.append([])
        groups[-1].append(seen)
    for group in groups:
        first, last = group[0], group[-1]
        name = first.beat.value + (f" {first.number}" if first.number is not None else "")
        held: Counter[Deferral] = Counter()
        for seen, after in itertools.pairwise(group):
            if seen.reason is not None:
                held[seen.reason] += after.at - seen.at
        if last.phase is Phase.WENT:
            quiet = f", quiet {last.quiet:g} s" if last.quiet is not None else ""
            went = f"went {last.at - start:.1f} after {last.age:.1f} s{quiet}"
        elif last.phase is Phase.DROPPED:
            went = f"dropped {last.at - start:.1f}"
        else:
            went = "still due at the end"
        back = ", ".join(f"{reason} {held[reason]:.1f} s" for reason in Deferral if held[reason])
        line = _delivery_text(r, last, start) if last.phase is Phase.WENT else ""
        rows.append(
            f"  {name}: due {first.due - start:.1f}, {went}"
            + (f"; held back by {back}" if back else "")
            + line
        )
    return rows


def _delivery_text(r: Rehearsal, went: TransitionSeen, start: float) -> str:
    """How the transition's line went: matched by when its pending age counts from."""
    origin = went.due + went.held
    delivery = next((d for d in r.deliveries if d.due == origin), None)
    if delivery is None:
        return "; its line not over yet"
    yields = f", {delivery.yields} yields" if delivery.yields else ""
    if delivery.prefix is not None:
        yields += f", prefix {delivery.prefix}"
    if delivery.heard is None:
        then = f" ({delivery.then})" if delivery.then is not None else ""
        return f"; its line never said in full{then}{yields}: nobody owed it an answer"
    heard_from, heard_until = delivery.heard
    return f"; heard {heard_from - start:.1f} to {heard_until - start:.1f}{yields}"


def _finals_row(r: Rehearsal) -> str:
    """How late finals came after the voice detector heard their dater stop (decision 0012):
    one still to come holds the floor, so this decides how often a brief opening is usable.
    And the stops no final followed (a cough, a laugh), each holding it LATE_FINAL at most."""
    after = sorted(ms(f.at - f.stopped) for f in r.finals if f.stopped is not None)
    timing = (
        f"; after their dater stopped a median {round(statistics.median(after))} ms, p95 "
        f"{_percentile(after, 95)} ms, worst {after[-1]} ms"
        if after
        else ""
    )
    speaking = sum(f.stopped is None for f in r.finals)
    stale = sum(f.stopped is not None and not f.latest for f in r.finals)
    unanswered = sum(
        not any(f.seat is seat and at - FINAL_LEAD <= f.at <= at + LATE_FINAL for f in r.finals)
        for seat, at in r.stops
    )
    return (
        f"Finals: {len(r.finals)}{timing}; {speaking} while their dater was still speaking, "
        f"{stale} after newer speech; {sum(f.answered for f in r.finals)} answered a question "
        f"Nova had said; {sum(f.hint for f in r.finals)} closing-phrase hints. Stops with no "
        f"final within {LATE_FINAL:g} s: {unanswered} of {len(r.stops)}."
    )


def _percentile(values: Sequence[int], percent: int) -> int:
    """The nearest-rank percentile: with few checks, the worst or close to it."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]


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
