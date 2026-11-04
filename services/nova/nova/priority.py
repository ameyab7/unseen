"""Safety checks before lines, on one model (decision 0005, 2026-10-22).

Ollama serves one request at a time, and the distress check shares it with Nova's lines
unless NOVA_CLASSIFIER_LLM is another model. Measured on the development PC, a check took a
median 377 ms idle, 698 ms (worst 1,209 ms) while set pieces were prepared, and 1,307 ms
(worst 1,760 ms) with both daters checked while live lines were written: queued behind lines,
against a 2 s timeout. With this gate, while any check is waiting or running, no new line
request goes to the model: a check waits at most for the one line request already running,
never for the prep queue or the next line. A line waits for the checks, inside its own
timeout, so a slow check can cost a line its model words (the fallback), never a check its
verdict.

It can't stop a request already running: cancelling one would cost that line, and with
Claude may not stop the work at all (decision 0005). Separate hardware for the classifier is
the product owner's later choice.
"""

import asyncio
import contextlib
import heapq
import itertools
from collections.abc import AsyncIterator


class SafetyFirst:
    """One for each model shared by lines and checks."""

    def __init__(self) -> None:
        self._checks = 0
        self._clear = asyncio.Event()
        self._clear.set()

    @property
    def checking(self) -> int:
        """Checks waiting or running now."""
        return self._checks

    @contextlib.asynccontextmanager
    async def check(self) -> AsyncIterator[None]:
        """Around a safety check's request: lines hold back until it's done."""
        self._checks += 1
        self._clear.clear()
        try:
            yield
        finally:
            self._checks -= 1
            if not self._checks:
                self._clear.set()

    async def line(self) -> None:
        """Before a line's request: waits while any check is waiting or running."""
        await self._clear.wait()


class Slots:
    """At most `size` requests in flight at once (NOVA_PREP_PARALLEL), and the next free
    slot goes to the waiting request with the lowest priority number, then to the earliest.
    Set pieces take their place in the running order as their priority, so with one slot
    the opening and the first questions are written, candidates and meaning check, before
    anything needed later. In arrival order, the second first question queued behind the
    first request of every later moment, and the prep budget often ran out on it (rehearsal,
    2026-10-31; decision 0011)."""

    def __init__(self, size: int) -> None:
        if size < 1:
            raise ValueError("at least one slot")
        self._free = size
        self._waiting: list[tuple[int, int, asyncio.Future[None]]] = []
        self._arrivals = itertools.count()

    @contextlib.asynccontextmanager
    async def slot(self, priority: int = 0) -> AsyncIterator[None]:
        await self._acquire(priority)
        try:
            yield
        finally:
            self._release()

    async def _acquire(self, priority: int) -> None:
        if self._free and not self._waiting:
            self._free -= 1
            return
        granted = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiting, (priority, next(self._arrivals), granted))
        try:
            await granted
        except asyncio.CancelledError:
            if granted.done() and not granted.cancelled():
                self._release()  # handed the slot just as it was cancelled: pass it on
            raise

    def _release(self) -> None:
        while self._waiting:
            *_, granted = heapq.heappop(self._waiting)
            if not granted.done():  # one cancelled while it waited is skipped
                granted.set_result(None)
                return
        self._free += 1
