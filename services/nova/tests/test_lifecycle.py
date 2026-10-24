"""A model request's life (decision 0005): the warm-up's one deadline and bounded retries, a
timed-out request cancelled and its late answer never used, preparation capped, and the
distress check still heard while set pieces are written. Fake models only: never Ollama."""

import asyncio
import time
from types import SimpleNamespace

import anthropic
import openai
import pytest

from nova.cues import Moment, Say
from nova.distress import DistressCheck, Verdict
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.lines import ErrorKind, LineWriter, Source
from nova.models import (
    WARM_UP_ATTEMPTS,
    ModelError,
    OllamaModel,
    WarmUpError,
    retryable,
    warm_up,
)

NO_PAUSE = (0.0,)  # backoff for tests: the retry rules, without the waiting


def status(sdk, name: str, code: int) -> Exception:
    """An SDK's own status error, as it raises one for an HTTP response."""
    response = SimpleNamespace(status_code=code, headers={}, request=None)
    return getattr(sdk, name)(message="x", response=response, body=None)


CONNECTION = openai.APIConnectionError(request=None)  # Ollama not up yet
SDK_TIMEOUT = openai.APITimeoutError(request=None)


class Scripted:
    """Answers each request in turn from a script: an exception to raise, a number of
    seconds to take before answering, or None to hang until cancelled. Records each
    request's start, and whether it was cancelled."""

    name = "fake/model"

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.started: list[float] = []
        self.cancelled = 0

    async def label(self, *, system: str, prompt: str, schema=None) -> str:
        return await self._next()

    async def write(self, *, system: str, prompt: str) -> str:
        return await self._next()

    async def _next(self) -> str:
        self.started.append(time.monotonic())
        step = self.script.pop(0) if self.script else 0.0
        try:
            if isinstance(step, Exception):
                raise step
            if step is None:
                await asyncio.Event().wait()
            await asyncio.sleep(step)
            return "Maya, Theo, over to you."
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


# --- The warm-up: one deadline, bounded retries ------------------------------------------


async def test_a_cold_start_that_fails_fast_is_tried_again_and_then_warms():
    """Ollama still starting refuses the connection, then loads the model and answers."""
    model = Scripted(CONNECTION, SDK_TIMEOUT, 0.05)
    warmed = await warm_up(model, deadline=2.0, backoff=NO_PAUSE)
    assert warmed.error is None and warmed.attempts == 3


async def test_a_slow_cold_load_is_held_never_cancelled_to_try_again():
    """Cancelling the request waiting on a load aborts the load (measured on Ollama)."""
    model = Scripted(0.3)  # a load that takes most of the deadline
    warmed = await warm_up(model, deadline=1.0, backoff=NO_PAUSE)
    assert warmed.error is None and warmed.attempts == 1 and model.cancelled == 0


async def test_a_load_that_outlasts_the_deadline_fails_once_and_says_so():
    model = Scripted(None)
    started = time.monotonic()
    warmed = await warm_up(model, deadline=0.2, backoff=NO_PAUSE)
    assert warmed.error is WarmUpError.TIMEOUT and warmed.attempts == 1
    assert time.monotonic() - started < 0.4, "the deadline, never more"


@pytest.mark.parametrize(
    "error",
    [
        status(openai, "AuthenticationError", 401),  # a bad key
        status(openai, "NotFoundError", 404),  # a model not pulled
        status(openai, "BadRequestError", 400),  # a parameter it rejects
        status(anthropic, "PermissionDeniedError", 403),
    ],
    ids=["bad-key", "not-pulled", "bad-request", "no-permission"],
)
async def test_a_permanent_failure_is_never_tried_again(error):
    model = Scripted(error, 0.0)
    warmed = await warm_up(model, deadline=2.0, backoff=NO_PAUSE)
    assert warmed.error is WarmUpError.REJECTED and warmed.attempts == 1
    assert len(model.started) == 1


async def test_a_failure_we_cant_tell_is_never_tried_again_either():
    model = Scripted(RuntimeError("something odd"), 0.0)
    warmed = await warm_up(model, deadline=2.0, backoff=NO_PAUSE)
    assert warmed.error is WarmUpError.ERROR and warmed.attempts == 1


async def test_a_failure_that_keeps_coming_back_stops_at_the_attempt_limit():
    model = Scripted(*[CONNECTION] * 20)
    warmed = await warm_up(model, deadline=5.0, backoff=NO_PAUSE)
    assert warmed.error is WarmUpError.ERROR and warmed.attempts == WARM_UP_ATTEMPTS


