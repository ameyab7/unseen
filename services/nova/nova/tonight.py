"""What Nova has said tonight, across every date, so it doesn't keep saying the same thing.

One Tonight is shared by every date's line writer. Lines are kept per moment with the
daters' first names masked, so lines from different dates compare fairly and no earlier
dater's name ever reaches a later date's prompt. Lines that carry what a listener or dater
said (audience questions, replies) are never kept (nova/lines.py, REMEMBERED_LIVE).

Set pieces are written before their date, so they're kept as planned: prompts see them
while the date runs, a planned line stays once it's spoken, and the ones the date never
spoke (it ended early) are dropped when it finishes, so lines the room never heard don't
steer later dates.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from nova.cues import Moment
from nova.text import mask_names

NAME = "[name]"  # stands in for a dater's first name
SHOWN = 24  # lines per moment shown to the model: about three a date for eight dates
# Live moments (a silence, talk to the room) show fewer, so a late-night prompt stays small
# and quick: the silence prompt grew from 777 characters on date 1 to 2,437 on date 8.
SHOWN_LIVE = 8
# Similarity at which two lines count as the same line said again (a fixed fallback).
SAME = 0.9


@dataclass(eq=False)
class Remembered:
    """One line in tonight's memory, names masked. planned: a set piece chosen for a date
    that hasn't been spoken yet."""

    text: str
    planned: bool = False


def mask(text: str, names: Iterable[str]) -> str:
    return mask_names(text, names, NAME)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def similarity(a: str, b: str) -> float:
    """Word-level similarity from 0 (nothing alike) to 1 (the same words in order)."""
    return SequenceMatcher(None, words(a), words(b), autojunk=False).ratio()


class Tonight:
    def __init__(self) -> None:
        self._lines: dict[Moment, list[Remembered]] = {}

    def remember(self, moment: Moment, text: str, *, names: Iterable[str]) -> None:
        """A line the room heard."""
        self._lines.setdefault(moment, []).append(Remembered(mask(text, names)))

    def plan(self, moment: Moment, text: str, *, names: Iterable[str]) -> Remembered:
        """A set piece chosen for a date. Prompts see it at once; heard() keeps it, and
        drop() removes it if the date finishes without it."""
        line = Remembered(mask(text, names), planned=True)
        self._lines.setdefault(moment, []).append(line)
        return line

    def heard(self, line: Remembered) -> None:
        """A planned line was spoken: it stays for the rest of the night."""
        line.planned = False

    def drop(self, lines: Iterable[Remembered]) -> None:
        """A date finished: those of its planned lines it never spoke are forgotten."""
        unspoken = {id(line) for line in lines if line.planned}
        for moment, kept in self._lines.items():
            self._lines[moment] = [line for line in kept if id(line) not in unspoken]

    def said(self, moment: Moment, *, limit: int = SHOWN) -> list[str]:
        """The most recent lines for this moment, oldest first, names masked."""
        return [line.text for line in self._lines.get(moment, [])[-limit:]]

    def uses(self, moment: Moment, text: str, *, names: Iterable[str]) -> tuple[int, int]:
        """How often this line has been said (or planned) tonight for the moment, near
        enough word for word, and where it last was among the moment's lines: -1 if never."""
        masked = mask(text, names)
        count, last = 0, -1
        for k, line in enumerate(self._lines.get(moment, [])):
            if similarity(masked, line.text) >= SAME:
                count, last = count + 1, k
        return count, last

    def closeness(self, moment: Moment, text: str, *, names: Iterable[str]) -> float:
        """How close a line is to the most similar line said tonight for this moment."""
        masked = mask(text, names)
        lines = self._lines.get(moment, [])
        return max((similarity(masked, line.text) for line in lines), default=0.0)
