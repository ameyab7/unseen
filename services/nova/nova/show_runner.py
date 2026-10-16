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
from dataclasses import dataclass, field
from typing import Protocol

from nova import script
from nova.cards import Pair, Seat
from nova.cues import (
    AlertModerator,
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
    Sound,
    StopSpeaking,
)
from nova.decisions import DecisionDesk, Outcome
from nova.distress import Verdict, is_unmistakable
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

LIGHTNING_QUESTIONS = 3
AUDIENCE_QUESTIONS = 2
# Below this, a percentage says too much about too few people, so the verdict stays hidden.
MIN_VOTES_FOR_VERDICT = 5


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
    ) -> None:
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
        self._report_hold = False  # the pause is for a report: only the moderator lifts it
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

    @property
    def started(self) -> bool:
        return self._started_at is not None

    # --- Things that happen in the room ---------------------------------------------------
    # Daters, the moderator and the room can do these at any time, so none of them raises
    # before start() or after the date. Moderator alerts never depend on the show clock.

    def speech(self, now: float, seat: Seat) -> list[Cue]:
        """A dater spoke (voice activity), which resets the silence clock."""
        cues = self._due(now)
        if self._live and self._held_since is None:
            self._quiet_since = self._show_time(now)
        return cues

    def need_a_moment(self, now: float, seat: Seat) -> list[Cue]:
        """Nova covers without naming anyone, and the show clock holds until the same dater
        is back. If they aren't back after timing.moment_alert_after, Nova asks the room to
        bear with us once, the moderator is alerted, and the show stays on hold until the
        moderator keeps waiting, resumes or ends the date. No limit on how often, but the
        moderator is alerted when the same dater pauses a second time."""
        cues = self._due(now)
        if self._can_pause:
            cues += self._pause(now, seat, script.NEED_A_MOMENT)
        return cues

    def back(self, now: float, seat: Seat) -> list[Cue]:
        """The dater who paused is back: the only time Nova says "And we're back.". Not
        after a report: the reporter's date ends in the app, and the moderator decides."""
        cues = self._due(now)
        if self._live and self._away is seat and not self._report_hold:
            self._clear_pause(now)
            cues.append(Say(Moment.WELCOME_BACK, text=script.WELCOME_BACK))
        return cues

    def moderator_keep_waiting(self, now: float) -> list[Cue]:
        """The show stays on hold for the dater; Nova says nothing more."""
        return self._due(now)

    def moderator_resume(self, now: float) -> list[Cue]:
        """The show carries on without the dater who paused. Nova says nothing: nobody is back."""
        cues = self._due(now)
        if self._live and self._away is not None:
            self._clear_pause(now)
        return cues

    def dater_left(self, now: float, seat: Seat) -> list[Cue]:
        """Nova closes kindly, with no reason and no blame; the show moves to the next pair."""
        cues = self._due(now)
        if not self._live:
            return cues
        self._clear_pause(now)
        leaver, other = self._pair.card(seat), self._pair.card(seat.other)
        cues += [StopSpeaking(), Say(Moment.DATER_LEFT, text=script.dater_left(leaver, other))]
        cues += self._close_poll()
        cues += self._end_date(EndReason.DATER_LEFT, None)
        self._beats.clear()
        self._schedule(self._show_time(now) + self._timing.next_up_after_result, "next_up")
        return cues

    def moderator_end(self, now: float) -> list[Cue]:
        """Nova stops at once and says only the approved closing line. The recorded fallback
        segment then plays until the moderator chooses what happens next. Nothing to end
        before the date has started."""
        cues = self._due(now)
        if self.finished or not self.started:
            return cues
        self._clear_pause(now)
        cues.append(StopSpeaking())
        if self.end_reason is None:
            cues.append(Say(Moment.MODERATOR_END, text=script.MODERATOR_END))
            cues += self._close_poll()
            cues += self._end_date(EndReason.MODERATOR_ENDED, None)
        cues.append(PlayFallbackSegment())
        return cues + self._finish()

    def unlock_lightning_round(self, now: float) -> list[Cue]:
        """The room's gestures filled the lightning round. Too late once its slot starts.
        Before the date, the unlock is kept and Nova announces it after the doors intro, so
        a paid unlock is never lost; the meter closes at once either way."""
        cues = self._due(now)
        if not self.started and not self._lightning_unlocked:
            self._lightning_unlocked = self._announce_unlock = True
            return cues + self._close_meter()
        if self._live and not self._lightning_decided and not self._lightning_unlocked:
            self._lightning_unlocked = True
            cues.append(Say(Moment.LIGHTNING_UNLOCKED, text=script.LIGHTNING_UNLOCKED))
            cues += self._close_meter()
        return cues

    def queue_question(self, now: float, question: AudienceQuestion) -> list[Cue]:
        """Only questions a moderator approved are ever read; anything else is dropped. One
        approved before the date starts is kept for it."""
        cues = self._due(now)
        if (self._live or not self.started) and question.approved:
            self._questions.append(question.text)
        return cues

    def dater_asked_nova(self, now: float, seat: Seat, words: str) -> list[Cue]:
        """A dater spoke to Nova on air. Their words are data for the reply, never
        instructions. Only while they're on air: not on a pause, not while deciding.

        A dater who unmistakably wants to stop (the instant layer, nova/distress.py) never
        gets a reply, so Nova can't deflect them: the moderator is alerted at once, whenever
        it happens, and if a pause can start, Nova stops mid-line, says the kind line and
        starts a need-a-moment pause for that dater; if not (another pause, the decision
        window), Nova stays quiet and the moderator acts. Anything else that needs a closer
        listen is checked for meaning outside the runner, and the verdict comes back through
        distress_check; whoever runs the show holds the reply until then."""
        cues = self._due(now)
        if is_unmistakable(words):
            return cues + self._wants_to_stop(now, seat)
        if self._live and self._held_since is None and self.segment in ON_AIR:
            cues.append(Say(Moment.REPLY, about=seat, detail=words))
        return cues

    def dater_said(self, now: float, seat: Seat, words: str) -> list[Cue]:
        """What a dater said on air that wasn't to Nova: to the other dater, or to nobody.
        Nova doesn't answer, but the instant layer hears it just as in dater_asked_nova,
        since a dater who wants to stop won't always say so to Nova."""
        cues = self._due(now)
        if is_unmistakable(words):
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

    def dater_reported(self, now: float, seat: Seat) -> list[Cue]:
        """A dater pressed Report. The prototype's report sheet: "Nova pauses the date and a
        live moderator listens in." The moderator is alerted first, at any time. If a pause
        can start, Nova stops mid-line and covers with the need-a-moment line and talk to
        the room, naming nobody and saying nothing about a report. The show clock holds for
        the reporter's seat until the moderator resumes or ends the date (the reporter's
        date ends in the app, so their own back() doesn't end it); the 30-second alert
        works as usual, and it never counts as a repeated pause. If a pause can't start,
        the alert is all."""
        cues = self._due(now)
        cues.append(AlertModerator(ModeratorAlert.DATER_REPORTED, seat))
        if self._can_pause:
            cues.append(StopSpeaking())
            cues += self._pause(now, seat, script.NEED_A_MOMENT, repeatable=False)
            self._report_hold = True
        return cues

    # --- Internals ------------------------------------------------------------------------

    @property
    def _live(self) -> bool:
        return self._started_at is not None and self.end_reason is None and not self.finished

    @property
    def _can_pause(self) -> bool:
        """On air, and nobody else on a pause."""
        return self._live and self._away is None and self.segment in ON_AIR

    def _pause(self, now: float, seat: Seat, line: str, *, repeatable: bool = True) -> list[Cue]:
        """Nova says the line and covers with talk to the room; the show clock holds until
        the same dater is back. repeatable: counts towards the repeated-pause alert."""
        self._away, self._held_since, self._overdue = seat, now, False
        cues: list[Cue] = [Say(Moment.NEED_A_MOMENT, text=line), self._room_talk()]
        if repeatable:
            self._pauses[seat] += 1
            if self._pauses[seat] > 1:
                cues.append(AlertModerator(ModeratorAlert.REPEATED_PAUSE, seat))
        return cues

    def _wants_to_stop(self, now: float, seat: Seat) -> list[Cue]:
        """The moderator is alerted, and if a pause can start, Nova stops mid-line, drops
        whatever it had queued, says the kind line at once and pauses for the dater."""
        cues: list[Cue] = [AlertModerator(ModeratorAlert.DATER_DISTRESSED, seat)]
        if self._can_pause:
            cues += [StopSpeaking(), *self._pause(now, seat, script.DISTRESS_PAUSE)]
        return cues

    def _room_talk(self) -> Say:
        """Nova talks to the room, working only from safe facts about the show."""
        if self.segment is None:
            raise RuntimeError("Nova only talks to the room during a segment")
        facts = RoomFacts(self.segment, self._poll_open, self._questions_asked)
        return Say(Moment.ROOM_TALK, room=facts)

    def _show_time(self, now: float) -> float:
        if self._started_at is None:
            raise RuntimeError("Call start() first")
        stopped_at = self._held_since if self._held_since is not None else now
        return stopped_at - self._started_at - self._held

    def _schedule(self, at: float, action: str, arg: object = None) -> None:
        heapq.heappush(self._beats, _Beat(at, next(self._order), action, arg))

    def _due(self, now: float) -> list[Cue]:
        """Everything due by now, once the date has started; nothing before."""
        return self._advance(now) if self.started else []

    def _advance(self, now: float) -> list[Cue]:
        """Runs every beat and silence prompt due by now, in show-time order."""
        self._show_time(now)  # raises if not started
        cues: list[Cue] = []
        while not self.finished:
            if self._held_since is not None:
                # The show clock is stopped: the only thing that can happen is the alert.
                due = self._held_since + self._timing.moment_alert_after
                if not self._overdue and due <= now:
                    cues += self._not_back_in_time()
                break
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
                return [*self._enter(Segment.FIRST_QUESTION, at), self._prompt(first)]
            case "second_prompt":
                return [self._prompt(second)]
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
                    self._lightning_question(1),
                ]
            case "lightning_question":
                if self.segment is not Segment.LIGHTNING_ROUND:
                    return []
                return [self._lightning_question(int(str(beat.arg)))]
            case "last_word":
                return [*self._enter(Segment.LAST_WORD, at), Say(Moment.LAST_WORD, about=first)]
            case "second_last_word":
                return [Say(Moment.LAST_WORD, about=second)]
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
        """The next approved question the screen lets through. One it catches is skipped
        without a word, since a spoken decline would tell the room a listener asked
        something off limits: the console gets a code, and the next in the queue takes the
        slot."""
        cues: list[Cue] = []
        while self._questions and self._questions_asked < AUDIENCE_QUESTIONS:
            question = self._questions.popleft()
            reasons = screen_question(question)
            if reasons:
                cues.append(QuestionSkipped(reasons[0]))
                continue
            self._questions_asked += 1
            self._quiet_since = at
            cues.append(Say(Moment.FROM_THE_ROOM, detail=question))
            break
        return cues

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
            self._room_talk(),
        ]

    def _result(self, at: float) -> list[Cue]:
        """Always at the end of the window, even if both decided early, so the timing of
        the result never hints at who chose what or whether someone didn't answer. The
        room's verdict comes only now, after both decisions are locked, and only if enough
        people voted."""
        self._desk.close_window()
        outcome = self._desk.resolve()
        cues = [
            *self._close_poll(),
            *self._enter(Segment.RESULT, at),
            Say(Moment.RESULT, text=script.result_line(outcome)),
        ]
        tally = self._poll.tally()
        if tally.total >= MIN_VOTES_FOR_VERDICT:
            cues.append(ShowVerdict(yes_share=tally.yes / tally.total))
        # Written before the date, so it can't know or hint at the outcome.
        cues.append(Say(Moment.OUTRO))
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
        """An open prompt to both daters: nobody is singled out for being quiet."""
        self._last_nudge = at
        return [Say(Moment.SILENCE)]

    def _not_back_in_time(self) -> list[Cue]:
        """Said once. The show stays on hold until the moderator keeps waiting, resumes or
        ends the date, or the dater comes back."""
        self._overdue = True
        alert: list[Cue] = []
        if self._away is not None:
            alert.append(AlertModerator(ModeratorAlert.PAUSE_NOT_BACK, self._away))
        return [*alert, Say(Moment.BEAR_WITH_US, text=script.BEAR_WITH_US)]

    def _clear_pause(self, now: float) -> None:
        """Ends any pause and restarts the show clock; silence counts from now."""
        if self._held_since is not None:
            self._held += now - self._held_since
            self._held_since = None
            self._quiet_since = self._show_time(now)
        self._away = None
        self._overdue = False
        self._report_hold = False

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
        self.end_reason = reason
        self.outcome = outcome
        self.segment = None
        return [*self._close_meter(), DateEnded(reason, outcome)]

    def _finish(self) -> list[Cue]:
        self.finished = True
        self._beats.clear()
        self._held_since, self._away, self._overdue = None, None, False
        self._report_hold = False
        return [Finished()]
