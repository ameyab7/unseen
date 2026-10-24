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
