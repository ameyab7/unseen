"""The private decision at the end of a date.

Nova never sees who chose what. The show runner only opens the window, closes it, and
asks for the outcome: mutual, or not. In production a Postgres function (resolve_date)
answers; InMemoryDecisionDesk is the stand-in for rehearsals and tests, with the same rule.
"""

from enum import StrEnum
from typing import Protocol

from nova.cards import Seat


class Choice(StrEnum):
    REVEAL = "reveal"
    UNSEEN = "unseen"


class Outcome(StrEnum):
    MUTUAL = "mutual"
    NOT_MUTUAL = "not_mutual"


class DecisionError(RuntimeError):
    pass


class DecisionDesk(Protocol):
    def open_window(self) -> None: ...
    def close_window(self) -> None: ...
    def resolve(self) -> Outcome: ...


class InMemoryDecisionDesk:
    """Mutual only if both chose reveal. No answer counts as keep it unseen."""

    def __init__(self) -> None:
        self._choices: dict[Seat, Choice] = {}
        self._open = False
        self._closed = False
        self.resolved = False

    def open_window(self) -> None:
        self._open = True

    def close_window(self) -> None:
        self._open = False
        self._closed = True

    def choose(self, seat: Seat, choice: Choice) -> None:
        if not self._open:
            raise DecisionError("The decision window is not open")
        if seat in self._choices:
            raise DecisionError(f"The {seat} dater has already decided")
        self._choices[seat] = choice

    def resolve(self) -> Outcome:
        if not self._closed:
            raise DecisionError("The decision window is still open")
        self.resolved = True
        both_reveal = all(self._choices.get(seat) is Choice.REVEAL for seat in Seat)
        return Outcome.MUTUAL if both_reveal else Outcome.NOT_MUTUAL
