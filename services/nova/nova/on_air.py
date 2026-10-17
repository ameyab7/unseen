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
Speech-to-text punctuates each final, so the instant layer reads past the punctuation that
ends a final ("I want to stop." then "eating sugar." is no request either). A request
heard only with that punctuation might be a fragment or might be meant: the moderator is
alerted at once (possible distress), and the meaning layer decides whether the date
pauses. If a dater who said a request keeps talking, the words so far are checked again
after MATCH_CAP seconds and acted on if they still end with it. An unmistakable request
goes to the show runner and is never answered. Every other utterance goes to the meaning
check once; if it was said to Nova, the reply cue is asked for at the same time and handed
on with a hold, which resolves to the verdict once the verdict's own cues have been handed
on. Whoever speaks the reply waits for the hold and drops the reply on a yes.

An utterance also closes MAX_UTTERANCE seconds after its first final while the dater is
still speaking, so a long story, or noise that keeps the voice detector hearing speech, is
still checked: the words so far go to both layers, with no reply, since the dater hasn't
finished. What they say next is the next utterance.

Utterances close in tick(), which whoever runs the show calls as time passes, or in
listen(), which does it until cancelled; anything a dater does also closes what's due. At
the end of the date, finish() closes whatever is still open and waits for the checks in
flight; a check that can't finish in time alerts the moderator as unchecked.

