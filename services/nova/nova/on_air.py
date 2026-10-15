"""Hearing what a dater says on air, in one place. The rehearsal, the evaluation and the agent
for real shows all use it, so what the evaluation measures is what runs (decision 0005).

Speech recognition finalizes at almost every pause, so a final transcript is often half a
sentence ("Nova," then "what's your favourite film?"). Nova acts on utterances instead:
each dater's finals from when the voice detector hears them start speaking, until they
have stopped and no new final has come for the grace (NOVA_UTTERANCE_GRACE_SECONDS), or
at most NOVA_UTTERANCE_MAX_WAIT_SECONDS after they stopped. A final that comes after that
starts the next utterance.

The instant layer (nova/distress.py) hears each final as it arrives, joined with the
dater's last few seconds, but acts only once the utterance closes, and only if the whole
utterance still ends with the request: "I want to stop" then "eating sugar" is no request.
If a dater who said one keeps talking, the words so far are checked again after
MATCH_CAP seconds and acted on if they still end with it. An unmistakable request goes to
the show runner and is never answered. Every other utterance goes to the meaning check
once; if it was said to Nova, the reply cue is asked for at the same time and handed on
with a hold, which resolves to the verdict once the verdict's own cues have been handed
on. Whoever speaks the reply waits for the hold and drops the reply on a yes.

Utterances close in tick(), which whoever runs the show calls as time passes, or in
listen(), which does it until cancelled; anything a dater does also closes what's due.

What daters say stays in memory: an utterance until it closes, and the Listener 15
seconds of it. Nothing here logs or stores it.
"""

import asyncio
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from nova.cards import Seat
from nova.config import Settings
from nova.cues import Cue
from nova.distress import Checked, DistressCheck, Listener, Verdict
from nova.show_runner import ShowRunner

# A request the instant layer heard waits this long for the dater to stop speaking, then
# the words so far are checked again.
MATCH_CAP = 3.0
# A sentence end older than this isn't the one a transcript belongs to.
SENTENCE_END_MAX_AGE = 5.0
# Words to Nova name Nova in the first few words ("Nova, ...", "Hey Nova ...", "OK so
# Nova ...") or as the last word ("..., Nova?"). Anywhere else, Nova is talked about.
ADDRESS_WORDS = 3
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)*")


def addresses_nova(words: str) -> bool:
    """Whether a dater said these words to Nova, not about Nova ("did you hear what Nova
    said?") or about Nova's things ("Nova's questions")."""
    said = _WORD.findall(words.lower().replace("’", "'"))
    return "nova" in said[:ADDRESS_WORDS] or said[-1:] == ["nova"]


@dataclass(frozen=True)
class OnAirCues:
    """Cues for whoever runs the show, in the order they came. sentence_end: when the dater
    stopped speaking, if the first line here answers them (Nova's replies are measured from
    it). closed_at: when Nova acted on what they said; from sentence_end, the grace. hold:
    the distress verdict a reply here waits for."""

    cues: list[Cue]
    sentence_end: float | None = None
    hold: asyncio.Future[Verdict] | None = None
    closed_at: float | None = None


@dataclass(frozen=True)
class Closed:
    """One utterance Nova acted on, without its words. instant: the instant layer acted on
    it, at its close or after MATCH_CAP; otherwise it went to the meaning layer. waited:
    seconds from when the dater stopped speaking, or None if they hadn't yet."""

    seat: Seat
    instant: bool
    waited: float | None


@dataclass
class _Utterance:
    started: float  # when its first final arrived
    last_final: float
    words: list[str] = field(default_factory=list)
    matched: float | None = None  # when the instant layer heard a request in it

    @property
    def text(self) -> str:
        return " ".join(self.words)


@dataclass
class _Dater:
    speaking: bool = False
    ended: float | None = None  # when they stopped speaking, for the utterance now open
    utterance: _Utterance | None = None


