"""What was said on this date, so a live reply or a silence prompt can build on it
(decision 0008): a callback to something a dater said a minute ago, or a link between the
two daters' answers, instead of reacting only to the last sentence.

Lifetime: one date. Each date of a show is opened by its Night (nova/show.py), which makes
one DateContext for it alone, the rehearsal's single date included. The on-air listener adds
a dater's turn once the distress and conduct check has cleared it (nova/on_air.py), the line
writer adds Nova's own lines as they play and reads a summary for a reply or a silence
prompt (nova/lines.py), and the date closes it when it ends, however it ends: cleared, and
nothing joins it after, not even a turn whose check finishes late. Tonight's memory
(nova/tonight.py) is a different thing, across dates and for repetition only: a line
written from this context is never kept there, so nothing a dater said reaches a later
date.

Privacy: it holds the minimum, in memory only, and is never logged, stored or sent
anywhere but the line model's prompt, inside the data block. A turn is kept only if it was
a whole utterance (not a fragment cut at the 10 s cap or acted on by the instant layer),
the check answered no distress and no conduct, conduct's instant layer heard no personal
detail in it, it holds at least MIN_WORDS words, and it isn't an echo of Nova's own voice.
Every kept turn is also passed to the guardrails, so no proper noun from it can reach the
room (nova/lines.py, _check).
"""

from collections import deque
from dataclasses import dataclass

from nova.cards import Seat
from nova.conduct import personal_detail
from nova.text import WORD, plain
from nova.tonight import similarity

TURNS = 8  # the daters' completed turns kept, both seats together
TURN_CHARS = 200  # each turn is cut, at a word, to at most this many characters
MIN_WORDS = 3  # shorter turns ("yeah", "ha, same") give nothing to build on
# Words that only acknowledge ("ha, so true", "mm, yeah, totally"): a turn of nothing else
# says the dater answered, and is otherwise forgotten, so it never takes the place of one
# that says something (before, "Mm, yeah, totally." pushed an answer out of the four shown).
ACKNOWLEDGING = frozenset((
    "yeah", "yes", "yep", "yup", "no", "nope", "nah", "totally", "okay", "ok", "sure", "right",
    "mm", "mhm", "mmm", "hmm", "ha", "haha", "hah", "heh", "wow", "oh", "ooh", "cool", "nice",
    "true", "exactly", "same", "really", "agreed", "definitely", "absolutely", "fair", "enough",
    "aw", "aww", "ah", "so", "that", "that's", "thats", "is", "it", "it's", "its", "lol",
    "indeed", "great", "good", "lovely", "amazing", "fun", "funny", "well", "i", "know", "see",
    "agree", "me", "too", "sounds", "love", "god", "omg", "sweet", "fair", "point", "yes,",
))  # fmt: skip
SHOWN_CHARS = 1000  # what a prompt sees of this date, all told: the oldest goes first
NOVA_LINES = 3  # Nova's own lines kept, so it doesn't repeat itself and can call back
SHOWN_TURNS = 4  # the most recent turns a prompt sees
SHOWN_QUESTIONS = 2  # unanswered questions a prompt sees
# A transcript this like a line Nova said is its own voice coming back through a dater's
# microphone (decision 0005), not something the dater said.
ECHO = 0.6


@dataclass(frozen=True)
class Turn:
    """One completed, checked dater turn. to_nova: it was said to Nova (on_air.addresses_nova),
    so a question in it is Nova's to answer, not the other dater's. An empty text: the dater
    spoke, but nothing they said is kept (a check held it back, or it was too short); it
    still answers the other dater's last question, so a question someone declined is never
    an open loop for Nova to bring back."""

    seat: Seat
    text: str
    to_nova: bool = False
    held: bool = False  # with no text: a check held it back
    n: int = 0  # when it was said, counting every turn either dater took


def _cut(text: str) -> str:
    """At most TURN_CHARS, cut at a word."""
    if len(text) <= TURN_CHARS:
        return text
    return text[:TURN_CHARS].rsplit(" ", 1)[0] + "..."