async def test_retries_back_off_and_never_pass_the_deadline():
    model = Scripted(*[CONNECTION] * 20)
    started = time.monotonic()
    warmed = await warm_up(model, deadline=0.35, backoff=(0.1, 0.2, 0.4))
    assert warmed.error is WarmUpError.ERROR and time.monotonic() - started < 0.35
    gaps = [b - a for a, b in zip(model.started, model.started[1:], strict=False)]
    # Tries at 0, 0.1 and 0.3 s; the next pause (0.4 s) would pass the deadline: no more.
    assert len(model.started) == 3 and gaps[0] >= 0.1 and gaps[1] >= 0.2


@pytest.mark.parametrize(
    ("error", "again"),
    [
        (CONNECTION, True),
        (SDK_TIMEOUT, True),
        (status(openai, "RateLimitError", 429), True),
        (status(openai, "InternalServerError", 500), True),
        (status(anthropic, "InternalServerError", 529), True),  # Claude overloaded
        (status(anthropic, "AuthenticationError", 401), False),
        (status(openai, "UnprocessableEntityError", 422), False),
        (ValueError("bad JSON"), False),
    ],
)
def test_only_failures_that_may_pass_are_retryable(error, again):
    assert retryable(error) is again


# --- A timed-out request is cancelled, and its answer never used -----------------------------


async def test_a_live_line_that_times_out_is_cancelled_and_the_fallback_said():
    model = Scripted(None)
    writer = LineWriter(model, REHEARSAL_PAIR, next_pair=NEXT_PAIR, live_timeout=0.05)
    line = await writer.line_for(Say(Moment.SILENCE))
    assert model.cancelled == 1, "the request itself is cancelled, not left running"
    assert line.source is Source.FALLBACK and line.error is ErrorKind.TIMEOUT


async def test_a_late_answer_never_reaches_a_later_cue():
    """The slow request is cancelled, so its words can't turn up in the next line."""

    class Late(Scripted):
        async def write(self, *, system: str, prompt: str) -> str:
            if not self.started:
                self.started.append(time.monotonic())
                try:
                    await asyncio.sleep(0.3)
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise
                return "LATE: Maya, Theo, this answer is for a cue long gone."
            return "Maya, Theo, over to you."

    model = Late()
    writer = LineWriter(model, REHEARSAL_PAIR, next_pair=NEXT_PAIR, live_timeout=0.05)
    first = await writer.line_for(Say(Moment.SILENCE))
    await asyncio.sleep(0.4)  # past when the late answer would have come
    second = await writer.line_for(Say(Moment.SILENCE))
    assert model.cancelled == 1 and "LATE" not in first.text + second.text
    assert second.source is Source.MODEL


# --- Preparation is capped, and nothing it starts is left running ----------------------------


class Counting(Scripted):
    """Each request takes `seconds`, counting how many are in flight at once."""

    def __init__(self, seconds: float) -> None:
        super().__init__()
        self.seconds, self.now, self.peak = seconds, 0, 0

    async def write(self, *, system: str, prompt: str) -> str:
        self.now += 1
        self.peak = max(self.peak, self.now)
        try:
            await asyncio.sleep(self.seconds)
            return "Maya and Theo, here we go."
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.now -= 1


@pytest.mark.parametrize("limit", [1, 2])
async def test_preparation_never_has_more_than_its_limit_in_flight_and_leaves_none(limit):
    """When the budget runs out, what's in flight is cancelled: no request is left
    running, or queued on Nova's side, behind the date's live lines and checks."""
    model = Counting(0.05)
    writer = LineWriter(
        model, REHEARSAL_PAIR, next_pair=NEXT_PAIR, prep_budget=0.12, prep_parallel=limit
    )
    await writer.prepare()
    assert writer.budget_exhausted and model.peak <= limit
    assert model.now == 0, "nothing still running after prepare() returns"
    assert model.cancelled >= 1, "the one in flight at the budget was cancelled"
    await asyncio.sleep(0.1)
    assert model.now == 0


# --- The distress check is heard while set pieces are written --------------------------------


class OneAtATime:
    """A server that serves one request at a time, first come first served, as this
    machine's Ollama does: a line takes `line` seconds, a label `label` seconds."""

    name = "fake/ollama"

    def __init__(self, *, line: float, label: float) -> None:
        self.line, self.label_takes = line, label
        self._slot = asyncio.Lock()

    async def write(self, *, system: str, prompt: str) -> str:
        async with self._slot:
            await asyncio.sleep(self.line)
        return "Maya and Theo, here we go."

    async def label(self, *, system: str, prompt: str, schema=None) -> str:
        async with self._slot:
            await asyncio.sleep(self.label_takes)
        return "no"


