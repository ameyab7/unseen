"""When Nova steps in although nobody has gone quiet (decision 0013): the conversation is
going wrong, not stopping.

Nova's only cue to step in used to be a long silence, so in the 2026-11-02 full-date
simulation it sat through two minutes of one dater firing heavy questions while the other
apologised more with every answer. A host changes gear. This module notices, from what each
dater's finished turns are like, never from what they said:

- EASY_WIN: one dater keeps putting themselves down or apologising (three turns in two
  minutes). Nova gives them an easy win: a light question about something on their own card.
- TURN_AROUND: one dater asks question after question (four or more in two minutes, at
  least two thirds of their turns) and the other asks at most one back. Nova playfully
  invites the asker to answer one themselves.
- BRING_IN: one dater has three quarters of the words over two minutes while the other
  only says "yeah". Nova brings the quiet one in, by name.
- MOVE_ON: a dater passes ("I'll pass") and the other comes straight back with a question.
  Nova moves the talk somewhere new for both, never back to what was passed on.
- LIGHTEN: a flat stretch: six short turns in a row, no questions, for 45 seconds or more,
  with no silence long enough for a silence prompt. A quick either-or for both.
- Talking over each other (three times in a minute): the brand book's "One at a time. I
  want both of those stories." (ShowRunner, with the name of whoever started first).

And when it stays out (the show runner adds the rest: free talk only, never in its first
minute, never within 45 s of a line of Nova's, never while anyone is speaking, the floor
holds or a beat is due within 20 s, never with a dater away, and never once Nova has more
than a fifth of the last three minutes' airtime):

- banter, a long story the other is into, a deep conversation both want: none of the
  signals fire while both ask and both answer;
- "sorry" as manners ("sorry, go on", "sorry, you first", "sorry?") is not a put-down;
- after anything the safety checks raised (distress yes or maybe, unchecked, any conduct
  flag), for SAFETY_QUIET: that's the moderator's, and Nova never papers over it;
- after a dater tells Nova to stay out ("Nova, let us talk"), for the rest of the date;
- at most MAX_STEERS a date, MIN_APART between them, and never the same kind twice running;
- a dater who just passed is never the one asked: EASY_WIN becomes LIGHTEN for both.

The signals are English word patterns: another language never fires them, so Nova stays
out. Nothing here keeps or logs what anyone said: each turn is four numbers and flags.
"""

import re
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from nova.cards import Seat
from nova.text import straight_quotes

WINDOW = 120.0  # seconds of turns the signals look at
SAFETY_QUIET = 60.0  # after a safety check raised something, Nova doesn't step in for this long
MAX_STEERS = 2  # a date
MIN_APART = 120.0  # between two
OVERLAPS = 3  # talking over each other this many times...
OVERLAP_WINDOW = 60.0  # ...in this long
TURNS_KEPT = 24


class Steer(StrEnum):
    EASY_WIN = "easy_win"
    TURN_AROUND = "turn_around"
    BRING_IN = "bring_in"
    MOVE_ON = "move_on"
    LIGHTEN = "lighten"


