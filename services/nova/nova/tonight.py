"""What Nova has said tonight, across every date, so it doesn't keep saying the same thing.

One Tonight is shared by every date's line writer. Lines are kept per moment with the
daters' first names masked, so lines from different dates compare fairly and no earlier
dater's name ever reaches a later date's prompt. Lines that carry what a listener or dater
said (audience questions, replies) are never kept (nova/lines.py, REMEMBERED_LIVE).
"""

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

from nova.cues import Moment

NAME = "[name]"  # stands in for a dater's first name
SHOWN = 24  # lines per moment shown to the model: about three a date for eight dates


def mask(text: str, names: Iterable[str]) -> str:
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", NAME, text)
    return text


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def similarity(a: str, b: str) -> float:
    """Word-level similarity from 0 (nothing alike) to 1 (the same words in order)."""
    return SequenceMatcher(None, words(a), words(b), autojunk=False).ratio()


class Tonight:
    def __init__(self) -> None:
        self._lines: dict[Moment, list[str]] = {}

    def remember(self, moment: Moment, text: str, *, names: Iterable[str]) -> None:
        """A line the room heard (or will hear: set pieces are remembered when chosen)."""
        self._lines.setdefault(moment, []).append(mask(text, names))

    def said(self, moment: Moment) -> list[str]:
        """The most recent lines for this moment, oldest first, names masked."""
        return self._lines.get(moment, [])[-SHOWN:]

    def closeness(self, moment: Moment, text: str, *, names: Iterable[str]) -> float:
        """How close a line is to the most similar line said tonight for this moment."""
        masked = mask(text, names)
        return max((similarity(masked, line) for line in self._lines.get(moment, [])), default=0.0)