async def checks_during_prep(prep_parallel: int | None) -> list[Verdict]:
    server = OneAtATime(line=0.03, label=0.005)
    writer = LineWriter(server, REHEARSAL_PAIR, next_pair=NEXT_PAIR, prep_parallel=prep_parallel)
    check = DistressCheck(server, timeout=0.08)
    verdicts: list[Verdict] = []
    preparing = asyncio.ensure_future(writer.prepare())
    while not preparing.done():
        verdicts.append((await check.check("I swim in the sea every morning.")).verdict)
        await asyncio.sleep(0.01)
    await preparing
    return verdicts


async def test_with_preparation_capped_every_distress_check_is_heard_in_time():
    """A check waits behind at most one set piece (NOVA_PREP_PARALLEL=1), never a batch."""
    verdicts = await checks_during_prep(1)
    assert verdicts and Verdict.UNCHECKED not in verdicts


async def test_uncapped_preparation_is_what_would_starve_the_check():
    """The contrast: thirty set-piece requests queued at once push the check past its time."""
    assert Verdict.UNCHECKED in await checks_during_prep(None)


# --- A line cut off by the token limit is never said ------------------------------------------


async def test_an_ollama_line_cut_off_at_the_token_limit_is_an_error_not_a_line():
    async def create(**_):
        choice = SimpleNamespace(
            finish_reason="length", message=SimpleNamespace(content="Maya, wh")
        )
        return SimpleNamespace(choices=[choice])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(ModelError, match="cut off"):
        await OllamaModel("gemma", client=client).write(system="RULES", prompt="TASK")


# --- The documented failure: a cold start turned into a date of fallbacks ----------------------


class ColdServer:
    """A model that must load before it answers, as Ollama does: the first request starts
    the load, others wait on it, and if every request waiting is cancelled the load is
    aborted, so the next request starts it again from nothing (measured on Ollama)."""

    name = "fake/cold"

    def __init__(self, load: float) -> None:
        self.load, self.loaded, self.loads, self.waiting = load, False, 0, 0
        self._loading: asyncio.Task | None = None

    async def _ready(self) -> None:
        if self.loaded:
            return
        if self._loading is None:
            self.loads += 1
            self._loading = asyncio.ensure_future(asyncio.sleep(self.load))
        self.waiting += 1
        try:
            await asyncio.shield(self._loading)
            self.loaded = True
        except asyncio.CancelledError:
            if self.waiting == 1 and self._loading is not None and not self._loading.done():
                self._loading.cancel()  # nobody waits for it any more: the load is aborted
                self._loading = None
            raise
        finally:
            self.waiting -= 1

    async def label(self, *, system: str, prompt: str, schema=None) -> str:
        await self._ready()
        return "ok"

    async def write(self, *, system: str, prompt: str) -> str:
        await self._ready()
        # Clearly longer than the 20 ms the reloaded model leaves of the budget below, on
        # Linux's fine timers as on Windows' 15 ms ones (2 ms fitted on Linux).
        await asyncio.sleep(0.05)
        return "Maya and Theo, here we go."


async def prepared_after_warming(deadline: float) -> tuple[ColdServer, LineWriter]:
    server = ColdServer(load=0.3)
    writer = LineWriter(server, REHEARSAL_PAIR, next_pair=NEXT_PAIR, prep_budget=0.32)
    await writer.warm_up(deadline=deadline)
    await writer.prepare()
    return server, writer


async def test_a_warm_up_shorter_than_the_load_aborts_it_and_the_date_falls_back():
    """The old 30 s warm-up against a slower load: cancelled mid-load, the load starts
    again inside the prep budget, and most set pieces get their fallback."""
    server, writer = await prepared_after_warming(0.1)
    written = sum(a.error is None for s in writer.selections for a in s.attempts)
    assert server.loads == 2, "the warm-up's cancel aborted the first load"
    assert writer.budget_exhausted and written < 10, "most of the date's lines never written"


async def test_a_warm_up_held_for_the_whole_load_leaves_prep_its_full_budget():
    server, writer = await prepared_after_warming(1.0)
    assert server.loads == 1 and not writer.budget_exhausted and len(writer.selections) == 10
    assert all(a.error is None for s in writer.selections for a in s.attempts), "none timed out"
