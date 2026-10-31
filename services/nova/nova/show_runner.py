"""Nova's show runner: walks one date's running order on a controllable clock.

Deterministic and offline. Every method takes the current time and returns the cues due
by then; nothing here talks to LiveKit, a language model or the network.

Two clocks: wall time is what callers pass in. Show time is the date's own clock, which
stops while Nova covers a "need a moment"; the running order follows show time.

Nova never learns who chose what. At the end of the decision window the runner closes
it and asks the decision desk for one thing: mutual or not.
"""

import heapq
import itertools
import math
import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from nova import running_order, script
from nova.cards import Pair, Seat
from nova.cues import (
    AlertModerator,
    BeatFailed,
    Conduct,
    Cue,
    DateEnded,
    DecisionWindowOpened,
    EndReason,
    Finished,
    LightningMeterClosed,
    ModeratorAlert,
    Moment,
    PlayFallbackSegment,
    PlaySound,
    PlayVoiceIntro,
    PollClosed,
    PollOpened,
    QuestionSkipped,
    RoomFacts,
    Say,
    Segment,
    SegmentStarted,
    ShowVerdict,
    Situation,
    Sound,
    StopFallbackSegment,
    StopSpeaking,
)
from nova.decisions import DecisionDesk, Outcome
from nova.distress import Verdict, is_unmistakable
from nova.floor import FINAL_LEAD, Floor, Move, move, possible_completion
from nova.guardrails import screen_question
from nova.questions import AudienceQuestion
from nova.timing import FULL, Timing

# Segments where the daters are on air and can ask for a moment.
ON_AIR = frozenset(
    {
        Segment.DOORS,
        Segment.FIRST_QUESTION,
        Segment.FREE_TALK,
        Segment.FROM_THE_ROOM,
        Segment.LIGHTNING_ROUND,
        Segment.LAST_WORD,
    }
)
# Segments where a long silence brings a prompt from Nova.
SILENCE_RULE = frozenset({Segment.FREE_TALK, Segment.FROM_THE_ROOM})
# Lines that go out at once even over a dater mid-sentence: safety, and the result.
NEVER_HELD = frozenset(
    {
        Moment.NEED_A_MOMENT,  # the need-a-moment cover and the distress pause's kind line
        Moment.BEAR_WITH_US,
        Moment.WELCOME_BACK,
        Moment.DATER_LEFT,
        Moment.MODERATOR_END,
        Moment.RESULT,
        Moment.SHOW_PAUSE,
    }
)
# A dater's turn: Nova asks one dater something and waits for their answer.
TURN_MOMENTS = frozenset(
    {Moment.FIRST_QUESTION, Moment.FROM_THE_ROOM, Moment.LIGHTNING_QUESTION, Moment.LAST_WORD}
)
# Beats that start a turn, or end the turns ("That's time"): when paced, each waits until
# the dater asked before had timing.answer_window after Nova finished asking them.
TURN_BEATS = frozenset(
    {
        "first_question",
        "second_prompt",
        "from_the_room",
        "audience_question",
        "lightning",
        "lightning_question",
        "last_word",
        "second_last_word",
        "time",
    }
)
# Turns that break into the daters' own conversation: they wait for timing.lull.
LULL_BEATS = frozenset({"from_the_room", "audience_question"})
# Lines that announce a scheduled transition. Conversational, whoever plays them waits for
# the same opening the runner did (quiet_needed, decision 0012) before each.
ANNOUNCES = TURN_MOMENTS | {Moment.TIME}


class Transition(StrEnum):
    """A scheduled transition, as diagnostics name it: its beat (TURN_BEATS)."""

    FIRST_QUESTION = "first_question"
    SECOND_PROMPT = "second_prompt"
    FROM_THE_ROOM = "from_the_room"
    AUDIENCE_QUESTION = "audience_question"
    LIGHTNING = "lightning"
    LIGHTNING_QUESTION = "lightning_question"
    LAST_WORD = "last_word"
    SECOND_LAST_WORD = "second_last_word"
    TIME = "time"


class Phase(StrEnum):
    DUE = "due"  # it first came due
    DEFERRED = "deferred"  # what holds it back changed
    WENT = "went"  # committed: its lines went to whoever plays them
    DROPPED = "dropped"  # it never goes: the optional second room question


class Deferral(StrEnum):
    """What holds a due conversational transition back (_deferral), in that order."""

    SPEAKING = "speaking"  # a dater is speaking: never talked over, however overdue
    PREVIOUS_LINE = "previous_line"  # Nova's last turn line isn't over (TURN_WAIT_CAP)
    LAST_WORD = "last_word"  # a last word's protected time
    QUIET = "quiet"  # not yet the quiet it needs (quiet_needed)
    # The floor's own (nova/floor.py, Hold), within the conversation's overrun budget.
    CLARIFICATION = "clarification"
    HANDOVER = "handover"
    THINKING = "thinking"
    UNHEARD = "unheard"
    ANSWER = "answer"


@dataclass(frozen=True)
class TransitionSeen:
    """A due transition, for rehearsal diagnostics (decision 0012): codes and numbers
    only. at and due are wall times; held is the room holds since it came due, which its
    age leaves out."""

    phase: Phase
    beat: Transition
    number: int | None  # a lightning question's place
    at: float
    due: float
    held: float
    reason: Deferral | None = None
    quiet: float | None = None  # the quiet it needs (deferred) or needed (went)

    @property
    def age(self) -> float:
        return self.at - self.due - self.held


@dataclass(frozen=True)
class FinalSeen:
    """A final transcript, for rehearsal diagnostics: when it arrived, which speech it was
    taken to say, and what the floor made of it. Never the words."""

    seat: Seat
    at: float
    began: float | None  # when the speech it says began (the voice detector), if known
    stopped: float | None  # when that dater stopped; None while they're still speaking
    move: Move
    latest: bool  # it says the conversation's latest speech
    hint: bool  # a closing phrase, taken as a possible completion
    answered: bool  # it answered a question Nova had said


# After a turn has waited this long (a line never reported played, daters who never pause
# for long), it goes at the first turn gap: it never cuts a dater off.
# Wall seconds, during which the show clock is stopped.
TURN_WAIT_CAP = 20.0
# Only speech this long without a break is talked over: a TV the voice detector hears.
SPEECH_CAP = 30.0
# Conduct the moderator hears about at once, with nothing said on air (decision 0006).
FLAGGED = frozenset(
    {
        Conduct.HARASSMENT,
        Conduct.SEXUAL,
        Conduct.HATE,
        Conduct.THREAT,
        Conduct.MINOR,
        Conduct.UNSURE,
    }
)

# Below this, a percentage says too much about too few people, so the verdict stays hidden.
# Proposed (decision 0004): 20 votes, rounded to the nearest 10%, since the daters see the
# share again in their recap and a share of a handful of votes is noise.
MIN_VOTES_FOR_VERDICT = 20
VERDICT_STEP = 0.1
# A beat that fails is tried again this many show seconds later, at most this many times.
BEAT_RETRY = 1.0
BEAT_RETRIES = 5


@dataclass(frozen=True)
class PollTally:
    yes: int
    no: int

    @property
    def total(self) -> int:
        return self.yes + self.no


class RoomPoll(Protocol):
    def tally(self) -> PollTally:
        """Votes so far on "Second date?"."""
        ...


class NoVotes:
    """A poll nobody has voted in: rehearsals and the evaluation have no room."""

    def tally(self) -> PollTally:
        return PollTally(yes=0, no=0)


@dataclass(order=True)
class _Beat:
    at: float  # show time
    order: int
    action: str = field(compare=False)
    arg: object = field(default=None, compare=False)
    tries: int = field(default=0, compare=False)  # how often it has failed already


