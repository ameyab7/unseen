"""Safety checks before lines on a shared model (nova/priority.py), and requests that run
out of time sent once, never again (decision 0005). Fake models, a real event loop."""

import asyncio

from nova.cues import Moment, Say
from nova.distress import CheckError, DistressCheck
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.lines import ErrorKind, LineWriter, Source
from nova.priority import SafetyFirst
from nova.rehearsal import Rehearsal


class Slot:
    """One request at a time, as Ollama serves them: lines and checks, in the order they
    arrive, each taking `takes` seconds. Records the order they ran in."""

    name = "ollama/fake"

    def __init__(self, *, takes: float = 0.05) -> None:
        self.takes = takes
        self.ran: list[str] = []
        self.writes = self.labels = 0
        self._one = asyncio.Lock()

    async def write(self, *, system: str, prompt: str) -> str:
        self.writes += 1
        async with self._one:
            self.ran.append("line")
            await asyncio.sleep(self.takes)
        return "Maya, Theo, over to you."

    async def label(self, *, system: str, prompt: str, schema=None) -> str:
        self.labels += 1
        async with self._one:
            self.ran.append("check")
            await asyncio.sleep(self.takes)
        return '{"label": "no", "conduct": "none"}'


async def test_a_check_waiting_goes_before_every_line_not_yet_sent():
    """Without the gate, a check queues behind every line already waiting for the model."""
    gate, slot = SafetyFirst(), Slot()
    writer = LineWriter(slot, REHEARSAL_PAIR, next_pair=NEXT_PAIR, live_timeout=5.0, gate=gate)
    check = DistressCheck(slot, timeout=5.0, gate=gate)
    first = asyncio.ensure_future(writer.line_for(Say(Moment.SILENCE)))
    await asyncio.sleep(0.01)  # the first line is running
    checked = asyncio.ensure_future(check.check("I had a lovely week, thanks."))
    await asyncio.sleep(0)
    others = [asyncio.ensure_future(writer.line_for(Say(Moment.SILENCE))) for _ in range(3)]
    await asyncio.gather(first, checked, *others)
    assert slot.ran == ["line", "check", "line", "line", "line"], "after the one running only"


async def test_without_checks_lines_go_straight_through():
    gate, slot = SafetyFirst(), Slot(takes=0.0)
    writer = LineWriter(slot, REHEARSAL_PAIR, next_pair=NEXT_PAIR, live_timeout=1.0, gate=gate)
    line = await writer.line_for(Say(Moment.SILENCE))
    assert line.source is Source.MODEL and gate.checking == 0


async def test_a_line_waiting_on_checks_waits_inside_its_own_timeout():
    """A slow check can cost a line its model words, the fallback, never a check its
    verdict."""
    gate, slot = SafetyFirst(), Slot(takes=0.3)
    writer = LineWriter(slot, REHEARSAL_PAIR, next_pair=NEXT_PAIR, live_timeout=0.1, gate=gate)
    check = DistressCheck(slot, timeout=2.0, gate=gate)
    checked = asyncio.ensure_future(check.check("I had a lovely week, thanks."))
    await asyncio.sleep(0)
    line = await writer.line_for(Say(Moment.SILENCE))
    assert line.source is Source.FALLBACK and line.error is ErrorKind.TIMEOUT
    assert (await checked).error is None, "the check got its verdict"
    assert slot.writes == 0, "the line never reached the model"


def test_a_rehearsal_gates_only_models_that_share_one_ollama():
    class Model:
        def __init__(self, name: str) -> None:
            self.name = name

    class Voice:
        async def speak(self, text: str) -> float | None:
            return 0.0

        def stop(self) -> None:
            pass

    def gate(model: str, classifier: str | None) -> bool:
        r = Rehearsal(
            REHEARSAL_PAIR,
            model=Model(model),
            classifier=Model(classifier) if classifier else None,
            speaker=Voice(),
            voice="fake",
            stt="deepgram/nova-3",
        )
        return r._gate is not None

    assert gate("ollama/gemma4:26b", None) and gate("ollama/gemma4:26b", "ollama/small")
    assert not gate("anthropic/claude-haiku-5-5", None)
    assert not gate("ollama/gemma4:26b", "anthropic/claude-haiku-5-5")


# --- A request that runs out of time is sent once (decision 0005) -----------------------------


class Hangs:
    """Never answers; counts what was asked."""

    name = "fake/hangs"

    def __init__(self) -> None:
        self.writes = self.labels = 0

    async def write(self, *, system: str, prompt: str) -> str:
        self.writes += 1
        await asyncio.Event().wait()
        return ""

    async def label(self, *, system: str, prompt: str, schema=None) -> str:
        self.labels += 1
        await asyncio.Event().wait()
        return ""


async def test_a_live_line_that_times_out_is_never_asked_for_again():
    """With Claude, an abandoned request may still run and be billed (decision 0005): it
    is never followed by a second one for the same line."""
    model = Hangs()
    writer = LineWriter(model, REHEARSAL_PAIR, next_pair=NEXT_PAIR, live_timeout=0.05)
    line = await writer.line_for(Say(Moment.SILENCE))
    assert line.source is Source.FALLBACK and line.error is ErrorKind.TIMEOUT
    assert model.writes == 1


async def test_a_check_that_times_out_is_never_sent_again():
    model = Hangs()
    checked = await DistressCheck(model, timeout=0.05).check("I had a lovely week, thanks.")
    assert checked.error is CheckError.TIMEOUT and model.labels == 1


async def test_set_pieces_that_run_out_of_budget_are_never_asked_for_again():
    """Three candidates for each set piece, once each: when the budget runs out, every
    piece not chosen gets its fallback and nothing is sent again."""
    model = Hangs()
    writer = LineWriter(
        model, REHEARSAL_PAIR, next_pair=NEXT_PAIR, prep_timeout=5.0, prep_budget=0.1
    )
    await writer.prepare()
    assert writer.budget_exhausted and writer.all_fallbacks
    assert model.writes <= 3 * len(writer.selections or [None] * 10)
    sent = model.writes
    await asyncio.sleep(0.05)
    assert model.writes == sent, "nothing more after the budget"
