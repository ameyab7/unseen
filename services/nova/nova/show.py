"""A show's night: its dates, one at a time, each with a context of its own (decision 0011;
the context itself is decision 0008).

Every entrypoint that runs a date opens it through a Night, the LiveKit agent's rehearsal
included (one date a session), so the real show and the rehearsal keep one contract:

- Each date gets exactly one DateContext, made when the date is opened, before anything is
  heard or said on it, and used by that date alone.
- One date is live at a time in a show (one room, one pair on air): opening another while
  one is live raises DateInProgress.
- When a date ends, however it ends (it ran to the end, the moderator ended it before it
  opened, the session dropped, an error), its context is emptied and closed, the checks
  still in flight stop, and the set pieces it never said leave tonight's memory.
- Only what Nova said, names masked, carries from one date to the next (nova/tonight.py).
  Two Nights share nothing, so two shows in one process never meet.

The real show's server (not built yet) opens each date of the night this way, with the
next pair as next_pair; a rehearsal is a night of one date.
"""

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

from nova.cards import Pair
from nova.rehearsal import Rehearsal
from nova.tonight import Tonight


class DateInProgress(RuntimeError):
    """A date was opened while another of the same show was still live."""


class Night:
    """make: what runs a date (Rehearsal; the agent's tests give their own)."""

    def __init__(self, *, make: Callable[..., Rehearsal] = Rehearsal) -> None:
        self._make = make
        self.tonight = Tonight()  # what Nova has said tonight, across this night's dates
        self.live: Rehearsal | None = None  # the date on air now, if any
        self.dates = 0  # dates opened tonight

    @contextlib.contextmanager
    def date(self, pair: Pair, **options: Any) -> Iterator[Rehearsal]:
        """Opens one date (options: Rehearsal's), with this night's memory and a context of
        its own, and closes it when the block ends, however it ends."""
        if self.live is not None:
            raise DateInProgress("a show runs one date at a time: end the live one first")
        date = self._make(pair, tonight=self.tonight, **options)
        self.live = date
        self.dates += 1
        try:
            yield date
        finally:
            date.close()
            self.live = None