# A dater putting themselves or their answer down, or apologising for it. Not "sorry" as
# manners or as asking again.
SELF_DOWN = re.compile(
    r"\bsorry\b(?![,.]?\s*(?:go on|go ahead|you first|after you|what|i missed|can you|could"
    r" you|did you|say that|\?))(?!\?)"
    r"|\b(?:i'm|i am) (?:so |really |just |honestly |a bit |kind of |such )?(?:bad|terrible"
    r"|useless|awful|rubbish|hopeless|boring|dull|the worst|a disaster|an idiot)\b"
    r"|\b(?:boring|basic|predictable|terrible|rubbish|lame|uninspired|shallow|vague|stupid"
    r"|silly|bad) (?:answer|choice|pick|joke)\b"
    r"|\bi'm rambling\b|\bignore me\b|\bgo blank\b|\bmy social skills\b",
    re.IGNORECASE,
)
PASSED = re.compile(
    r"\b(?:i'll pass|i pass|pass on that|i'd rather not|rather not say|rather keep \w+(?: \w+)?"
    r" out of it|let's not|can we skip|skip that one)\b",
    re.IGNORECASE,
)
# Said to Nova: leave us to it. Steering is off for the rest of the date.
STAY_OUT = re.compile(
    r"\b(?:let us talk|let's just talk|let us chat|stay out of (?:it|this)|leave us to it"
    r"|we're fine|we've got this|we got this|you can relax)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Turn:
    at: float
    seat: Seat
    words: int
    question: bool
    self_down: bool
    passed: bool


class Steering:
    """One date's signals for stepping in, on the show clock."""

    def __init__(self) -> None:
        self._turns: deque[_Turn] = deque(maxlen=TURNS_KEPT)
        self._overlaps: deque[tuple[float, Seat]] = deque(maxlen=OVERLAPS)
        self._steers: list[tuple[float, Steer]] = []
        self._one_at_a_time: float | None = None
        self._safety_at: float | None = None
        self.off = False  # a dater asked Nova to stay out

    def heard(self, at: float, seat: Seat, words: str, *, to_nova: bool = False) -> None:
        """A dater's finished turn: kept as numbers and flags only."""
        text = straight_quotes(words)
        if to_nova:
            if STAY_OUT.search(text):
                self.off = True
            return  # words to Nova aren't the daters' conversation
        self._turns.append(
            _Turn(
                at,
                seat,
                len(text.split()),
                "?" in text,
                SELF_DOWN.search(text) is not None,
                PASSED.search(text) is not None,
            )
        )

    def overlap(self, at: float, first: Seat) -> None:
        """Both daters talking at once; first: who was speaking already."""
        self._overlaps.append((at, first))

    def safety(self, at: float) -> None:
        """A safety check raised something: the moderator's, so Nova stays out a while."""
        self._safety_at = at

    def stepped_in(self, at: float, steer: Steer) -> None:
        self._steers.append((at, steer))

    def said_one_at_a_time(self, at: float) -> None:
        self._one_at_a_time = at
        self._overlaps.clear()

    def talking_over(self, at: float) -> Seat | None:
        """Whoever started first, if they've talked over each other OVERLAPS times in
        OVERLAP_WINDOW: the brand book's "One at a time." Once a date."""
        if self.off or self._one_at_a_time is not None or len(self._overlaps) < OVERLAPS:
            return None
        if at - self._overlaps[0][0] > OVERLAP_WINDOW:
            return None
        return self._overlaps[-1][1]

    def due(self, at: float) -> tuple[Steer, Seat | None] | None:
        """The step-in the conversation calls for now, and who it's for (None: both)."""
        if self.off or len(self._steers) >= MAX_STEERS:
            return None
        if self._steers and at - self._steers[-1][0] < MIN_APART:
            return None
        if self._safety_at is not None and at - self._safety_at < SAFETY_QUIET:
            return None
        since = self._steers[-1][0] if self._steers else -float("inf")
        turns = [t for t in self._turns if at - t.at <= WINDOW and t.at > since]
        last = self._steers[-1][1] if self._steers else None
        for found in (
            self._easy_win(turns),
            self._turn_around(turns),
            self._bring_in(turns),
            self._move_on(turns, at),
            self._lighten(turns),
        ):
            if found is not None and found[0] is not last:
                return found
        return None

    # --- The signals ---------------------------------------------------------------------

    @staticmethod
    def _easy_win(turns: list[_Turn]) -> tuple[Steer, Seat | None] | None:
        for seat in Seat:
            downs = [t for t in turns if t.seat is seat and t.self_down]
            if len(downs) >= 3:
                mine = [t for t in turns if t.seat is seat]
                if mine and mine[-1].passed:  # never ask the one who just passed
                    return Steer.LIGHTEN, None
                return Steer.EASY_WIN, seat
        return None

    @staticmethod
    def _turn_around(turns: list[_Turn]) -> tuple[Steer, Seat | None] | None:
        for seat in Seat:
            mine = [t for t in turns if t.seat is seat]
            theirs = [t for t in turns if t.seat is seat.other]
            asked = sum(t.question for t in mine)
            asking = asked >= 4 and asked >= 2 * len(mine) / 3
            if asking and len(theirs) >= 3 and sum(t.question for t in theirs) <= 1:
                return Steer.TURN_AROUND, seat
        return None

    @staticmethod
    def _bring_in(turns: list[_Turn]) -> tuple[Steer, Seat | None] | None:
        total = sum(t.words for t in turns)
        if total < 150:
            return None
        for seat in Seat:
            held = sum(t.words for t in turns if t.seat is seat)
            quiet = [t for t in turns if t.seat is seat.other]
            if held / total >= 0.75 and all(t.words <= 6 and not t.question for t in quiet):
                return Steer.BRING_IN, seat.other
        return None

    @staticmethod
    def _move_on(turns: list[_Turn], at: float) -> tuple[Steer, Seat | None] | None:
        if len(turns) < 2:
            return None
        passed, then = turns[-2], turns[-1]
        pressed = then.seat is passed.seat.other and then.question
        if passed.passed and pressed and at - passed.at <= 30.0:
            return Steer.MOVE_ON, None
        return None

    @staticmethod
    def _lighten(turns: list[_Turn]) -> tuple[Steer, Seat | None] | None:
        recent = turns[-6:]
        if len(recent) < 6 or {t.seat for t in recent} != set(Seat):
            return None
        if any(t.words > 7 or t.question for t in recent):
            return None
        if recent[-1].at - recent[0].at < 45.0:
            return None
        return Steer.LIGHTEN, None