What daters say stays in memory: an utterance until it closes, and the Listener 15
seconds of it, pruned as time passes and cleared when the date is over. Nothing here logs
or stores it.
"""

import asyncio
import contextlib
import re
import time
from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from nova.cards import Seat
from nova.conduct import personal_detail
from nova.config import Settings
from nova.cues import Conduct, Cue
from nova.distress import Checked, DistressCheck, Listener, Verdict
from nova.show_runner import ShowRunner
from nova.text import straight_quotes

# A request the instant layer heard waits this long for the dater to stop speaking, then
# the words so far are checked again.
MATCH_CAP = 3.0
# An utterance closes this long after its first final even if the dater is still speaking,
# so the meaning layer always hears them.
MAX_UTTERANCE = 10.0
# A sentence end older than this isn't the one a transcript belongs to.
SENTENCE_END_MAX_AGE = 5.0
# Words to Nova name Nova in the first few words of a sentence or a final transcript
# ("Nova, ...", "Hey Nova ...", "OK so Nova ...") or as its last word ("..., Nova?").
# Hesitations and openers before those words don't count ("Okay, so, um, Nova, ...").
# Anywhere else, Nova is talked about.
ADDRESS_WORDS = 3
OPENERS = frozenset(
    {"um", "umm", "uh", "uhh", "er", "erm", "hmm", "hm", "oh", "okay", "ok", "so", "well"}
    | {"wait", "hey", "hi", "like", "right", "yeah", "alright"}
)
# Nova directly followed by one of these is talked about, wherever it comes ("Nova said",
# "Nova is so funny"); with a comma between, Nova is asked ("Nova, is it my turn?").
# "Nova's" is one word, so never Nova.
MENTIONED_BY = frozenset(
    {"said", "says", "asked", "asks", "is", "isn't", "was", "wasn't", "thinks", "thought"}
)
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)*")
_SENTENCE = re.compile(r"[^.?!]+")


def addresses_nova(*finals: str) -> bool:
    """Whether a dater said these words, one or more final transcripts, to Nova, not about
    Nova ("did you hear what Nova said?") or about Nova's things ("Nova's questions").
    Speech recognition splits sentences at pauses and an utterance joins them, so Nova is
    looked for in each sentence of the whole and in each final: "That's so funny." then
    "Nova, what do you think?" is to Nova."""
    finals = tuple(straight_quotes(final.lower()) for final in finals)
    text = " ".join(finals)
    words = list(_WORD.finditer(text))
    starts = [word.start() for word in words]

    def between(begin: int, end: int) -> range:
        """The words that start between two places in text."""
        return range(bisect_left(starts, begin), bisect_left(starts, end))

    def opening(part: range) -> range:
        """The first few words of a part, after any hesitations and openers."""
        skip = 0
        while skip < len(part) and words[part[skip]].group() in OPENERS:
            skip += 1
        return part[skip : skip + ADDRESS_WORDS]

    parts = [between(sentence.start(), sentence.end()) for sentence in _SENTENCE.finditer(text)]
    begin = 0
    for final in finals:
        parts.append(between(begin, begin + len(final)))
        begin += len(final) + 1
    named = {k for part in parts for k in (*opening(part), *part[-1:])}
    return any(_asked(text, words, k) for k in named)


def _asked(text: str, words: list[re.Match[str]], k: int) -> bool:
    """Word k is Nova, and not directly followed by a verb that talks about Nova."""
    if words[k].group() != "nova":
        return False
    after = words[k + 1] if k + 1 < len(words) else None
    return not (
        after is not None
        and after.group() in MENTIONED_BY
        and text[words[k].end() : after.start()].isspace()
    )


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
    # When the instant layer first heard a request that, once more was said, it hears only
    # with speech-to-text's punctuation; and whether the moderator has heard of it.
    loose: float | None = None
    alerted: bool = False

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
        # Each meaning check in flight: what acts on it, with the dater, the hold it resolves
        # and the model call itself.
        self._checks: dict[
            asyncio.Task[None], tuple[Seat, asyncio.Future[Verdict], asyncio.Task[Checked]]
        ] = {}
        self.checks: list[Checked] = []  # distress checks: verdict and time only
        self.closed: list[Closed] = []  # utterances: layer and wait only

    def speech_started(self, seat: Seat) -> None:
        """The voice detector heard a dater start speaking, which resets the silence clock
        and keeps their utterance open."""
        now = self._clock()
        self._close_due(now)
        dater = self._daters[seat]
        dater.speaking, dater.ended = True, None
        self._send(self._runner.speech_started(now, seat))

    def speech_ended(self, seat: Seat) -> None:
        """The voice detector heard a dater stop: the end of their sentence, and the grace
        starts once the last final is in. The show runner hears it too: a beat's lines and
        sounds that waited for them go out once nobody is speaking."""
        now = self._clock()
        self._close_due(now)
        dater = self._daters[seat]
        dater.speaking, dater.ended = False, now
        self._send(self._runner.speech_stopped(now, seat))

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
        if self._listener.unmistakable(now, seat, utterance.words) is None:
            self._unmatched(now, seat, utterance)
        elif utterance.matched is None:
            utterance.matched = now

    def tick(self) -> None:
        """Acts on every utterance that has closed by now, on any request that has waited
        MATCH_CAP for a dater who keeps talking, and on anything a dater has been saying for
        MAX_UTTERANCE without a break. Lets go of words older than the Listener keeps."""
        now = self._clock()
        self._close_due(now)
        self._listener.prune(now)

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
            elif dater.speaking:
                due.append(utterance.started + MAX_UTTERANCE)
            # Only while they speak, as in _close_due: once they stop, the close alerts.
            if dater.speaking and utterance.loose is not None and not utterance.alerted:
                due.append(utterance.loose + MATCH_CAP)
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
        """Waits for every distress check in flight, and what the show does with it. It never
        waits on a check that is already done (since Python 3.12, awaiting gather() over
        finished tasks doesn't yield, so the loop would spin), and it re-raises a check that
        failed, so a bug shows."""
        while True:
            done = [task for task in self._checks if task.done()]
            for task in done:
                del self._checks[task]
            for task in done:
                error = None if task.cancelled() else task.exception()
                if error is not None:
                    raise error
            if not self._checks:
                return
            await asyncio.wait(set(self._checks))

    def flush(self) -> None:
        """Closes every utterance still open, at once: the date is over, so nobody waits for
        the rest of a sentence."""
        now = self._clock()
        for seat, dater in self._daters.items():
            if dater.utterance is not None:
                self._close(now, seat, dater, dater.utterance)

    async def finish(self, timeout: float) -> None:
        """The date is over: whatever a dater was still saying is acted on, the checks in
        flight get up to timeout seconds, and any still running then alerts the moderator as
        unchecked (cancel)."""
        self.flush()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.heard(), timeout)
        self.cancel()

    def cancel(self) -> None:
        """Stops the distress checks still in flight, and lets go of what daters said: the
        date is over. Each check stopped this way resolves its hold as unchecked and alerts
        the moderator, as a check that failed does."""
        for task, (seat, hold, check) in list(self._checks.items()):
            if task.done():
                continue
            task.cancel()
            check.cancel()  # in case the task never started, and so never awaited it
            if not hold.done():
                hold.set_result(Verdict.UNCHECKED)
                self._send(self._runner.distress_check(self._clock(), seat, Verdict.UNCHECKED))
        self._checks.clear()
        self._listener.clear()
        for dater in self._daters.values():
            dater.utterance, dater.ended = None, None

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
            elif dater.speaking:
                if (
                    utterance.loose is not None
                    and not utterance.alerted
                    and now >= utterance.loose + MATCH_CAP
                ):
                    self._possible_request(now, seat, utterance)
                if utterance.matched is None and now >= utterance.started + MAX_UTTERANCE:
                    self._close(now, seat, dater, utterance, capped=True)

    def _close(
        self, now: float, seat: Seat, dater: _Dater, utterance: _Utterance, *, capped: bool = False
    ) -> None:
        """The utterance is over: the instant layer on all of it, or else the meaning layer,
        and a reply if it was said to Nova. Beats already due are handed on first, so
        they're never taken for the reply. capped: the dater is still speaking, past
        MAX_UTTERANCE, so their words are checked but not answered yet."""
        ended = dater.ended if dater.ended is not None else utterance.last_final
        dater.utterance, dater.ended = None, None
        runner, words = self._runner, utterance.text
        if runner.started:
            self._send(runner.tick(now))
        hearing = self._listener.utterance(now, seat, utterance.words, since=utterance.started)
        instant = hearing.unmistakable is not None
        self.closed.append(Closed(seat, instant=instant, waited=None if capped else now - ended))
        slipped = personal_detail(words)  # conduct's instant layer (decision 0006)
        if slipped is not None:
            self._send(runner.dater_conduct(now, seat, slipped.conduct, street=slipped.street))
        if hearing.unmistakable is not None:
            self._wants_to_stop(now, seat, hearing.unmistakable, utterance, ended)
            return
        if self._listener.unmistakable(now, seat, utterance.words, loose=True) is not None:
            self._possible_request(now, seat, utterance)
        hold: asyncio.Future[Verdict] | None = None
        if hearing.check is not None:
            hold = asyncio.get_running_loop().create_future()
            task = asyncio.ensure_future(
                self._act_on(
                    hearing.check,
                    seat,
                    ended,
                    now,
                    hold,
                    heard_detail=bool(slipped),
                    alerted=utterance.alerted,
                )
            )
            self._checks[task] = (seat, hold, hearing.check)
            task.add_done_callback(self._done)
        if not capped and addresses_nova(*utterance.words):  # one reply, to the whole utterance
            asked = runner.dater_asked_nova(now, seat, words, wants_to_stop=False)
            self._send(asked, ended, hold, now)

    def _recheck(self, now: float, seat: Seat, dater: _Dater, utterance: _Utterance) -> None:
        """A request has waited MATCH_CAP while the dater kept talking: if the words so far
        still end with it, it's acted on now and the utterance is over; if not, it waits for
        the next one or the close."""
        heard = self._listener.unmistakable(now, seat, utterance.words)
        if heard is None:
            self._unmatched(now, seat, utterance)
            return
        stopped = not dater.speaking and dater.ended is not None
        ended = dater.ended if stopped else utterance.matched
        assert ended is not None
        dater.utterance, dater.ended = None, None
        self._listener.forget(seat)
        if self._runner.started:
            self._send(self._runner.tick(now))
        self.closed.append(Closed(seat, instant=True, waited=now - ended if stopped else None))
        self._wants_to_stop(now, seat, heard, utterance, ended)

    def _unmatched(self, now: float, seat: Seat, utterance: _Utterance) -> None:
        """The instant layer no longer hears a request in the utterance. If it still does with
        speech-to-text's punctuation, the request may be meant: from when it was first heard,
        it gets the moderator's attention after MATCH_CAP, or when the utterance closes."""
        loose = self._listener.unmistakable(now, seat, utterance.words, loose=True)
        if utterance.loose is None and loose is not None:
            utterance.loose = utterance.matched if utterance.matched is not None else now
        utterance.matched = None

    def _possible_request(self, now: float, seat: Seat, utterance: _Utterance) -> None:
        """A request heard only with speech-to-text's punctuation ("I want to stop." then more
        words): the moderator is alerted at once, as possible distress, and the meaning layer
        decides whether the date pauses. Once per utterance."""
        if utterance.alerted:
            return
        utterance.alerted = True
        self._send(self._runner.distress_check(now, seat, Verdict.MAYBE))

    def _wants_to_stop(
        self, now: float, seat: Seat, heard: str, utterance: _Utterance, ended: float
    ) -> None:
        runner = self._runner
        said = runner.dater_asked_nova if addresses_nova(*utterance.words) else runner.dater_said
        self._send(said(now, seat, heard, wants_to_stop=True), ended, closed_at=now)

    def _done(self, task: asyncio.Task[None]) -> None:
        self._checks.pop(task, None)

    async def _act_on(
        self,
        check: asyncio.Task[Checked],
        seat: Seat,
        sentence_end: float,
        closed_at: float,
        hold: asyncio.Future[Verdict],
        *,
        heard_detail: bool = False,
        alerted: bool = False,
    ) -> None:
        """The verdict's cues are handed on first, then the hold resolves; then the conduct
        label from the same call, unless it's a personal detail the instant layer already
        heard. alerted: the moderator already heard of this utterance as possible distress,
        so a maybe isn't sent twice."""
        verdict = Verdict.UNCHECKED
        conduct: Conduct | None = None
        try:
            checked = await check
            verdict, conduct = checked.verdict, checked.conduct
            self.checks.append(checked)
            if not (alerted and verdict is Verdict.MAYBE):
                cues = self._runner.distress_check(self._clock(), seat, verdict)
                self._send(cues, sentence_end, closed_at=closed_at)
        finally:
            if not hold.done():
                hold.set_result(verdict)
        repeat = conduct is Conduct.PERSONAL_DETAIL and heard_detail
        if conduct not in (None, Conduct.NONE) and not repeat:
            self._send(self._runner.dater_conduct(self._clock(), seat, conduct))

    def _send(
        self,
        cues: Sequence[Cue],
        sentence_end: float | None = None,
        hold: asyncio.Future[Verdict] | None = None,
        closed_at: float | None = None,
    ) -> None:
        if cues:
            self._deliver(OnAirCues(list(cues), sentence_end, hold, closed_at))
