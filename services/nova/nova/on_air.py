"""Hearing what a dater says on air, in one place. The rehearsal, the evaluation and the agent
for real shows all use it, so what the evaluation measures is what runs (decision 0005).

Every final transcript goes through the distress check's two layers (nova/distress.py)
first. An unmistakable request to stop goes to the show runner at once and is never
answered. Everything else goes to the meaning check; if it was said to Nova, the reply cue
is asked for at the same time and handed on with a hold, which resolves to the verdict once
the verdict's own cues have been handed on. Whoever speaks the reply waits for the hold and
drops the reply on a yes.

What daters say stays in memory: the Listener keeps 15 seconds of it, and nothing here logs
or stores it.
"""

import asyncio
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from nova.cards import Seat
from nova.cues import Cue
from nova.distress import Checked, DistressCheck, Hearing, Listener, Verdict
from nova.show_runner import ShowRunner

ADDRESSES_NOVA = re.compile(r"\bnova\b", re.IGNORECASE)
# A sentence end older than this isn't the one a transcript belongs to.
SENTENCE_END_MAX_AGE = 5.0


@dataclass(frozen=True)
class OnAirCues:
    """Cues for whoever runs the show, in the order they came. sentence_end: when the dater
    stopped speaking, if the first line here answers them (Nova's replies are measured from
    it). hold: the distress verdict a reply here waits for."""

    cues: list[Cue]
    sentence_end: float | None = None
    hold: asyncio.Future[Verdict] | None = None


class OnAirListener:
    def __init__(
        self,
        runner: ShowRunner,
        check: DistressCheck,
        *,
        deliver: Callable[[OnAirCues], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runner = runner
        self._listener = Listener(check)
        self._deliver = deliver
        self._clock = clock
        self._last_sentence_end: float | None = None
        self._checks: set[asyncio.Task[None]] = set()
        self.checks: list[Checked] = []  # distress checks: verdict and time only

    def speech_started(self, seat: Seat) -> None:
        """The voice detector heard a dater start speaking, which resets the silence clock."""
        self._send(self._runner.speech(self._clock(), seat))

    def speech_ended(self, seat: Seat) -> None:
        """The voice detector heard a dater stop: the end of their sentence."""
        self._last_sentence_end = self._clock()

    def hear(self, seat: Seat, text: str) -> Hearing:
        """A final transcript of what a dater said, to Nova or not. Beats already due are
        handed on first, so they're never taken for the reply."""
        now, runner = self._clock(), self._runner
        if runner.started:
            self._send(runner.tick(now))
        ended = self._last_sentence_end
        if ended is None or now - ended > SENTENCE_END_MAX_AGE:
            ended = now
        hearing = self._listener.hear(now, seat, text)
        to_nova = bool(ADDRESSES_NOVA.search(text))
        if hearing.unmistakable is not None:
            heard = runner.dater_asked_nova if to_nova else runner.dater_said
            self._send(heard(now, seat, hearing.unmistakable), ended)
            return hearing
        hold: asyncio.Future[Verdict] | None = None
        if hearing.check is not None:
            hold = asyncio.get_running_loop().create_future()
            task = asyncio.ensure_future(self._act_on(hearing.check, seat, ended, hold))
            self._checks.add(task)
            task.add_done_callback(self._checks.discard)
        if to_nova:
            self._send(runner.dater_asked_nova(now, seat, text), ended, hold)
        return hearing

    def remember(self, seat: Seat, text: str, *, at: float) -> None:
        """What a dater said before, without hearing it: for the evaluation, which sets up
        what came before the one sentence it tests."""
        self._listener.remember(at, seat, text)

    async def heard(self) -> None:
        """Waits for every distress check in flight, and what the show does with it."""
        while self._checks:
            await asyncio.gather(*self._checks)

    def cancel(self) -> None:
        """Stops the distress checks still in flight: the date is over."""
        for task in self._checks:
            task.cancel()

    async def _act_on(
        self,
        check: asyncio.Task[Checked],
        seat: Seat,
        sentence_end: float,
        hold: asyncio.Future[Verdict],
    ) -> None:
        """The verdict's cues are handed on first, then the hold resolves."""
        verdict = Verdict.UNCHECKED
        try:
            checked = await check
            verdict = checked.verdict
            self.checks.append(checked)
            self._send(self._runner.distress_check(self._clock(), seat, verdict), sentence_end)
        finally:
            if not hold.done():
                hold.set_result(verdict)

    def _send(
        self,
        cues: Sequence[Cue],
        sentence_end: float | None = None,
        hold: asyncio.Future[Verdict] | None = None,
    ) -> None:
        if cues:
            self._deliver(OnAirCues(list(cues), sentence_end, hold))
