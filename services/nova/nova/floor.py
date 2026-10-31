"""Local, conservative floor signals, independent of safety and model inference.

These rules recognize explicit conversational moves, not whether an answer is good
enough. Unrecognized speech uses a quiet gap; an explicit hold or unanswered handover
gets a longer, bounded opportunity; a closing phrase is only a hint, which shortens a due
transition's gap (decision 0012). Speech answers a question only if the voice detector
timed it inside the question's answer window, which opens when the room has heard the
question; speech no final has said yet holds the floor for a bounded time, since it may
be "let me think". No transcript is retained in the state or logged: only seats, moves
and times.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from nova.cards import Seat
from nova.text import plain


class Move(StrEnum):
    UNKNOWN = "unknown"
    HOLD = "hold"
    PASS = "pass"
    OTHER = "other"
    NOVA = "nova"


class Hold(StrEnum):
    """Why the floor holds Nova back."""

    CLARIFICATION = "clarification"  # a dater asked Nova something, and is owed the answer
    HANDOVER = "handover"  # a dater handed the floor to the other, who hasn't taken it
    THINKING = "thinking"  # "Let me think.", "hmm", a sentence left hanging on "because"
    UNHEARD = "unheard"  # speech no final has said yet: it may be any of these
    ANSWER = "answer"  # a dater Nova asked hasn't answered yet


# A final arrives a moment after the voice detector hears its words end, so it belongs to
# whoever stopped within LATE_FINAL seconds. It can also arrive just before the detector
# reports the stop, which comes only after a stretch of silence: up to FINAL_LEAD before
# it, a final describes how the speech ended. Assumptions, which rehearsals measure.
LATE_FINAL = 2.0
FINAL_LEAD = 1.0


_HOLD = re.compile(
    r"(?:\b(?:let me think|give me (?:a|one) (?:second|moment)|"
    r"hang on|hold on|i(?:'m| am) thinking|there(?:'s| is) more)\b[^.!?]*[.!?]*$"
    r"|^(?:h+m+|u+m+|u+h+)[.!?, ]*$|\b(?:and|because|but|so|or)\s*[,.…]*$)",
    re.IGNORECASE,
)
_PASS = re.compile(
    r"^(?:(?:okay|well|um|uh)[, ]+)*(?:i(?:'ll| will)?\s+pass|pass|"
    r"i(?:'d| would) rather (?:not|skip)|skip (?:this|that)(?: one)?|"
    r"that(?:'s| is) (?:it|all))"
    r"[.!?, ]*$",
    re.IGNORECASE,
)
# The utterance's last sentence is a closing phrase, at most after a filler. Not "anyway"
# or "so yeah" alone, and not a phrase trailing off ("...") or asked as a question.
_COMPLETION = re.compile(
    r"(?:^|[.!?…]\s+)(?:(?:yeah|yep|so|well|okay|ok|um|uh|and|but|i mean|i think|i guess)"
    r"[, ]+)*that(?:'s| is) (?:about it|pretty much it|all i(?:'ve| have)? got|all i have)"
    r"(?: for me)?[.!]?$",
    re.IGNORECASE,
)


def possible_completion(words: str) -> bool:
    """The utterance ends on a closing phrase ("That's about it for me."): possibly, never
    certainly, the end of the thought. A phrase with more words after it doesn't count."""
    return _COMPLETION.search(plain(words).strip()) is not None


def move(words: str, *, other_name: str, to_nova: bool = False) -> Move:
    """Explicit hints only. A question to Nova can also contain an answer or a pass."""
    text = plain(words).strip()
    if to_nova:
        return Move.NOVA
    name = re.escape(other_name)
    if re.search(rf"\b(?:you go first[, ]+{name}|{name}[, ]+you go first)\b", text, re.I):
        return Move.OTHER
    if _PASS.fullmatch(text):
        return Move.PASS
    # A named question or an explicit "what/how about you" hands the floor to the
    # other dater. A mentioned name alone is not an invitation.
    if ("?" in text and re.search(rf"\b{name}\b", text, re.I)) or re.search(
        r"\b(?:what|how) about you\b|\byour turn\b|\byou go first\b", text, re.I
    ):
        return Move.OTHER
    if _HOLD.search(text):
        return Move.HOLD
    return Move.UNKNOWN