class DateContext:
    """This date's recent turns and Nova's own last lines, bounded, for one date only."""

    def __init__(self) -> None:
        self._turns: deque[Turn] = deque(maxlen=TURNS)
        self._nova: deque[str] = deque(maxlen=NOVA_LINES)
        self._count = 0  # every turn either dater took, kept or not
        self._last: dict[Seat, int] = {}  # when each dater last took one
        self.closed = False  # the date is over: nothing joins it again

    def __len__(self) -> int:
        """How many dater turns are kept, words and all: for the log, which never sees them."""
        return sum(bool(turn.text) for turn in self._turns)

    def heard(self, seat: Seat, words: str, *, to_nova: bool = False) -> bool:
        """A dater's completed turn that the check cleared. Kept unless it is too short to
        build on or holds a personal detail (then only that they spoke), or is Nova's own
        line echoed back (then nothing); True if kept."""
        text = plain(words)
        if self.closed or not text or any(similarity(text, line) >= ECHO for line in self._nova):
            return False
        if personal_detail(text) is not None:
            self.spoke(seat)
            return False
        if acknowledges(text):
            self.spoke(seat, held=False)
            return False
        self._turns.append(Turn(seat, _cut(text), to_nova, n=self._took(seat)))
        return True

    def spoke(self, seat: Seat, *, held: bool = True) -> None:
        """A dater spoke but nothing they said is kept: the checks held it back (distress,
        conduct, a request to stop, a calm boundary, a personal detail), kept as an empty
        turn, so a question answered only with it is never shown; or, with held False, it
        was too short or only acknowledged, and takes no place at all. Either way the other
        dater's question counts as answered."""
        if self.closed:
            return
        n = self._took(seat)
        if held:
            self._turns.append(Turn(seat, "", held=True, n=n))

    def _took(self, seat: Seat) -> int:
        self._count += 1
        self._last[seat] = self._count
        return self._count

    def said(self, line: str) -> None:
        """A line Nova wrote and the room heard (a set piece or a live reaction)."""
        text = plain(line)
        if text and not self.closed:
            self._nova.append(_cut(text))

    def spoken(self) -> list[str]:
        """Every dater turn kept: the guardrails block any proper noun from them."""
        return [turn.text for turn in self._turns if turn.text]

    def last_turn(self) -> Turn | None:
        """The turn just taken, if it was kept with its words: all an optional prefix may
        pick up (decision 0012). None if the latest was held back or too short to keep: a
        prefix about something said earlier would land out of nowhere."""
        if not self._turns:
            return None
        turn = self._turns[-1]
        return turn if turn.text and turn.n == self._count else None

    def said_by(self, seat: Seat) -> list[str]:
        """One dater's kept turns: their own evidence for anything Nova says both daters
        share (nova/grounding.py). For the check only, never shown to a prompt."""
        return [turn.text for turn in self._turns if turn.seat is seat and turn.text]

    def unanswered(self, *, away: Seat | None = None) -> list[Turn]:
        """Questions one dater asked the other that the other hasn't spoken since: open
        loops worth coming back to. None while a dater is away, who can't answer."""
        if away is not None:
            return []
        found = [
            turn
            for turn in self._turns
            if not turn.to_nova
            and turn.text.endswith("?")
            and self._last.get(turn.seat.other, 0) < turn.n
        ]
        return found[-SHOWN_QUESTIONS:]

    def summary(
        self, names: dict[Seat, str], *, away: Seat | None = None, answering: str = ""
    ) -> dict[str, object] | None:
        """What a reply or a silence prompt sees, as data: the last SHOWN_TURNS turns, each
        under the first name of the dater who said it; questions still unanswered; and
        Nova's own last lines. None if there's nothing. away: a dater the moderator resumed
        without, whose turns, and anything naming them, are left out. answering: the words
        a reply answers, already in its prompt, so they aren't shown twice."""
        absent = names[away] if away is not None else None
        answered = plain(answering)

        def shown(text: str) -> bool:
            return absent is None or absent.lower() not in {w.lower() for w in WORD.findall(text)}

        turns = [
            turn
            for turn in self._turns
            if turn.text
            and turn.seat is not away
            and shown(turn.text)
            and turn.text != _cut(answered)
            and turn not in self._declined()
        ]
        recent = turns[-SHOWN_TURNS:]
        questions = [turn for turn in self.unanswered(away=away) if turn not in recent]
        nova = [line for line in self._nova if shown(line)]
        # One budget for all of it, the oldest of each kind going first: Nova's own lines,
        # then the turns, then the questions (all under SHOWN_CHARS, whatever was said).
        for kept in (nova, recent, questions):
            while kept and _size(recent, questions, nova) > SHOWN_CHARS:
                kept.pop(0)
        if not recent and not questions and not nova:
            return None
        summary: dict[str, object] = {}
        if recent:
            summary["earlier"] = [
                {
                    "by": names[turn.seat],
                    **({"to": "Nova"} if turn.to_nova else {}),
                    "said": turn.text,
                }
                for turn in recent
            ]
        if questions:
            summary["unanswered"] = [
                {"asked_by": names[turn.seat], "question": turn.text} for turn in questions
            ]
        if nova:
            summary["you_said"] = nova
        return summary

    def _declined(self) -> list[Turn]:
        """Questions the other dater answered only with a turn the checks held back (a calm
        boundary, say): never shown, so Nova can't steer back to what was declined. Before,
        one run wrote "let's move away from the office talk" after a question about work."""
        turns = list(self._turns)
        declined = []
        for k, turn in enumerate(turns):
            if not turn.text.endswith("?") or turn.to_nova:
                continue
            reply = next((later for later in turns[k + 1 :] if later.seat is turn.seat.other), None)
            if reply is not None and reply.held:
                declined.append(turn)
        return declined

    def clear(self) -> None:
        """Lets go of everything: the date is over, or was discarded."""
        self._turns.clear()
        self._nova.clear()
        self._count = 0
        self._last.clear()

    def close(self) -> None:
        """The date is over, however it ended: everything goes, and nothing joins again."""
        self.clear()
        self.closed = True


def acknowledges(text: str) -> bool:
    """Too short to build on, or only acknowledging what was said ("ha, so true")."""
    words = [w.lower() for w in WORD.findall(text)]
    return len(words) < MIN_WORDS or all(w in ACKNOWLEDGING for w in words)


def _size(turns: list[Turn], questions: list[Turn], nova: list[str]) -> int:
    return sum(len(t.text) for t in (*turns, *questions)) + sum(len(line) for line in nova)
