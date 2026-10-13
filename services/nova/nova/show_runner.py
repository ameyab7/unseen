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
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from nova import script
from nova.cards import Pair, Seat
from nova.cues import (
    Cue,
    DateEnded,
    DecisionWindowOpened,
    EndReason,
    Finished,
    Moment,
    PlaySound,
    PlayVoiceIntro,
    PollClosed,
    PollOpened,
    Say,
    Segment,
    SegmentStarted,
    ShowVerdict,
    Sound,
    StopSpeaking,
)
from nova.decisions import DecisionDesk, Outcome
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

LIGHTNING_QUESTIONS = 3
AUDIENCE_QUESTIONS = 2


class RoomPoll(Protocol):
    def yes_share(self) -> float | None:
        """Share of the room voting yes to "Second date?", or None if nobody voted."""
        ...


@dataclass(order=True)
class _Beat:
    at: float  # show time
    order: int
    action: str = field(compare=False)
    arg: object = field(default=None, compare=False)


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
    ) -> None:
        self._pair = pair
        self._desk = desk
        self._poll = poll
        self._timing = timing
        self._next_pair = next_pair
        self._invite = not invitation_given_tonight

        self._beats: list[_Beat] = []
        self._order = itertools.count()
        self._started_at: float | None = None
        self._held = 0.0  # wall seconds the show clock has been stopped
        self._hold: tuple[Seat, float] | None = None  # who asked for a moment, and when
        self._poll_open = False
        self._lightning_unlocked = False
        self._lightning_decided = False
        self._questions: deque[str] = deque()
        self._questions_asked = 0
        self._quiet_since = 0.0  # show time of the last word from a dater
        self._last_nudge: float | None = None
        self._heard: dict[Seat, float] = {seat: -math.inf for seat in Seat}

        self.segment: Segment | None = None
        self.end_reason: EndReason | None = None
        self.outcome: Outcome | None = None
        self.finished = False

    # --- Driving the clock ----------------------------------------------------------------

    def start(self, now: float) -> list[Cue]:
        if self._started_at is not None:
            raise RuntimeError("This date has already started")
        self._started_at = now
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
                for n in range(1, LIGHTNING_QUESTIONS)
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

    # --- Things that happen in the room ---------------------------------------------------

    def speech(self, now: float, seat: Seat) -> list[Cue]:
        """A dater spoke (voice activity), which resets the silence clock."""
        cues = self._advance(now)
        if self._live and self._hold is None:
            shown = self._show_time(now)
            self._quiet_since = shown
            self._heard[seat] = shown
        return cues

    def need_a_moment(self, now: float, seat: Seat) -> list[Cue]:
        """Nova covers without naming anyone, and the show clock holds for up to
        timing.moment_hold, or until the same dater is back."""
        cues = self._advance(now)
        if self._live and self._hold is None and self.segment in ON_AIR:
            self._hold = (seat, now)
            cues += [Say(Moment.NEED_A_MOMENT, text=script.NEED_A_MOMENT), Say(Moment.ROOM_TALK)]
        return cues

    def back(self, now: float, seat: Seat) -> list[Cue]:
        cues = self._advance(now)
        if self._hold is not None and self._hold[0] is seat:
            cues += self._end_hold(now)
        return cues

    def dater_left(self, now: float, seat: Seat) -> list[Cue]:
        """Nova closes kindly, with no reason and no blame; the show moves to the next pair."""
        cues = self._advance(now)
        if not self._live:
            return cues
        self._release_hold(now)
        leaver, other = self._pair.card(seat), self._pair.card(seat.other)
        cues += [StopSpeaking(), Say(Moment.DATER_LEFT, text=script.dater_left(leaver, other))]
        cues += self._close_poll()
        cues += self._end_date(EndReason.DATER_LEFT, None)
        self._beats.clear()
        self._schedule(self._show_time(now) + self._timing.next_up_after_result, "next_up")
        return cues

    def moderator_end(self, now: float) -> list[Cue]:
        """Nova stops at once, says only the approved closing line, and says nothing after."""
        cues = self._advance(now)
        if self.finished:
            return cues
        self._release_hold(now)
        cues.append(StopSpeaking())
        if self.end_reason is None:
            cues.append(Say(Moment.MODERATOR_END, text=script.MODERATOR_END))
            cues += self._close_poll()
            cues += self._end_date(EndReason.MODERATOR_ENDED, None)
        return cues + self._finish()

    def unlock_lightning_round(self, now: float) -> list[Cue]:
        """The room's gestures filled the lightning round. Too late once its slot starts."""
        cues = self._advance(now)
        if self._live and not self._lightning_decided and not self._lightning_unlocked:
            self._lightning_unlocked = True
            cues.append(Say(Moment.LIGHTNING_UNLOCKED, text=script.LIGHTNING_UNLOCKED))
        return cues

    def queue_question(self, now: float, question: str) -> list[Cue]:
        """An audience question that has already passed screening."""
        cues = self._advance(now)
        if self._live:
            self._questions.append(question)
        return cues

    # --- Internals ------------------------------------------------------------------------

    @property
    def _live(self) -> bool:
        return self._started_at is not None and self.end_reason is None and not self.finished

    def _show_time(self, now: float) -> float:
        if self._started_at is None:
            raise RuntimeError("Call start() first")
        stopped_at = self._hold[1] if self._hold is not None else now
        return stopped_at - self._started_at - self._held

    def _schedule(self, at: float, action: str, arg: object = None) -> None:
        heapq.heappush(self._beats, _Beat(at, next(self._order), action, arg))

    def _advance(self, now: float) -> list[Cue]:
        """Runs every beat and silence prompt due by now, in show-time order."""
        self._show_time(now)  # raises if not started
        cues: list[Cue] = []
        while not self.finished:
            if self._hold is not None:
                ends = self._hold[1] + self._timing.moment_hold
                if ends > now:
                    break
                cues += self._end_hold(ends)
                continue
            shown = self._show_time(now)
            beat_at = self._beats[0].at if self._beats else math.inf
            silence_at = self._silence_due()
            if min(beat_at, silence_at) > shown:
                break
            if beat_at <= silence_at:
                cues += self._run(heapq.heappop(self._beats))
            else:
                cues += self._nudge(silence_at)
        return cues

    def _run(self, beat: _Beat) -> list[Cue]:
        at = beat.at
        match beat.action:
            case "doors":
                return [
                    PlaySound(Sound.ON_AIR),
                    *self._enter(Segment.DOORS, at),
                    Say(Moment.DOORS_INTRO),
                    PlayVoiceIntro(Seat.LEFT),
                    PlayVoiceIntro(Seat.RIGHT),
                ]
            case "first_question":
                return [*self._enter(Segment.FIRST_QUESTION, at), self._prompt(Seat.LEFT)]
            case "second_prompt":
                return [self._prompt(Seat.RIGHT)]
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
                if not self._lightning_unlocked:
                    return self._enter(Segment.FREE_TALK, at)
                return [*self._enter(Segment.LIGHTNING_ROUND, at), self._lightning_question(1)]
            case "lightning_question":
                if self.segment is not Segment.LIGHTNING_ROUND:
                    return []
                return [self._lightning_question(int(str(beat.arg)))]
            case "last_word":
                return [*self._enter(Segment.LAST_WORD, at), Say(Moment.LAST_WORD, about=Seat.LEFT)]
            case "second_last_word":
                return [Say(Moment.LAST_WORD, about=Seat.RIGHT)]
            case "time":
                return self._time(at)
            case "result":
                return self._result(at)
            case "next_up":
                return self._next_up(at)
        raise ValueError(f"Unknown beat {beat.action!r}")

    def _enter(self, segment: Segment, at: float) -> list[Cue]:
        self.segment = segment
        self._quiet_since = at
        return [SegmentStarted(segment)]

    def _prompt(self, seat: Seat) -> Say:
        card = self._pair.card(seat)
        return Say(Moment.FIRST_QUESTION, about=seat, detail=card.prompts[0].text)

    def _audience_question(self, at: float) -> list[Cue]:
        if not self._questions or self._questions_asked >= AUDIENCE_QUESTIONS:
            return []
        self._questions_asked += 1
        self._quiet_since = at
        return [Say(Moment.FROM_THE_ROOM, detail=self._questions.popleft())]

    def _lightning_question(self, n: int) -> Say:
        return Say(Moment.LIGHTNING_QUESTION, detail=f"{n} of {LIGHTNING_QUESTIONS}")

    def _time(self, at: float) -> list[Cue]:
        self._desk.open_window()
        self._poll_open = True
        return [
            PlaySound(Sound.TIMES_UP),
            *self._enter(Segment.TIME, at),
            DecisionWindowOpened(seconds=self._timing.decision_window),
            PollOpened(script.POLL_QUESTION),
            Say(Moment.TIME, text=script.time_line(self._pair)),
            Say(Moment.ROOM_TALK),
        ]

    def _result(self, at: float) -> list[Cue]:
        """Always at the end of the window, even if both decided early, so the timing of
        the result never hints at who chose what or whether someone didn't answer. The
        room's verdict comes only now, after both decisions are locked."""
        self._desk.close_window()
        outcome = self._desk.resolve()
        cues = [
            *self._close_poll(),
            *self._enter(Segment.RESULT, at),
            Say(Moment.RESULT, text=script.result_line(outcome)),
        ]
        share = self._poll.yes_share()
        if share is not None:
            cues.append(ShowVerdict(yes_share=share))
        if self._invite:
            cues.append(Say(Moment.INVITATION, text=script.INVITATION))
        cues += self._end_date(EndReason.COMPLETED, outcome)
        self._schedule(at + self._timing.next_up_after_result, "next_up")
        return cues

    def _next_up(self, at: float) -> list[Cue]:
        cues: list[Cue] = []
        if self._next_pair is not None:
            cues += self._enter(Segment.NEXT_UP, at)
            cues.append(Say(Moment.NEXT_UP, detail=self._next_pair.label))
        return cues + self._finish()

    def _silence_due(self) -> float:
        if not self._live or self.segment not in SILENCE_RULE:
            return math.inf
        due = self._quiet_since + self._timing.silence_after
        if self._last_nudge is not None:
            due = max(due, self._last_nudge + self._timing.silence_cooldown)
        return due

    def _nudge(self, at: float) -> list[Cue]:
        seat = min(Seat, key=lambda s: self._heard[s])  # the dater heard from least recently
        self._heard[seat] = at
        self._last_nudge = at
        return [Say(Moment.SILENCE, about=seat)]

    def _end_hold(self, at: float) -> list[Cue]:
        self._release_hold(at)
        self._quiet_since = self._show_time(at)  # silence counts from the hand-back
        return [Say(Moment.WELCOME_BACK, text=script.WELCOME_BACK)]

    def _release_hold(self, now: float) -> None:
        if self._hold is not None:
            self._held += now - self._hold[1]
            self._hold = None

    def _close_poll(self) -> list[Cue]:
        if not self._poll_open:
            return []
        self._poll_open = False
        return [PollClosed()]

    def _end_date(self, reason: EndReason, outcome: Outcome | None) -> list[Cue]:
        self.end_reason = reason
        self.outcome = outcome
        self.segment = None
        return [DateEnded(reason, outcome)]

    def _finish(self) -> list[Cue]:
        self.finished = True
        self._beats.clear()
        self._hold = None
        return [Finished()]