class ShowRunner:
    def __init__(
        self,
        pair: Pair,
        *,
        desk: DecisionDesk,
        poll: RoomPoll,
        timing: Timing = FULL,
        next_pair: Pair | None = None,
        invitation_given_tonight: bool = False,
        first_up: Seat | None = None,
        rng: random.Random | None = None,
        paced: bool = False,
        conversational: bool = False,
        observe: Callable[[TransitionSeen | FinalSeen], None] | None = None,
    ) -> None:
        """paced: whoever plays the cues reports each of Nova's lines as it finishes
        (line_done), so a dater's turn waits for the dater asked before to have had time to
        answer (TURN_BEATS). Unpaced, every beat goes by the clock alone. observe: hears
        each change in a due transition and each final, for rehearsal diagnostics."""
        # Each lightning question gets a full gap before the last word.
        questions = running_order.LIGHTNING_QUESTIONS
        if timing.lightning + questions * timing.lightning_gap > timing.last_word:
            raise ValueError(
                f"{questions} lightning questions, {timing.lightning_gap:g} s apart, don't fit "
                "before the last word: shorten the gap or move the last word"
            )
        self._pair = pair
        self._desk = desk
        self._poll = poll
        self._timing = timing
        self._next_pair = next_pair
        self._invite = not invitation_given_tonight
        # Who answers first in the first question and the last word, chosen per date.
        if first_up is None:
            first_up = (rng or random.SystemRandom()).choice(list(Seat))
        self.first_up = first_up

        self._beats: list[_Beat] = []
        self._order = itertools.count()
        self._started_at: float | None = None
        self._held = 0.0  # wall seconds the show clock has been stopped
        self._held_since: float | None = None  # wall time the show clock stopped, if it has
        self._away: Seat | None = None  # the dater on a "need a moment"
        self._overdue = False  # not back in time: the moderator decides what happens next
        self._report_hold = False  # the hold is for a report: only the moderator lifts it
        self._reporter: Seat | None = None  # who reported, while the hold is theirs
        # A dater who asked for a moment while the show was held for something else: their
        # own pause starts when that hold ends.
        self._wanting: Seat | None = None
        # A dater the moderator resumed without came back during another hold: Nova
        # welcomes them back when that hold ends, not while it's still covering.
        self._welcome_due = False
        self._window_open = False  # the private decision window
        self._finish_when_said = False  # Next up is waiting for a dater: finish once it's out
        self._cover_lines = 0  # lines Nova has said to the room in this hold
        self._show_paused_by: Seat | None = None  # the whole date is held for their conduct
        self._unkind: dict[Seat, int] = dict.fromkeys(Seat, 0)  # unkind remarks this date
        self._segment_playing = False  # the fallback segment is covering this hold
        # Daters the moderator resumed the show without: their turns are skipped, silence
        # prompts go to the other dater, and if they come back, Nova welcomes them back. Not
        # after a report: the reporter's date ends in the app.
        self._absent: set[Seat] = set()
        self._absent_reported: set[Seat] = set()
        self._pauses: dict[Seat, int] = dict.fromkeys(Seat, 0)
        self._poll_open = False
        self._meter_open = True
        self._lightning_unlocked = False
        self._announce_unlock = False  # unlocked before the date: announced after the doors
        self._lightning_decided = False
        self._questions: deque[str] = deque()
        self._questions_asked = 0
        self._quiet_since = 0.0  # show time of the last word from a dater
        self._last_nudge: float | None = None
        self._speaking: set[Seat] = set()  # daters the voice detector hears right now
        self._waiting: list[Cue] = []  # a beat's lines and sounds, held while one speaks
        self._waiting_until: float | None = None  # wall time they go out whatever happens
        self._paced = paced
        self.conversational = conversational
        self.floor = Floor(wait=timing.floor_wait)
        self.conversation_delay = 0.0
        self._recovered_delay = 0.0
        self._turns_unfinished = 0  # turn lines cued that Nova hasn't finished saying
        self._turn_done_at: float | None = None  # wall time Nova last finished asking one
        self._last_turn_moment: Moment | None = None
        # Wall time the show clock stopped for a turn that waits (_turn_waits), like a hold:
        # everything after it moves too, so the decision window is never cut short.
        self._turn_paused_since: float | None = None
        self._turn_wait_from: float | None = None  # wall time the due turn began waiting
        self._speech_ended_at: float | None = None  # wall time a dater last stopped
        self._speech_since: float | None = None  # wall time the current speech began
        self._began: float | None = None  # wall time a dater last started speaking
        self._stopped: dict[Seat, float] = {}  # wall time each dater last stopped
        self._seat_began: dict[Seat, float] = {}  # when each dater's latest speech began
        # Each dater's earliest speech no final has said yet: what their next final says.
        self._untranscribed: dict[Seat, float] = {}
        self._final_at: dict[Seat, float] = {}  # when each dater's latest final arrived
        # A closing phrase heard while its dater was the only one speaking, and the floor's
        # revision then: a hint once they stop, if nothing has happened since.
        self._closing: tuple[Seat, int] | None = None
        # Decision 0012: the scheduled transition that is due (its beat's action and arg, so
        # a retry is the same one), the wall time it first came due, kept until it goes,
        # and the room holds since, which its pending age leaves out.
        self._transition: tuple[str, object] | None = None
        self._transition_since: float | None = None
        self._transition_held = 0.0
        self._deferred: tuple[Deferral | None, float | None] | None = None  # as last seen
        self._quiet_used: float | None = None  # the quiet it needed when last checked
        self.observe = observe or (lambda seen: None)
        # The daters the last turn asked who haven't started answering yet.
        self._awaiting: set[Seat] = set()

        self.segment: Segment | None = None
        self.end_reason: EndReason | None = None
        self.outcome: Outcome | None = None
        self.finished = False

    # --- Driving the clock ----------------------------------------------------------------

    def start(self, now: float) -> list[Cue]:
        if self._started_at is not None:
            raise RuntimeError("This date has already started")
        self._started_at = now
        if self.finished:  # ended before it started (moderator_end, dater_left)
            return []
        t = self._timing
        plan = [
            (0.0, "doors", None),
            (t.first_question, "first_question", None),
            (t.first_question + t.second_prompt_after, "second_prompt", None),
            (t.free_talk, "free_talk", None),
            (t.from_the_room, "from_the_room", None),
            (t.from_the_room + t.second_question_after, "audience_question", None),
            (t.lightning, "lightning", None),
            *(
                (t.lightning + n * t.lightning_gap, "lightning_question", n + 1)
                for n in range(1, running_order.LIGHTNING_QUESTIONS)
            ),
            (t.last_word, "last_word", None),
            (t.last_word + t.second_last_word_after, "second_last_word", None),
            (t.time, "time", None),
            (t.time + t.decision_window, "result", None),
        ]
        for at, action, arg in plan:
            self._schedule(at, action, arg)
        return self._advance(now)

    def tick(self, now: float) -> list[Cue]:
        return self._advance(now)

    @property
    def speaking(self) -> frozenset[Seat]:
        """The daters the voice detector hears right now."""
        return frozenset(self._speaking)

    @property
    def started(self) -> bool:
        return self._started_at is not None

    @property
    def held(self) -> bool:
        """The show is held: a dater's moment, a distress or conduct pause, or a report."""
        return self._held_since is not None

    def line_done(
        self,
        now: float,
        say: Say,
        *,
        said: bool = True,
        heard_from: float | None = None,
        heard_until: float | None = None,
    ) -> None:
        """Nova finished a line, or it never played (cut off, dropped, nothing to say).
        said: the room heard all of it, or all it ever will, from heard_from (its first
        audio) to heard_until (both now if not given). Paced, the dater asked in a turn
        line gets their answer window from then; conversational, a turn line never said
        owes nobody an answer, and the next turn doesn't wait for one."""
        if self.conversational and say.moment is Moment.REPLY and say.about is not None:
            if say.return_floor:
                self.floor.replied(now, say.about)
            elif self.floor.clarifying is say.about:
                self.floor.clarifying = None
                self.floor.last_move = Move.UNKNOWN
        if not self._paced or say.moment not in TURN_MOMENTS or not self.started:
            return
        self._turns_unfinished = max(0, self._turns_unfinished - 1)
        self._turn_done_at = now
        self._last_turn_moment = say.moment
        if self.conversational:
            if said:
                self.floor.asked(now, heard_from=heard_from, heard_until=heard_until)
            else:
                self.floor.withdrawn()

    @property
    def expected_speaker(self) -> Seat | None:
        """For the single-microphone simulation only; real inputs supply their seat."""
        if self.floor.handover is not None:
            return self.floor.handover
        if len(self.floor.expected) == 1:
            return next(iter(self.floor.expected))
        return None

    def turn_words(self, now: float, seat: Seat, words: str, *, to_nova: bool = False) -> None:
        """Local floor hints on each transcript, before advancing the clock. No safety
        verdict is inferred here; the listener still checks exactly the same utterances."""
        if not self.conversational or not self._live or self.held:
            return
        signal = move(words, other_name=self._pair.card(seat.other).first_name, to_nova=to_nova)
        # Which speech it says, from the voice detector: the earliest no final has said yet,
        # however late this one arrives. While they're still going, the rest is to come.
        speaking = seat in self._speaking
        began = self._untranscribed.pop(seat, None)
        if speaking:
            self._untranscribed[seat] = self._seat_began[seat]
        ended = None if speaking else self._stopped.get(seat)
        self._final_at[seat] = now
        closing = signal is Move.UNKNOWN and possible_completion(words)
        latest = self._latest(seat)
        owed = seat in self.floor.expected
        self.floor.heard(
            now,
            seat,
            signal,
            spoke=(began, ended),
            speaking=speaking,
            completion=closing and latest,
        )
        self._closing = (
            (seat, self.floor.revision) if closing and self._speaking == {seat} else None
        )
        if signal is not Move.HOLD and signal is not Move.NOVA:
            self._awaiting.discard(seat)
        answered = owed and seat not in self.floor.expected
        self.observe(
            FinalSeen(seat, now, began, ended, signal, latest, closing and latest, answered)
        )

    def _latest(self, seat: Seat) -> bool:
        """These words are the latest speech: nobody is speaking, and nobody has started
        since this dater stopped. A final can arrive after newer speech, or while the dater
        is still going: neither ends the conversation's latest utterance."""
        stopped = self._stopped.get(seat)
        if self._speaking or stopped is None:
            return False
        return self._began is None or self._began <= stopped

    # --- Things that happen in the room ---------------------------------------------------
    # Daters, the moderator and the room can do these at any time, so none of them raises
    # before start() or after the date. Moderator alerts never depend on the show clock.

    def speech_started(self, now: float, seat: Seat) -> list[Cue]:
        """The voice detector hears a dater start speaking. Until they stop, a beat's lines
        and sounds wait (up to timing.talk_over_grace), and no silence prompt comes."""
        if self.conversational:
            if self.held:
                self.floor.revision += 1
                self.floor.completion_hint = False
            else:
                self.floor.started(now, seat)
        self._closing = None  # newer speech: an earlier closing phrase isn't the last word
        cues = [] if self.conversational else self._due(now)
        if not self._speaking:
            self._speech_since = now
        self._speaking.add(seat)
        self._began = self._seat_began[seat] = now
        self._untranscribed.setdefault(seat, now)
        if not self.conversational:
            self._awaiting.discard(seat)
        if self._live and self._held_since is None:
            self._quiet_since = self._show_time(now)
        return cues + (self._due(now) if self.conversational else [])

    def speech_stopped(self, now: float, seat: Seat) -> list[Cue]:
        """The voice detector hears a dater stop. The silence clock counts from now, and
        once nobody is speaking, anything that waited goes out, in order."""
        self._speaking.discard(seat)
        self._speech_ended_at = self._stopped[seat] = now
        if not self._speaking:
            self._speech_since = None
        if self.conversational:
            self._ended_on_a_final(now, seat)
        if self._live and self._held_since is None:
            self._quiet_since = self._show_time(now)
        return self._due(now)

    def _ended_on_a_final(self, now: float, seat: Seat) -> None:
        """A final during this speech, at most FINAL_LEAD before the voice detector heard
        it stop, already said how it ended: nothing is left unheard, and a closing phrase in
        it becomes a hint, if nothing has happened since and nobody else is speaking. A
        final further back means more was said after it, which a later final will say."""
        final, began = self._final_at.get(seat), self._seat_began.get(seat)
        transcribed = (
            final is not None and began is not None and final >= began and now - final <= FINAL_LEAD
        )
        if transcribed:
            self._untranscribed.pop(seat, None)
        self.floor.stopped(now, seat, transcribed=transcribed)
        closing, self._closing = self._closing, None
        if transcribed and closing == (seat, self.floor.revision) and not self._speaking:
            self.floor.completion_hint = True

    def need_a_moment(self, now: float, seat: Seat) -> list[Cue]:
        """Nova stops mid-line, as for a report, and covers without naming anyone, and the
        show clock holds until the same dater is back. If they aren't back after
        timing.moment_alert_after, Nova asks the room to bear with us once, the moderator
        is alerted, and the show stays on hold until the moderator keeps waiting, resumes
        or ends the date. No limit on how often, but the moderator is alerted when the same
        dater pauses a second time. Asked while the show is already held for something
        else, it's kept: their own pause starts when that hold ends, instead of the show
        going on with them on air."""
        cues = self._due(now)
        if self._can_pause:
            cues.append(StopSpeaking())
            cues += self._pause(now, seat, script.NEED_A_MOMENT)
        elif self._live and self._held_since is not None and seat is not self._away:
            self._wanting = seat
        return cues

    def back(self, now: float, seat: Seat) -> list[Cue]:
        """The dater who paused is back, during the pause or after the moderator resumed
        without them: the only time Nova says "And we're back.", and only once the show is
        no longer held for anyone else. Not after a report: the reporter's date ends in the
        app, and the moderator decides; a dater who paused before someone reported is back,
        but the hold stays for the report."""
        cues = self._due(now)
        if self._live and self._away is seat and not self._report_hold:
            cues += self._clear_pause(now)
            if self._wanting is not None:  # the other dater's own moment starts now
                cues += self._take_waiting_moment(now)
            else:
                cues.append(Say(Moment.WELCOME_BACK, text=script.WELCOME_BACK))
        elif self._live and self._away is seat and self._reporter is not seat:
            self._away = None  # back, while the show stays held for the other's report
        elif self._live and self._wanting is seat:
            self._wanting = None  # back before their moment started
        elif self._live and seat in self._absent and seat not in self._absent_reported:
            self._absent.discard(seat)
            if self._held_since is None:
                cues.append(Say(Moment.WELCOME_BACK, text=script.WELCOME_BACK))
            else:
                self._welcome_due = True
        return cues

    def moderator_keep_waiting(self, now: float) -> list[Cue]:
        """The show stays on hold for the dater; Nova says nothing more."""
        return self._due(now)

    def moderator_resume(self, now: float) -> list[Cue]:
        """The show carries on without the dater who paused, and without a dater who
        reported. Nova says nothing about them: nobody is back. While they're away, their
        first-question prompt and last word are skipped (the other dater's turn comes on
        time), and silence prompts go to the other dater only; a dater who was already away
        stays away. Their decision is unchanged: they can still decide in the app. A dater
        who came back during the hold is welcomed now, and one who asked for a moment
        during it gets their own pause."""
        cues = self._due(now)
        if self._live and self._held_since is not None:
            away, reporter, welcome = self._away, self._reporter, self._welcome_due
            cues += self._clear_pause(now)
            if away is not None:
                self._absent.add(away)
            if reporter is not None:
                self._absent.add(reporter)
                self._absent_reported.add(reporter)
            if welcome:
                cues.append(Say(Moment.WELCOME_BACK, text=script.WELCOME_BACK))
            cues += self._take_waiting_moment(now)
        return cues

    def dater_left(self, now: float, seat: Seat) -> list[Cue]:
        """Nova closes kindly, with no reason and no blame, then one line thanking the room;
        Next up follows after timing.next_up_after_leave, or the fallback segment if
        there's no next pair. Before the date starts, the date ends at once: nothing has
        aired, so Nova says nothing and names nobody."""
        cues = self._due(now)
        if not self.started and not self.finished:
            return self._end_before_start(EndReason.DATER_LEFT)
        if not self._live:
            return cues
        segment = self.segment or Segment.FREE_TALK
        cues += self._clear_pause(now)
        leaver, other = self._pair.card(seat), self._pair.card(seat.other)
        cues += [StopSpeaking(), Say(Moment.DATER_LEFT, text=script.dater_left(leaver, other))]
        cues += self._close_poll()
        cues += self._end_date(EndReason.DATER_LEFT, None)
        facts = RoomFacts(segment, False, self._questions_asked, Situation.ENDED_EARLY)
        cues.append(Say(Moment.ROOM_TALK, room=facts))
        self._beats.clear()
        self._schedule(self._show_time(now) + self._timing.next_up_after_leave, "next_up", "left")
        return cues

    def moderator_end(self, now: float) -> list[Cue]:
        """Nova stops at once and says only the approved closing line. The recorded fallback
        segment then plays until the moderator chooses what happens next. Before the date
        starts, the date ends at once, with nothing said, so the moderator's end is never
        lost."""
        cues = self._due(now)
        if self.finished:
            return cues
        if not self.started:
            return self._end_before_start(EndReason.MODERATOR_ENDED)
        cues += self._clear_pause(now)
        cues.append(StopSpeaking())
        if self.end_reason is None:
            cues.append(Say(Moment.MODERATOR_END, text=script.MODERATOR_END))
            cues += self._close_poll()
            cues += self._end_date(EndReason.MODERATOR_ENDED, None)
        cues.append(PlayFallbackSegment())
        return cues + self._finish()

    def unlock_lightning_round(self, now: float) -> list[Cue]:
        """The room's gestures filled the lightning round. Too late once its slot starts.
        Before the date, or while the show is held (a dater's pause, a report, a conduct
        pause), the unlock is kept and Nova announces it once the date is on air again, so
        a paid unlock is never lost and never cheered over a pause; the meter closes at once
        either way."""
        cues = self._due(now)
        if not self.started and not self._lightning_unlocked:
            self._lightning_unlocked = self._announce_unlock = True
            return cues + self._close_meter()
        if self._live and not self._lightning_decided and not self._lightning_unlocked:
            self._lightning_unlocked = True
            if self._held_since is not None:
                self._announce_unlock = True
            else:
                unlocked: list[Cue] = [
                    Say(Moment.LIGHTNING_UNLOCKED, text=script.LIGHTNING_UNLOCKED)
                ]
                cues += self._wait_for_speech(now, unlocked)
            cues += self._close_meter()
        return cues

    def queue_question(self, now: float, question: AudienceQuestion) -> list[Cue]:
        """Only questions a moderator approved are ever read; anything else is dropped. One
        approved before the date starts is kept for it."""
        cues = self._due(now)
        if (self._live or not self.started) and question.approved:
            self._questions.append(question.text)
        return cues

    def dater_asked_nova(
        self, now: float, seat: Seat, words: str, *, wants_to_stop: bool | None = None
    ) -> list[Cue]:
        """A dater spoke to Nova on air. Their words are data for the reply, never
        instructions. Only while they're on air: not on a pause, not while deciding.

        A dater who unmistakably wants to stop (the instant layer, nova/distress.py) never
        gets a reply, so Nova can't deflect them: the moderator is alerted at once, whenever
        it happens, and if a pause can start, Nova stops mid-line, says the kind line and
        starts a need-a-moment pause for that dater; if not (another pause, the decision
        window), Nova stays quiet and the moderator acts. wants_to_stop: what the on-air
        listener's instant layer heard in the whole utterance (nova/on_air.py), which
        decides; None hears words here. Anything else that needs a closer listen is checked
        for meaning outside the runner, and the verdict comes back through distress_check;
        whoever runs the show holds the reply until then."""
        cues = self._due(now)
        if is_unmistakable(words) if wants_to_stop is None else wants_to_stop:
            return cues + self._wants_to_stop(now, seat)
        if self._live and self._held_since is None and self.segment in ON_AIR:
            away = seat.other if seat.other in self._absent else None
            cues.append(
                Say(
                    Moment.REPLY,
                    about=seat,
                    detail=words,
                    away=away,
                    return_floor=self.conversational and seat in self.floor.expected,
                )
            )
        return cues

    def dater_said(
        self, now: float, seat: Seat, words: str, *, wants_to_stop: bool | None = None
    ) -> list[Cue]:
        """What a dater said on air that wasn't to Nova: to the other dater, or to nobody.
        Nova doesn't answer, but the instant layer hears it just as in dater_asked_nova,
        since a dater who wants to stop won't always say so to Nova."""
        cues = self._due(now)
        if is_unmistakable(words) if wants_to_stop is None else wants_to_stop:
            cues += self._wants_to_stop(now, seat)
        return cues

    def distress_check(self, now: float, seat: Seat, verdict: Verdict) -> list[Cue]:
        """The meaning layer's verdict on what a dater said. Yes: as for an unmistakable
        request to stop. Maybe: the moderator is alerted. The check failed or was too slow:
        the moderator is alerted to listen in. No: nothing."""
        cues = self._due(now)
        match verdict:
            case Verdict.YES:
                cues += self._wants_to_stop(now, seat)
            case Verdict.MAYBE:
                cues.append(AlertModerator(ModeratorAlert.POSSIBLE_DISTRESS, seat))
            case Verdict.UNCHECKED:
                cues.append(AlertModerator(ModeratorAlert.DISTRESS_UNCHECKED, seat))
        return cues

    def dater_conduct(
        self,
        now: float,
        seat: Seat,
        conduct: Conduct,
        *,
        street: bool = False,
        distressed: bool = False,
    ) -> list[Cue]:
        """What a dater's own words did (decision 0006), at any time; on air, Nova acts.

        A personal detail: the moderator is alerted, and for a street or address Nova says
        the brand book's line once the dater stops ("Let's keep street names off air.").
        Unkind, the first time in a date: "Let's keep it kind." The second time by the same
        dater: "We're taking a short pause.", the show clock holds for both daters, Nova
        covers as for any hold, and the moderator is alerted and chooses resume or end.
        Only remarks Nova answered on air count: off air (before the date, on a hold, while
        deciding) an unkind remark only alerts the moderator. So does one from a dater who
        may be distressed (distressed: the same check said yes or maybe), who may be
        describing what was said to them. Harassment, sexual content, hate, a threat,
        anything about a minor, or unsure: the moderator is alerted at once with the
        category; Nova says nothing. No automatic mute: decision 0006 leaves that to the
        product owner."""
        cues = self._due(now)
        speaks = self._live and self._held_since is None and self.segment in ON_AIR
        match conduct:
            case Conduct.PERSONAL_DETAIL:
                cues.append(AlertModerator(ModeratorAlert.PERSONAL_DETAIL_ON_AIR, seat))
                if street and speaks:
                    line: list[Cue] = [
                        Say(Moment.KEEP_STREETS_OFF_AIR, text=script.KEEP_STREETS_OFF_AIR)
                    ]
                    cues += self._wait_for_speech(now, line)
            case Conduct.UNKIND if distressed or not speaks:
                cues.append(AlertModerator(ModeratorAlert.CONDUCT_FLAG, seat, Conduct.UNKIND))
            case Conduct.UNKIND:
                self._unkind[seat] += 1
                if self._unkind[seat] > 1:
                    cues.append(AlertModerator(ModeratorAlert.CONDUCT_FLAG, seat, Conduct.UNKIND))
                    cues += [StopSpeaking(), *self._pause_show(now, seat)]
                else:
                    kind: list[Cue] = [Say(Moment.KEEP_IT_KIND, text=script.KEEP_IT_KIND)]
                    cues += self._wait_for_speech(now, kind)
            case _ if conduct in FLAGGED:
                cues.append(AlertModerator(ModeratorAlert.CONDUCT_FLAG, seat, conduct))
        return cues

    def dater_reported(self, now: float, seat: Seat) -> list[Cue]:
        """A dater pressed Report. The prototype's report sheet: "Nova pauses the date and a
        live moderator listens in." The moderator is alerted first, at any time. If a pause
        can start, Nova stops mid-line and covers with the need-a-moment line and talk to
        the room, naming nobody and saying nothing about a report. The show clock holds for
        the reporter's seat until the moderator resumes or ends the date (the reporter's
        date ends in the app, so their own back() doesn't end it); the 30-second alert
        works as usual, and it never counts as a repeated pause. If the show is already held
        (the other dater's moment, a conduct pause), that hold becomes the report's: only
        the moderator lifts it, and the reporter is never put back on air. Otherwise (before
        the date, while deciding) the alert is all."""
        cues = self._due(now)
        cues.append(AlertModerator(ModeratorAlert.DATER_REPORTED, seat))
        if self._can_pause:
            cues.append(StopSpeaking())
            cues += self._pause(now, seat, script.NEED_A_MOMENT, repeatable=False)
            self._report_hold, self._reporter = True, seat
        elif self._live and self._held_since is not None:
            self._report_hold, self._reporter = True, seat
        return cues

    # --- Internals ------------------------------------------------------------------------

    @property
    def _live(self) -> bool:
        return self._started_at is not None and self.end_reason is None and not self.finished

    @property
    def _can_pause(self) -> bool:
        """On air, and nobody else on a pause, and the show not held."""
        return (
            self._live
            and self._away is None
            and self._held_since is None
            and self.segment in ON_AIR
        )

    def _pause(self, now: float, seat: Seat, line: str, *, repeatable: bool = True) -> list[Cue]:
        """Nova says the line and covers with talk to the room; the show clock holds until
        the same dater is back. Anything still waiting for a dater to finish is dropped.
        repeatable: counts towards the repeated-pause alert."""
        self._end_turn_pause(now)
        self._away, self._held_since, self._overdue = seat, now, False
        self._drop_waiting()
        self._cover_lines = 1
        cues: list[Cue] = [Say(Moment.NEED_A_MOMENT, text=line), self._cover_line()]
        if repeatable:
            self._pauses[seat] += 1
            if self._pauses[seat] > 1:
                cues.append(AlertModerator(ModeratorAlert.REPEATED_PAUSE, seat))
        return cues

    def _pause_show(self, now: float, seat: Seat) -> list[Cue]:
        """The whole date holds, for both daters: "We're taking a short pause.", then Nova
        covers as for any hold, until the moderator resumes or ends the date. seat: the
        dater whose conduct paused it."""
        self._end_turn_pause(now)
        self._held_since, self._overdue, self._show_paused_by = now, False, seat
        self._drop_waiting()
        self._cover_lines = 1
        return [Say(Moment.SHOW_PAUSE, text=script.SHOW_PAUSE), self._cover_line()]

    def _drop_waiting(self) -> None:
        """A pause drops what waited for a dater to finish. An approved audience question
        goes back to the front of the queue, uncounted, so it's read later, never lost."""
        for cue in reversed(self._waiting):
            if isinstance(cue, Say) and cue.moment is Moment.FROM_THE_ROOM and cue.detail:
                self._questions.appendleft(cue.detail)
                self._questions_asked -= 1
            if self._paced and _is_turn(cue):  # never asked, so nobody waits to answer it
                self._turns_unfinished = max(0, self._turns_unfinished - 1)
        self._waiting, self._waiting_until = [], None

    def _take_waiting_moment(self, now: float) -> list[Cue]:
        """The pause a dater asked for during another hold, now that it's over."""
        seat, self._wanting = self._wanting, None
        if seat is None or not self._can_pause:
            return []
        return self._pause(now, seat, script.NEED_A_MOMENT)

    def _end_before_start(self, reason: EndReason) -> list[Cue]:
        """The date ends before anything aired: no line, nobody named. Whoever runs the
        show then has nothing more to do for it, and start() says nothing."""
        return [*self._end_date(reason, None), PlayFallbackSegment(), *self._finish()]

    def _wants_to_stop(self, now: float, seat: Seat) -> list[Cue]:
        """The moderator is alerted, and if a pause can start, Nova stops mid-line, drops
        whatever it had queued, says the kind line at once and pauses for the dater."""
        cues: list[Cue] = [AlertModerator(ModeratorAlert.DATER_DISTRESSED, seat)]
        if self._can_pause:
            cues += [StopSpeaking(), *self._pause(now, seat, script.DISTRESS_PAUSE)]
        return cues

    def _room_talk(self, situation: Situation, **place: int) -> Say:
        """Nova talks to the room, working only from safe facts about the show."""
        if self.segment is None:
            raise RuntimeError("Nova only talks to the room during a segment")
        facts = RoomFacts(self.segment, self._poll_open, self._questions_asked, situation)
        return Say(Moment.ROOM_TALK, room=facts, **place)

    def _cover_line(self) -> Say:
        """One of the hold's lines to the room, numbered, so the line writer never says
        the same fallback twice in one hold."""
        situation = Situation.SHOW_PAUSED if self._show_paused_by is not None else Situation.PAUSE
        return self._room_talk(situation, number=self._cover_lines, of=self._timing.cover_lines)

    def _show_time(self, now: float) -> float:
        if self._started_at is None:
            raise RuntimeError("Call start() first")
        stopped_at = now
        if self._held_since is not None:
            stopped_at = self._held_since
        elif self._turn_paused_since is not None:
            stopped_at = self._turn_paused_since
        return stopped_at - self._started_at - self._held

    def _schedule(self, at: float, action: str, arg: object = None) -> None:
        heapq.heappush(self._beats, _Beat(at, next(self._order), action, arg))

    def _due(self, now: float) -> list[Cue]:
        """Everything due by now, once the date has started; nothing before."""
        return self._advance(now) if self.started else []

    def _advance(self, now: float) -> list[Cue]:
        """Runs every beat and silence prompt due by now, in show-time order."""
        self._show_time(now)  # raises if not started
        cues = self._done_waiting(now)
        while not self.finished:
            if self._held_since is not None:
                cues += self._cover(now)  # the show clock is stopped: Nova covers
                break
            if self._announce_unlock and self.segment in ON_AIR:  # unlocked during a hold
                self._announce_unlock = False
                unlocked: list[Cue] = [
                    Say(Moment.LIGHTNING_UNLOCKED, text=script.LIGHTNING_UNLOCKED)
                ]
                cues += self._wait_for_speech(now, unlocked)
            if self._turn_paused_since is not None:
                head = self._beats[0] if self._beats else None
                if head is None or not self._turn_waits(head, now):
                    self._end_turn_pause(now)
            shown = self._show_time(now)
            beat_at = self._beats[0].at if self._beats else math.inf
            silence_at = self._silence_due(now)
            if min(beat_at, silence_at) > shown:
                break
            if beat_at <= silence_at:
                if (
                    self.conversational
                    and self._beats[0].action == "audience_question"
                    and self.conversation_delay > self._recovered_delay
                ):
                    # The second room question is optional. Recover from it before
                    # shortening anyone's protected last word or decision time.
                    dropped = heapq.heappop(self._beats)
                    if self._transition == (dropped.action, dropped.arg):
                        self.observe(self._transition_seen(Phase.DROPPED, now))
                        self._forget_transition()  # never said now
                    self._recover_before("lightning", shown + self._timing.lull)
                    continue
                self._note_due(self._beats[0], now)
                if self._turn_waits(self._beats[0], now):
                    if self._turn_wait_from is None:
                        self._turn_wait_from = now
                    if self._turn_paused_since is None:
                        self._turn_paused_since = now  # the show clock waits with the turn
                    break
                beat = heapq.heappop(self._beats)
                if beat.action in TURN_BEATS:
                    self._turn_wait_from = None
                ran = self._announced(beat, self._attempt(beat, shown), now)
                if self._paced:
                    self._turns_unfinished += sum(_is_turn(cue) for cue in ran)
                    for cue in ran:
                        if isinstance(cue, Say) and cue.moment in TURN_MOMENTS:
                            self._awaiting = self._asked(cue)
                            if self.conversational:
                                self.floor.begin(self._asked(cue))
                cues += self._wait_for_speech(now, ran)
                if self._finish_when_said and not self._waiting:
                    cues += self._finish()
            else:
                cues += self._nudge(silence_at)
        return cues

    def _turn_waits(self, beat: _Beat, now: float) -> bool:
        """Paced, a turn waits while Nova is still asking the daters before (or has the line
        queued), while a dater is speaking, for timing.turn_gap after one stops (a pause
        between their sentences isn't the end of their answer; timing.lull before an audience
        question, which breaks into their conversation), and until everyone it asked has
        started answering: one who hasn't within timing.answer_window of the last person to
        speak (or of Nova finishing) is passed over. After TURN_WAIT_CAP it stops waiting
        for the lull and the answers, but still for timing.turn_gap of quiet (before, it went
        the instant one dater stopped, and Theo, starting to answer, was talked over); only
        SPEECH_CAP of speech without a break is talked over. Conversational, decision 0012
        (_deferral)."""
        if not self._paced or beat.action not in TURN_BEATS:
            return False
        if self.conversational:
            reason, quiet = self._deferral(beat, now)
            self._seen(beat, now, reason, quiet)
            return reason is not None
        if self._speaking:
            started = self._speech_since
            return started is None or now - started < SPEECH_CAP
        ended = self._speech_ended_at
        since = self._turn_wait_from
        if since is not None and self._turn_done_at is not None:
            since = max(since, self._turn_done_at)  # from when Nova finished asking
        if since is not None and now - since >= TURN_WAIT_CAP:
            return ended is not None and now < ended + self._timing.turn_gap
        if self._turns_unfinished:
            return True
        gap = self._timing.lull if beat.action in LULL_BEATS else self._timing.turn_gap
        if ended is not None and now < ended + gap:
            return True
        done = self._turn_done_at
        if done is None or not self._awaiting:
            return False
        last = max(done, ended) if ended is not None else done
        return now < last + self._timing.answer_window

    def _deferral(self, beat: _Beat, now: float) -> tuple[Deferral | None, float | None]:
        """Conversational (decision 0012): what holds a due turn back, if anything, and the
        quiet it needs after the last dater. In order: a dater speaking (never talked over);
        Nova's previous turn line, for at most TURN_WAIT_CAP; a last word's protected time;
        the quiet (quiet_needed); then, within the overrun budget, the floor (nova/floor.py,
        Floor.holds). Being overdue shortens only the quiet, never any of the others."""
        if self._speaking:
            return Deferral.SPEAKING, None
        self.floor.expected.difference_update(self._absent)
        if self.floor.handover in self._absent:
            self.floor.handover = None
        if self.floor.clarifying in self._absent:
            self.floor.clarifying = None
        if self.floor.holder in self._absent:
            self.floor.last_move = Move.UNKNOWN
        for seat in self._absent:
            self.floor.unheard.pop(seat, None)
        if self._turns_unfinished:
            since = self._turn_wait_from
            if since is None or now < since + TURN_WAIT_CAP:
                return Deferral.PREVIOUS_LINE, None
            return None, None
        if (
            self._last_turn_moment is Moment.LAST_WORD
            and self._turn_done_at is not None
            and self.floor.last_move is not Move.PASS
            and now < self._turn_done_at + self._timing.minimum_last_word
        ):
            return Deferral.LAST_WORD, None
        ended = self._speech_ended_at
        quiet = self.quiet_needed(now, self._age_from(beat), self._gap(beat))
        if ended is not None and now < ended + quiet:
            return Deferral.QUIET, quiet
        # The budget limits discretionary waiting, never authorizes speech over
        # a dater or a shorter private decision window.
        spent = self.conversation_delay
        if self._turn_paused_since is not None:
            spent += now - self._turn_paused_since
        hold = self.floor.holds(now)
        if spent < self._timing.conversation_overrun and hold is not None:
            return Deferral(hold.value), quiet
        return None, quiet

    def _gap(self, beat: _Beat) -> float:
        """A conversational turn's usual quiet after the last dater: a real lull, except
        the second last word, which follows the first."""
        return self._timing.turn_gap if beat.action == "second_last_word" else self._timing.lull

    def quiet_needed(self, now: float, due: float | None, gap: float) -> float:
        """Decision 0012: how long nobody must have spoken before a scheduled transition's
        line, whose usual wait is gap. due: when its pending age counts from, which is when it
        first came due, later by any room hold since (None: not a transition, so gap). Once
        the conversation has held it back for
        timing.transition_grace, timing.brief_opening: lively talk may never leave more, and
        no closing phrase is needed. Before that, timing.turn_gap if the latest utterance
        ended on one. Never a reason to speak while a dater is speaking: that always waits."""
        t = self._timing
        if due is None:
            return gap
        if now - due >= t.transition_grace:
            return min(gap, t.brief_opening)
        if self.floor.completion_hint:
            return min(gap, t.turn_gap)
        return gap

    def next_opening(self, now: float) -> float | None:
        """When the due transition could next go, if that's ahead: the quiet it needs after
        the last dater, or the grace's end, which shortens it. For whoever ticks the runner
        (nova/rehearsal.py, run): a brief opening is usable only if a tick lands in it. None
        while a dater speaks, during a hold, or with no transition due."""
        head = self._beats[0] if self._beats else None
        since = self._age_from(head) if head is not None else None
        ended = self._speech_ended_at
        if since is None or ended is None or self._speaking or self.held:
            return None
        t = self._timing
        at = ended + self.quiet_needed(now, since, self._gap(head))
        if now - since < t.transition_grace:
            at = min(at, max(since + t.transition_grace, ended + t.brief_opening))
        return at if at > now else None

    def _note_due(self, beat: _Beat, now: float) -> None:
        """Decision 0012: a scheduled transition's pending age starts the first time its beat
        is due, a time kept until it goes. Speech, thinking, clarification, waits, retries
        and yields never restart it; another beat at the head does."""
        if not (self.conversational and self._paced) or beat.action not in TURN_BEATS:
            return
        if self._transition != (beat.action, beat.arg):
            self._transition, self._transition_since = (beat.action, beat.arg), now
            self._transition_held, self._deferred = 0.0, None
            self.observe(self._transition_seen(Phase.DUE, now))

    def _age_from(self, beat: _Beat) -> float | None:
        """When the due transition's pending age counts from: when it first came due, later
        by the room holds since (nobody's conversation held it back during those)."""
        if self._transition != (beat.action, beat.arg) or self._transition_since is None:
            return None
        return self._transition_since + self._transition_held

    def _seen(self, beat: _Beat, now: float, reason: Deferral | None, quiet: float | None) -> None:
        """Tells the diagnostics whenever what holds the due transition back changes."""
        if self._transition != (beat.action, beat.arg):
            return
        self._quiet_used = quiet
        if self._deferred != (reason, quiet):
            self._deferred = (reason, quiet)
            if reason is not None:
                self.observe(self._transition_seen(Phase.DEFERRED, now, reason, quiet))

    def _transition_seen(
        self,
        phase: Phase,
        now: float,
        reason: Deferral | None = None,
        quiet: float | None = None,
    ) -> TransitionSeen:
        assert self._transition is not None and self._transition_since is not None
        action, arg = self._transition
        number = arg if isinstance(arg, int) else None
        held = self._transition_held
        return TransitionSeen(
            phase, Transition(action), number, now, self._transition_since, held, reason, quiet
        )

    def _forget_transition(self) -> None:
        self._transition = self._transition_since = None
        self._transition_held, self._deferred, self._quiet_used = 0.0, None, None

    def _announced(self, beat: _Beat, ran: list[Cue], now: float) -> list[Cue]:
        """The transition went: its lines carry when its pending age counts from, so the wait
        before each, and after a yield, goes on from there (nova/rehearsal.py). A beat that
        failed and is tried again (_attempt) is still the same transition, still due."""
        if self._transition != (beat.action, beat.arg) or ran == [BeatFailed(beat.action)]:
            return ran
        since = self._age_from(beat)
        self.observe(self._transition_seen(Phase.WENT, now, quiet=self._quiet_used))
        self._forget_transition()
        return [
            replace(cue, due=since) if isinstance(cue, Say) and cue.moment in ANNOUNCES else cue
            for cue in ran
        ]

    def _asked(self, say: Say) -> set[Seat]:
        """Who a turn line asks: the dater it's about, or everyone on air."""
        if say.about is not None:
            return {say.about}
        return {seat for seat in Seat if seat not in self._absent}

    def _end_turn_pause(self, now: float) -> None:
        """The show clock goes on after a turn's wait (or before a hold takes over)."""
        if self._turn_paused_since is not None:
            waited = now - self._turn_paused_since
            self._held += waited
            if self.conversational:
                self.conversation_delay += waited
            self._turn_paused_since = None
            if self.conversational:
                self._recover_slack(now)

    def _recover_slack(self, now: float) -> None:
        """Recover conversational delay from free talk before touching protected turns.
        All later beats move together, keeping the decision window exactly intact.
        Safety holds never call this with additional conversational delay."""
        if self.segment is not Segment.FREE_TALK:
            return
        boundary = next((b for b in self._beats if b.action == "from_the_room"), None)
        if boundary is None:
            return
        shown = self._show_time(now)
        minimum = max(shown, self._timing.free_talk + self._timing.minimum_free_talk)
        self._recover_before("from_the_room", minimum)

    def _recover_before(self, action: str, minimum: float) -> None:
        boundary = next((b for b in self._beats if b.action == action), None)
        if boundary is None:
            return
        available = boundary.at - minimum
        recover = min(self.conversation_delay - self._recovered_delay, max(0.0, available))
        if recover:
            target = boundary.at
            for beat in self._beats:
                if beat.at >= target:
                    beat.at -= recover
            self._recovered_delay += recover
            heapq.heapify(self._beats)

    def _attempt(self, beat: _Beat, shown: float) -> list[Cue]:
        """Runs a beat. One that raises (in production, the room poll or the decision desk
        behind it) is tried again BEAT_RETRY show seconds later, up to BEAT_RETRIES times,
        and the console hears each failure: so no beat is lost, and nothing a dater or the
        moderator does at that moment loses its alert to the exception."""
        try:
            return self._run(beat)
        except Exception:
            if beat.tries >= BEAT_RETRIES:
                return [BeatFailed(beat.action, gave_up=True)]
            again = _Beat(
                shown + BEAT_RETRY, next(self._order), beat.action, beat.arg, beat.tries + 1
            )
            heapq.heappush(self._beats, again)
            return [BeatFailed(beat.action)]

    def _run(self, beat: _Beat) -> list[Cue]:
        at = beat.at
        first, second = self.first_up, self.first_up.other
        match beat.action:
            case "doors":
                cues: list[Cue] = [
                    PlaySound(Sound.ON_AIR),
                    *self._enter(Segment.DOORS, at),
                    Say(Moment.DOORS_INTRO),
                    PlayVoiceIntro(Seat.LEFT),
                    PlayVoiceIntro(Seat.RIGHT),
                ]
                if self._announce_unlock:  # unlocked before the date started
                    self._announce_unlock = False
                    cues.append(Say(Moment.LIGHTNING_UNLOCKED, text=script.LIGHTNING_UNLOCKED))
                return cues
            case "first_question":
                return [
                    *self._enter(Segment.FIRST_QUESTION, at),
                    *self._present(self._prompt(first)),
                ]
            case "second_prompt":
                return self._present(self._prompt(second))
            case "free_talk":
                return self._enter(Segment.FREE_TALK, at)
            case "from_the_room":
                return [*self._enter(Segment.FROM_THE_ROOM, at), *self._audience_question(at)]
            case "audience_question":
                if self.segment is not Segment.FROM_THE_ROOM:
                    return []
                return self._audience_question(at)
            case "lightning":
                self._lightning_decided = True
                closed = self._close_meter()
                if not self._lightning_unlocked:
                    return [*closed, *self._enter(Segment.FREE_TALK, at)]
                return [
                    *closed,
                    *self._enter(Segment.LIGHTNING_ROUND, at),
                    running_order.lightning_question(1),
                ]
            case "lightning_question":
                if self.segment is not Segment.LIGHTNING_ROUND:
                    return []
                return [running_order.lightning_question(int(str(beat.arg)))]
            case "last_word":
                return [
                    *self._enter(Segment.LAST_WORD, at),
                    *self._present(running_order.last_word(first)),
                ]
            case "second_last_word":
                return self._present(running_order.last_word(second))
            case "time":
                return self._time(at)
            case "result":
                return self._result(at)
            case "next_up":
                return self._next_up(at, after_a_leave=beat.arg == "left")
        raise ValueError(f"Unknown beat {beat.action!r}")

    def _enter(self, segment: Segment, at: float) -> list[Cue]:
        self.segment = segment
        self._quiet_since = at
        if self.conversational and segment is Segment.FREE_TALK:
            # If an earlier turn ran long, recover it from this optional stretch.
            self._recover_slack(self._started_at + self._held + at)
        return [SegmentStarted(segment)]

    def _present(self, say: Say) -> list[Cue]:
        """A dater's turn, unless they're away after the moderator resumed without them.
        While the other dater is away, the turn carries away, so its line, written before
        the date for both daters, is never said if it names the one who's gone."""
        if say.about is None:
            return [say]
        if say.about in self._absent:
            return []
        if say.about.other in self._absent:
            return [replace(say, away=say.about.other)]
        return [say]

    def _prompt(self, seat: Seat) -> Say:
        return running_order.first_question(self._pair, seat)

    def _audience_question(self, at: float) -> list[Cue]:
        """The next approved question the screen lets through. One it catches is skipped
        without a word, since a spoken decline would tell the room a listener asked
        something off limits: the console gets a code, and the next in the queue takes the
        slot."""
        cues: list[Cue] = []
        if len(self._absent) == len(Seat):  # nobody on air to ask: it keeps for later
            return cues
        away = next(iter(self._absent), None)  # the one dater away, if one is
        while self._questions and self._questions_asked < running_order.AUDIENCE_QUESTIONS:
            question = self._questions.popleft()
            reasons = screen_question(question)
            if reasons:
                cues.append(QuestionSkipped(reasons[0]))
                continue
            self._questions_asked += 1
            self._quiet_since = at
            cues.append(Say(Moment.FROM_THE_ROOM, detail=question, away=away))
            break
        return cues

    def _time(self, at: float) -> list[Cue]:
        self._desk.open_window()
        self._window_open = True
        self._poll_open = True
        return [
            PlaySound(Sound.TIMES_UP),
            *self._enter(Segment.TIME, at),
            DecisionWindowOpened(seconds=self._timing.decision_window),
            PollOpened(script.POLL_QUESTION),
            Say(Moment.TIME, text=script.time_line(self._pair)),
            self._room_talk(Situation.DECIDING),
        ]

    def _result(self, at: float) -> list[Cue]:
        """Always at the end of the window, even if both decided early, so the timing of
        the result never hints at who chose what or whether someone didn't answer. The
        room's verdict comes only now, after both decisions are locked, and only if enough
        people voted."""
        self._desk.close_window()
        self._window_open = False
        outcome = self._desk.resolve()
        cues = [
            *self._close_poll(),
            *self._enter(Segment.RESULT, at),
            Say(Moment.RESULT, text=script.result_line(outcome)),
        ]
        try:
            tally = self._poll.tally()
        except Exception:  # the verdict is optional: the result and the rest still go out
            tally = PollTally(0, 0)
        if tally.total >= MIN_VOTES_FOR_VERDICT:
            cues.append(ShowVerdict(yes_share=rounded_share(tally.yes, tally.total)))
        # Written before the date, so it can't know or hint at the outcome.
        cues.append(Say(Moment.OUTRO))
        if self._invite:
            cues.append(Say(Moment.INVITATION, text=script.INVITATION))
        cues += self._end_date(EndReason.COMPLETED, outcome)
        self._schedule(at + self._timing.next_up_after_result, "next_up")
        return cues

    def _next_up(self, at: float, *, after_a_leave: bool = False) -> list[Cue]:
        cues: list[Cue] = []
        if self._next_pair is not None:
            cues += self._enter(Segment.NEXT_UP, at)
            cues.append(running_order.next_up(self._next_pair))
        elif after_a_leave:
            cues.append(PlayFallbackSegment())  # the date ended early, and nobody's next
        # Finished once Next up is out: it may wait for a dater to finish their sentence.
        self._finish_when_said = True
        return cues

    def _wait_for_speech(self, now: float, cues: list[Cue]) -> list[Cue]:
        """A beat's lines and sounds wait while a dater is speaking (or behind others that
        already wait, so they keep their order), for up to timing.talk_over_grace. State
        changes go on time: segments, the decision window, the poll, the meter, the desk.
        Safety lines and the result never wait (NEVER_HELD)."""
        if not self._speaking and not self._waiting:
            return cues
        on_time: list[Cue] = []
        for cue in cues:
            if waits_for_speech(cue):
                if self._waiting_until is None:
                    self._waiting_until = now + self._timing.talk_over_grace
                self._waiting.append(cue)
            else:
                on_time.append(cue)
        return on_time

    def _done_waiting(self, now: float) -> list[Cue]:
        """What waited goes out once nobody is speaking, or once the grace is over."""
        if not self._waiting:
            return []
        if self._speaking and self._waiting_until is not None and now < self._waiting_until:
            return []
        cues, self._waiting, self._waiting_until = self._waiting, [], None
        if self._finish_when_said:
            cues += self._finish()
        return cues

    def _silence_due(self, now: float) -> float:
        if not self._live or self.segment not in SILENCE_RULE or self._speaking:
            return math.inf
        if self.conversational and self.floor.waiting(now):
            return math.inf
        due = self._quiet_since + self._timing.silence_after
        if self._last_nudge is not None:
            due = max(due, self._last_nudge + self._timing.silence_cooldown)
        return due

    def _nudge(self, at: float) -> list[Cue]:
        """An open prompt to both daters: nobody is singled out for being quiet. With one
        away after the moderator resumed without them, to the one who's here."""
        self._last_nudge = at
        if not self._absent:
            return [Say(Moment.SILENCE)]
        return [Say(Moment.SILENCE, about=seat) for seat in Seat if seat not in self._absent]

    def _cover(self, now: float) -> list[Cue]:
        """While the show is held: a line to the room every timing.cover_every seconds, up
        to timing.cover_lines in all (the first came with the pause); the alert at
        timing.moment_alert_after; and the fallback segment once the lines run out, or at
        the alert, whichever is first."""
        if self._held_since is None:
            return []
        t, held = self._timing, self._held_since
        alert_at = held + t.moment_alert_after
        cues: list[Cue] = []
        while self._cover_lines < t.cover_lines:
            due = held + self._cover_lines * t.cover_every
            if due > now or due >= alert_at:
                break
            self._cover_lines += 1
            cues.append(self._cover_line())
        if not self._overdue and alert_at <= now:
            cues += self._not_back_in_time()
        segment_at = min(held + t.cover_lines * t.cover_every, alert_at)
        if not self._segment_playing and segment_at <= now:
            self._segment_playing = True
            cues.append(PlayFallbackSegment())
        return cues

    def _not_back_in_time(self) -> list[Cue]:
        """Said once. The show stays on hold until the moderator keeps waiting, resumes or
        ends the date, or the dater comes back."""
        self._overdue = True
        alert: list[Cue] = []
        if self._away is not None:
            alert.append(AlertModerator(ModeratorAlert.PAUSE_NOT_BACK, self._away))
        elif self._show_paused_by is not None:  # only the moderator can lift it: remind them
            alert.append(AlertModerator(ModeratorAlert.SHOW_HELD, self._show_paused_by))
        return [*alert, Say(Moment.BEAR_WITH_US, text=script.BEAR_WITH_US)]

    def _clear_pause(self, now: float) -> list[Cue]:
        """Ends any pause and restarts the show clock; silence counts from now. Stops the
        fallback segment first if it was covering, so it never plays under Nova."""
        stop: list[Cue] = [StopFallbackSegment()] if self._segment_playing else []
        if self._held_since is not None:
            duration = now - self._held_since
            self._held += duration
            if self.conversational:
                if self.floor.since is not None:
                    self.floor.since += duration
                if self._turn_done_at is not None:
                    self._turn_done_at += duration
                if self._transition_since is not None:  # nobody's talk held it back meanwhile
                    self._transition_held += duration
            self._held_since = None
            self._quiet_since = self._show_time(now)
        self._away = None
        self._overdue = False
        self._report_hold, self._reporter = False, None
        self._welcome_due = False
        self._cover_lines, self._segment_playing = 0, False
        self._show_paused_by = None
        return stop

    def _close_poll(self) -> list[Cue]:
        if not self._poll_open:
            return []
        self._poll_open = False
        return [PollClosed()]

    def _close_meter(self) -> list[Cue]:
        if not self._meter_open:
            return []
        self._meter_open = False
        return [LightningMeterClosed()]

    def _end_date(self, reason: EndReason, outcome: Outcome | None) -> list[Cue]:
        self.floor.clear()
        self._forget_transition()
        self.end_reason = reason
        self.outcome = outcome
        self.segment = None
        self._waiting, self._waiting_until = [], None  # never said after the date ends
        if self._window_open:  # ended early, while deciding: no decision is taken after it
            self._window_open = False
            self._desk.close_window()
        return [*self._close_meter(), DateEnded(reason, outcome)]

    def _finish(self) -> list[Cue]:
        self.finished = True
        self._beats.clear()
        self._forget_transition()
        self._waiting, self._waiting_until = [], None  # nothing is said after Finished
        self._finish_when_said = False
        self._held_since, self._away, self._overdue = None, None, False
        self._report_hold, self._reporter, self._wanting = False, None, None
        self._welcome_due = False
        self._absent, self._absent_reported = set(), set()
        self._cover_lines, self._segment_playing = 0, False
        self._show_paused_by = None
        return [Finished()]


def rounded_share(yes: int, total: int) -> float:
    """The yes share to the nearest 10%, halves up: 78% shows as 80%, 75% as 80%. Whole
    numbers, so no float error rounds a half the wrong way."""
    steps = (20 * yes + total) // (2 * total)  # tenths, rounded half up
    return round(steps * VERDICT_STEP, 1)


def _is_turn(cue: Cue) -> bool:
    return isinstance(cue, Say) and cue.moment in TURN_MOMENTS


def waits_for_speech(cue: Cue) -> bool:
    """Lines and sounds wait for a dater to finish; NEVER_HELD lines and everything else go
    on time. Whoever plays the cues asks again before each one (nova/rehearsal.py): a beat's
    lines go out together, and a dater may start speaking while the first plays."""
    if isinstance(cue, PlaySound | PlayVoiceIntro):
        return True
    return isinstance(cue, Say) and cue.moment not in NEVER_HELD