@dataclass
class Floor:
    """One question's opportunity, with a revision invalidating obsolete host work."""

    wait: float = 20.0
    revision: int = 0
    expected: set[Seat] = field(default_factory=set)
    holder: Seat | None = None
    handover: Seat | None = None
    clarifying: Seat | None = None
    last_move: Move = Move.UNKNOWN
    since: float | None = None
    active: bool = False
    # When the room heard the question: from the first audio of the delivery that said it
    # to its end (or the cut, if the rest was rightly never said). None until it's been
    # said, and if it never was: until then, nobody owes it an answer.
    window: tuple[float, float] | None = None
    # Seats whose latest speech no final has said yet: None while they speak, else when
    # they stopped. Not a question's state: it outlasts begin().
    unheard: dict[Seat, float | None] = field(default_factory=dict)
    final_wait: float = LATE_FINAL
    # What each dater said, as the voice detector timed it (began, ended: None while going
    # on), heard before the question was said: it answers only if it falls in the window,
    # as speech over the question's end does, whose final came before Nova finished.
    early: dict[Seat, tuple[float | None, float | None]] = field(default_factory=dict)
    # The latest utterance ended on a closing phrase (possible_completion), heard while
    # nobody was speaking. Any new speech clears it.
    completion_hint: bool = False

    @property
    def posed(self) -> bool:
        """Nova has said the question: its answer window is open."""
        return self.window is not None

    def begin(self, seats: set[Seat]) -> None:
        self.clear()
        self.active = True
        self.expected = set(seats)

    def asked(
        self, now: float, *, heard_from: float | None = None, heard_until: float | None = None
    ) -> None:
        """The room heard the question, from heard_from to heard_until (both now if not
        said): only speech that began after its first word and went on to its end, or
        began after it, answers it."""
        self.since = now
        start = heard_from if heard_from is not None else now
        self.window = (start, heard_until if heard_until is not None else now)
        for seat, (began, ended) in self.early.items():
            if self._answers(began, ended):
                self.expected.discard(seat)
        self.early.clear()

    def withdrawn(self) -> None:
        """The question was never said, or not all of it (dropped, stopped for a pause,
        superseded): nobody owes it an answer, so nothing waits for one."""
        self.expected.clear()
        self.window = None
        self.early.clear()

    def started(self, now: float, seat: Seat) -> None:
        self.revision += 1
        self.holder, self.since = seat, now
        self.unheard[seat] = None
        self.completion_hint = False
        if self.last_move is Move.PASS:
            self.last_move = Move.UNKNOWN  # they're speaking again: no longer passing
        # Starting to speak isn't a contribution yet: what is said decides (heard), so the
        # other dater's laugh never cancels a clarification Nova still owes, or a hold.

    def stopped(self, now: float, seat: Seat, *, transcribed: bool = False) -> None:
        """transcribed: a final just before the stop already said how the speech ended."""
        if seat in self.unheard:
            if transcribed:
                del self.unheard[seat]
            else:
                self.unheard[seat] = now
        if seat in self.early and self.early[seat][1] is None:
            self.early[seat] = (self.early[seat][0], now)

    def heard(
        self,
        now: float,
        seat: Seat,
        signal: Move,
        *,
        spoke: tuple[float | None, float | None] | None = None,
        speaking: bool = False,
        completion: bool = False,
    ) -> None:
        """spoke: when the speech this final says began, and ended (None while it goes on),
        from the voice detector; never when the final arrived. Without it (a bare floor, in
        a test), now. speaking: the dater is still going, so more is to come. completion:
        the words end on a closing phrase, and nobody has spoken since."""
        began, ended = spoke if spoke is not None else (now, now)
        self.revision += 1
        self.holder, self.since, self.last_move = seat, now, signal
        self.completion_hint = completion and signal is Move.UNKNOWN
        if speaking:
            self.unheard[seat] = None
        else:
            self.unheard.pop(seat, None)
        if not self.active:
            return
        if signal is Move.NOVA:
            self.clarifying = seat
            return
        if self.clarifying is seat:  # only the dater who asked moves on from it
            self.clarifying = None
        if signal is not Move.HOLD:
            if not self.posed:
                self.early[seat] = (began, ended)  # judged once the question is said
            elif self._answers(began, ended):
                self.expected.discard(seat)
        if self.handover is seat:
            self.handover = None
        if signal is Move.OTHER:
            self.handover = seat.other

    def _answers(self, began: float | None, ended: float | None) -> bool:
        """Speech answers the question if the room had heard it begin, and it went on to the
        question's end or began after it. Before, or with no start known, it's the daters'
        own conversation, however late its final arrives."""
        if self.window is None or began is None:
            return False
        start, end = self.window
        return began >= start and (ended is None or ended >= end)

    def replied(self, now: float, seat: Seat) -> None:
        if self.clarifying is seat:
            self.clarifying = None
            self.expected.add(seat)
            self.last_move = Move.HOLD
            self.since = now

    def holds(self, now: float) -> Hold | None:
        """What holds Nova back now, if anything: each for at most `wait` after the floor
        last changed; speech no final has said for at most final_wait after it stopped.
        A silence alone never ends a hold, a handover or a clarification."""
        if not self.active or self.since is None or now >= self.since + self.wait:
            return None
        if self.clarifying is not None:
            return Hold.CLARIFICATION
        if self.handover is not None:
            return Hold.HANDOVER
        if self.last_move is Move.HOLD:
            return Hold.THINKING
        if any(at is None or now < at + self.final_wait for at in self.unheard.values()):
            return Hold.UNHEARD
        if self.posed and self.expected:
            return Hold.ANSWER
        return None

    def waiting(self, now: float) -> bool:
        return self.holds(now) is not None

    def clear(self) -> None:
        self.revision += 1
        self.expected.clear()
        self.holder = self.handover = self.clarifying = None
        self.last_move = Move.UNKNOWN
        self.since = None
        self.window = None
        self.early.clear()
        self.active = self.completion_hint = False
