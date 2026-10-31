"""A show's night (nova/show.py, decision 0011): every date gets exactly one context of its
own, one date is live at a time in a show, two shows never meet, and however a date ends
its context is emptied and closed, so nothing said on it reaches another date. No LiveKit,
no network: a fake model that may see this date (a loopback endpoint) keeps every prompt."""

import asyncio
import contextlib
from pathlib import Path

import pytest

from nova.cards import Seat
from nova.config import Settings
from nova.distress import LIGHT_SYSTEM
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.meaning import MEANING_SYSTEM
from nova.rehearsal import Rehearsal
from nova.show import DateInProgress, Night
from nova.timing import REHEARSAL

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT
# Made-up words, so a match can only be a leak.
FIRST = ("I keep a jar of Quillfeather buttons on my desk at home.", "Quillfeather")
SECOND = ("Last summer I sailed all the way round Brindlecove.", "Brindlecove")
OTHER = ("My grandmother taught me to bake Marrowdale bread.", "Marrowdale")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Voice:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    async def speak(self, text: str) -> float | None:
        self.clock.now += 0.3
        return self.clock.now

    def stop(self) -> None:
        pass


class Model:
    """On this machine, so it may see this date (nova/privacy.py): writes one line, finds
    no distress in anything, and keeps every prompt it's given. gate: checks wait for it."""

    name = "fake/model"
    endpoint = "http://localhost:11434"

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.prompts: list[str] = []
        self.gate = gate

    async def write(self, *, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return "Maya, Theo, over to you."

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        if system == MEANING_SYSTEM:
            return '{"label": "fine"}'
        if system == LIGHT_SYSTEM:
            return '{"light": false}'
        if '"sentence": "' not in prompt:
            return "ok"  # the warm-up
        if self.gate is not None:
            await self.gate.wait()
        return "no"


def options(clock: Clock, model: Model, **more) -> dict:
    return {
        "model": model,
        "speaker": Voice(clock),
        "clock": clock,
        "timing": REHEARSAL,
        "first_up": LEFT,
        "voice": "fake/voice",
        "stt": "deepgram/nova-3",
        **more,
    }


async def speaks(r: Rehearsal, clock: Clock, who: str, words: str) -> None:
    """One dater says something and stops; Nova acts on it once the grace has passed. The
    chat names seats "maya" and "theo", whoever the daters are."""
    r.command(who)
    clock.now += 2.0
    r.speech_started()
    clock.now += 1.0
    r.transcript(words)
    r.speech_ended()
    clock.now += Settings.utterance_grace + 0.2
    r.tick()
    await r.heard()
    await r.speak_queued()


async def into_free_talk(r: Rehearsal, clock: Clock) -> None:
    """Set pieces written, the date started, both first questions asked."""
    await r.warm_up(deadline=1.0)
    await r.prepare()
    r.start()
    await r.speak_queued()
    for at in (40.0, 40.0 + REHEARSAL.answer_window + 1.0):
        clock.now = at
        r.tick()
        await r.speak_queued()
    clock.now = 50.0


async def a_date(r: Rehearsal, clock: Clock, *said: str) -> None:
    """Free talk, the daters saying each of `said` in turn, then a question to Nova."""
    await into_free_talk(r, clock)
    for k, words in enumerate(said):
        await speaks(r, clock, ("maya", "theo")[k % 2], words)
    await speaks(r, clock, "maya", "Nova, what should I ask about next?")


def replies(model: Model) -> list[str]:
    """The prompts that saw this date."""
    return [prompt for prompt in model.prompts if '"this_date"' in prompt]


# --- One context a date ---------------------------------------------------------------------


async def test_each_date_gets_one_context_of_its_own_made_as_it_opens():
    night = Night()
    with night.date(REHEARSAL_PAIR, **options(Clock(), Model())) as first:
        assert first._context is not None and len(first._context) == 0
        assert night.live is first and night.dates == 1
        kept = first._context
    with night.date(NEXT_PAIR, **options(Clock(), Model())) as second:
        assert second._context is not None and second._context is not kept
        assert second._writer._tonight is first._writer._tonight is night.tonight
    assert night.live is None and night.dates == 2


async def test_a_show_runs_one_date_at_a_time():
    night = Night()
    with night.date(REHEARSAL_PAIR, **options(Clock(), Model())), pytest.raises(DateInProgress):
        night.date(NEXT_PAIR, **options(Clock(), Model())).__enter__()
    with night.date(NEXT_PAIR, **options(Clock(), Model())):  # once the first has ended
        pass


async def test_two_shows_at_once_never_share_what_their_daters_said():
    """Two shows in one process (two cities, one worker), their dates running at the same
    time and interleaved: each reply sees its own date's turns and nobody else's."""

    async def show(said: tuple[str, str], other: tuple[str, str]) -> Model:
        clock, model = Clock(), Model()
        with Night().date(REHEARSAL_PAIR, **options(clock, model)) as r:
            await a_date(r, clock, said[0], "That sounds like a lovely thing to collect.")
        seen = replies(model)
        assert seen and said[1] in seen[-1] and other[1] not in seen[-1]
        return model

    await asyncio.gather(show(FIRST, SECOND), show(SECOND, FIRST))


# --- However a date ends ----------------------------------------------------------------------


ENDINGS = ["ran", "the moderator ended it", "never opened", "error", "cancelled"]


@pytest.mark.parametrize("ending", ENDINGS)
async def test_however_a_date_ends_its_context_is_emptied_and_closed(ending):
    night = Night()
    clock = Clock()
    seen: list[Rehearsal] = []

    async def go() -> None:
        with night.date(REHEARSAL_PAIR, **options(clock, Model())) as r:
            seen.append(r)
            if ending == "never opened":  # heard while Nova got ready, then ended
                await r.warm_up(deadline=1.0)
                await r.prepare()
                await speaks(r, clock, "maya", FIRST[0])
                assert not r.started and len(r._context) == 1
                r.command("end")
                return
            await into_free_talk(r, clock)
            await speaks(r, clock, "maya", FIRST[0])
            assert len(r._context) == 1, "heard while the date ran"
            match ending:
                case "the moderator ended it":
                    r.command("end")
                case "error":
                    raise RuntimeError("the session dropped")
                case "cancelled":
                    await asyncio.Event().wait()

    task = asyncio.ensure_future(go())
    if ending == "cancelled":
        while not seen or not len(seen[0]._context):
            await asyncio.sleep(0.01)
        task.cancel()
    raised = {"error": RuntimeError, "cancelled": asyncio.CancelledError}.get(ending)
    with pytest.raises(raised) if raised else contextlib.nullcontext():
        await task
    (r,) = seen
    context = r._context
    assert context.closed and len(context) == 0 and r.closed and night.live is None
    assert not context.heard(LEFT, OTHER[0]), "nothing joins a closed date"
    assert context.summary({LEFT: "Maya", RIGHT: "Theo"}) is None
    planned = [line for kept in night.tonight._lines.values() for line in kept if line.planned]
    assert planned == [], "the set pieces it never said left tonight's memory"


async def test_a_check_that_finishes_after_the_date_ended_never_adds_to_it():
    gate = asyncio.Event()
    clock = Clock()
    night = Night()
    with night.date(REHEARSAL_PAIR, **options(clock, Model(gate))) as r:
        await into_free_talk(r, clock)
        r.command("maya")
        r.speech_started()
        r.transcript(FIRST[0])
        r.speech_ended()
        clock.now += Settings.utterance_grace + 0.2
        r.tick()  # the check starts, and waits for the gate
        await asyncio.sleep(0.01)
    gate.set()
    await asyncio.sleep(0.01)
    assert len(r._context) == 0 and r._context.closed


# --- From one date to the next ----------------------------------------------------------------


async def test_nothing_said_on_one_date_reaches_the_next():
    """Only Nova's own lines, names masked, carry over (tonight's memory): never a word a
    dater said, nor their names."""
    night = Night()
    clock, first = Clock(), Model()
    with night.date(REHEARSAL_PAIR, **options(clock, first, next_pair=NEXT_PAIR)) as r:
        await a_date(r, clock, FIRST[0], SECOND[0])
    assert any(FIRST[1] in p and SECOND[1] in p for p in replies(first)), "date one saw them"

    clock, second = Clock(), Model()
    with night.date(NEXT_PAIR, **options(clock, second)) as r:
        await a_date(r, clock, OTHER[0], "That must have smelled wonderful, honestly.")
    assert replies(second) and all(OTHER[1] in p for p in replies(second)[-1:])
    for prompt in second.prompts:
        assert FIRST[1] not in prompt and SECOND[1] not in prompt
        assert "Maya" not in prompt and "Theo" not in prompt


async def test_acknowledgements_and_personal_details_never_take_a_turns_place():
    """Through the whole date: an acknowledgement says only that the dater answered, a
    personal detail only that they spoke; neither is shown, and the turns that say
    something stay."""
    clock, model = Clock(), Model()
    with Night().date(REHEARSAL_PAIR, **options(clock, model)) as r:
        await a_date(
            r,
            clock,
            FIRST[0],
            "Ha, so true.",
            SECOND[0],
            "My number is 07700 900123, text me.",
        )
    shown = replies(model)[-1]
    assert FIRST[1] in shown and SECOND[1] in shown
    assert "so true" not in shown and "07700" not in shown


def test_the_agent_opens_its_one_date_through_a_night():
    """The same contract for the rehearsal as for the real show (tests/test_agent.py runs
    it, in Docker)."""
    agent = (Path(__file__).parents[1] / "nova" / "agent.py").read_text(encoding="utf-8")
    assert "night = Night(make=Rehearsal)" in agent and "with night.date(" in agent
    assert "Rehearsal(\n" not in agent, "no date made any other way"