class OnAirListener:
    def __init__(
        self,
        runner: ShowRunner,
        check: DistressCheck,
        *,
        deliver: Callable[[OnAirCues], None],
        clock: Callable[[], float] = time.monotonic,
        grace: float = Settings.utterance_grace,
        max_wait: float = Settings.utterance_max_wait,
    ) -> None:
        self._runner = runner
        self._listener = Listener(check)
        self._deliver = deliver
        self._clock = clock
        self.grace, self.max_wait = grace, max_wait
        self._daters = {seat: _Dater() for seat in Seat}
        self._checks: set[asyncio.Task[None]] = set()
        self.checks: list[Checked] = []  # distress checks: verdict and time only
        self.closed: list[Closed] = []  # utterances: layer and wait only

    def speech_started(self, seat: Seat) -> None:
        """The voice detector heard a dater start speaking, which resets the silence clock
        and keeps their utterance open."""
        now = self._clock()
        self._close_due(now)
        dater = self._daters[seat]
        dater.speaking, dater.ended = True, None
        self._send(self._runner.speech(now, seat))

    def speech_ended(self, seat: Seat) -> None:
        """The voice detector heard a dater stop: the end of their sentence, and the grace
        starts once the last final is in."""
        now = self._clock()
        self._close_due(now)
        dater = self._daters[seat]
        dater.speaking, dater.ended = False, now

    def hear(self, seat: Seat, text: str) -> None:
        """A final transcript of what a dater said, to Nova or not. It joins their open
        utterance, or starts one; nothing is acted on until the utterance closes."""
        now = self._clock()
        self._close_due(now)
        if not text.strip():
            return
        dater = self._daters[seat]
        if not dater.speaking and (dater.ended is None or now - dater.ended > SENTENCE_END_MAX_AGE):
            dater.ended = now  # no voice detector heard them stop: the transcript stands in
        utterance = dater.utterance
        if utterance is None:
            utterance = dater.utterance = _Utterance(started=now, last_final=now)
        utterance.words.append(text)
        utterance.last_final = now
        self._listener.remember(now, seat, text)
        if self._listener.unmistakable(now, seat, utterance.text) is None:
            utterance.matched = None
        elif utterance.matched is None:
            utterance.matched = now

    def tick(self) -> None:
        """Acts on every utterance that has closed by now, and on any request that has waited
        MATCH_CAP for a dater who keeps talking."""
        self._close_due(self._clock())

    @property
    def next_due(self) -> float | None:
        """When tick() next has something to do, if anything is open."""
        due: list[float] = []
        for dater in self._daters.values():
            utterance = dater.utterance
            if utterance is None:
                continue
            if not dater.speaking:
                due.append(self._closes_at(dater, utterance))
            if utterance.matched is not None:
                due.append(utterance.matched + MATCH_CAP)
        return min(due, default=None)

    async def listen(self, *, every: float = 0.1) -> None:
        """Ticks until cancelled, at each utterance's close and at least every `every`
        seconds, so a dater is heard on time without anyone else ticking."""
        while True:
            self.tick()
            due = self.next_due
            await asyncio.sleep(every if due is None else min(every, max(0.0, due - self._clock())))

    def remember(self, seat: Seat, text: str, *, at: float) -> None:
        """What a dater said before, without hearing it: for the evaluation, which sets up
        what came before the words it tests."""
        self._listener.remember(at, seat, text)

    async def heard(self) -> None:
        """Waits for every distress check in flight, and what the show does with it."""
        while self._checks:
            await asyncio.gather(*self._checks)

    def cancel(self) -> None:
        """Stops the distress checks still in flight: the date is over."""
        for task in self._checks:
            task.cancel()

    # --- Internals --------------------------------------------------------------------------

    def _closes_at(self, dater: _Dater, utterance: _Utterance) -> float:
        ended = dater.ended if dater.ended is not None else utterance.last_final
        return min(max(ended, utterance.last_final) + self.grace, ended + self.max_wait)

    def _close_due(self, now: float) -> None:
        for seat, dater in self._daters.items():
            utterance = dater.utterance
            if utterance is None:
                continue
            if not dater.speaking and now >= self._closes_at(dater, utterance):
                self._close(now, seat, dater, utterance)
            elif utterance.matched is not None and now >= utterance.matched + MATCH_CAP:
                self._recheck(now, seat, dater, utterance)

    def _close(self, now: float, seat: Seat, dater: _Dater, utterance: _Utterance) -> None:
        """The utterance is over: the instant layer on all of it, or else the meaning layer,
        and a reply if it was said to Nova. Beats already due are handed on first, so
        they're never taken for the reply."""
        ended = dater.ended if dater.ended is not None else utterance.last_final
        dater.utterance, dater.ended = None, None
        runner, words = self._runner, utterance.text
        if runner.started:
            self._send(runner.tick(now))
        hearing = self._listener.utterance(now, seat, words, since=utterance.started)
        instant = hearing.unmistakable is not None
        self.closed.append(Closed(seat, instant=instant, waited=now - ended))
        if hearing.unmistakable is not None:
            self._wants_to_stop(now, seat, hearing.unmistakable, words, ended)
            return
        hold: asyncio.Future[Verdict] | None = None
        if hearing.check is not None:
            hold = asyncio.get_running_loop().create_future()
            task = asyncio.ensure_future(self._act_on(hearing.check, seat, ended, now, hold))
            self._checks.add(task)
            task.add_done_callback(self._checks.discard)
        if addresses_nova(words):
            self._send(runner.dater_asked_nova(now, seat, words), ended, hold, now)

    def _recheck(self, now: float, seat: Seat, dater: _Dater, utterance: _Utterance) -> None:
        """A request has waited MATCH_CAP while the dater kept talking: if the words so far
        still end with it, it's acted on now and the utterance is over; if not, it waits for
        the next one or the close."""
        heard = self._listener.unmistakable(now, seat, utterance.text)
        if heard is None:
            utterance.matched = None
            return
        stopped = not dater.speaking and dater.ended is not None
        ended = dater.ended if stopped else utterance.matched
        assert ended is not None
        dater.utterance, dater.ended = None, None
        self._listener.forget(seat)
        if self._runner.started:
            self._send(self._runner.tick(now))
        self.closed.append(Closed(seat, instant=True, waited=now - ended if stopped else None))
        self._wants_to_stop(now, seat, heard, utterance.text, ended)

    def _wants_to_stop(self, now: float, seat: Seat, heard: str, words: str, ended: float) -> None:
        runner = self._runner
        said = runner.dater_asked_nova if addresses_nova(words) else runner.dater_said
        self._send(said(now, seat, heard), ended, closed_at=now)

    async def _act_on(
        self,
        check: asyncio.Task[Checked],
        seat: Seat,
        sentence_end: float,
        closed_at: float,
        hold: asyncio.Future[Verdict],
    ) -> None:
        """The verdict's cues are handed on first, then the hold resolves."""
        verdict = Verdict.UNCHECKED
        try:
            checked = await check
            verdict = checked.verdict
            self.checks.append(checked)
            cues = self._runner.distress_check(self._clock(), seat, verdict)
            self._send(cues, sentence_end, closed_at=closed_at)
        finally:
            if not hold.done():
                hold.set_result(verdict)

    def _send(
        self,
        cues: Sequence[Cue],
        sentence_end: float | None = None,
        hold: asyncio.Future[Verdict] | None = None,
        closed_at: float | None = None,
    ) -> None:
        if cues:
            self._deliver(OnAirCues(list(cues), sentence_end, hold, closed_at))
