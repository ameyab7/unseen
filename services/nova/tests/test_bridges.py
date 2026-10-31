"""Optional host words must never displace a question or bypass its checks."""

import asyncio
from dataclasses import replace

import pytest

from nova.cards import Seat
from nova.cues import Moment, Say
from nova.date_context import DateContext
from nova.fixtures import REHEARSAL_PAIR
from nova.lines import LineWriter, Source, first_sentence
from nova.meaning import MeaningCheck
from nova.privacy import PrivacyPolicy
from nova.tonight import Tonight


class Model:
    name = "ollama/fake"
    endpoint = "http://localhost:11434"

    def __init__(self, prefix="That sounds like an adventure.", label="fine", delay=0):
        self.prefix, self.answer, self.delay = prefix, label, delay
        self.writes: list[str] = []
        self.labels: list[str] = []

    async def write(self, *, system, prompt):
        self.writes.append(prompt)
        if "optional link" in prompt:
            if self.delay:
                await asyncio.sleep(self.delay)
            return self.prefix
        return getattr(self, "question", "Theo, tell us about your proudest parking moment?")

    async def label(self, *, system, prompt, schema=None):
        self.labels.append(prompt)
        return '{"label": "' + self.answer + '"}'


async def writer(model: Model, *, privacy=None):
    context = DateContext()
    context.heard(Seat.LEFT, "We got lost on a hiking adventure.")
    tonight = Tonight()
    w = LineWriter(
        model,
        REHEARSAL_PAIR,
        context=context,
        tonight=tonight,
        privacy=privacy or PrivacyPolicy(),
        live_timeout=0.1,
        meaning=MeaningCheck(model, timeout=0.1),
        meaning_live=False,
    )
    await w.prepare()
    model.writes.clear()
    model.labels.clear()
    return w, tonight


QUESTION = Say(Moment.FIRST_QUESTION, about=Seat.RIGHT, bridge=True)


async def test_a_live_prefix_keeps_the_exact_prepared_question_and_always_gets_judged():
    model = Model()
    w, _ = await writer(model)
    base = await w.line_for(replace(QUESTION, bridge=False))
    line = await w.line_for(QUESTION)
    assert line.text == f"{model.prefix} {base.text}"
    assert line.from_date and line.source is Source.MODEL
    assert len(model.labels) == 1, "prefix meaning checks do not depend on meaning_live"
    assert "hiking adventure" in model.writes[0]
    assert "hiking adventure" in model.labels[0]


@pytest.mark.parametrize(
    ("prefix", "label", "delay"),
    [
        ("That sounds like an adventure.", "untrue_about_dater", 0),
        ("You both love adventure.", "fine", 0),
        ("How adventurous?", "fine", 0),
        ("One two three four five six seven eight nine.", "fine", 0),
        ("That sounds like an adventure.", "fine", 0.2),
    ],
)
async def test_a_rejected_or_slow_prefix_uses_the_existing_question(prefix, label, delay):
    w, _ = await writer(Model(prefix, label, delay))
    base = await w.line_for(replace(QUESTION, bridge=False))
    line = await w.line_for(QUESTION)
    assert line.text == base.text and line.source is base.source
    assert not line.from_date


async def test_a_remote_model_cannot_get_date_context_without_the_existing_opt_in():
    model = Model()
    model.endpoint = "https://remote.invalid"
    w, _ = await writer(model)
    line = await w.line_for(QUESTION)
    assert not line.from_date
    assert model.writes == [] and model.labels == []


async def test_a_prefix_never_reaches_a_later_dates_memory():
    model = Model()
    w, tonight = await writer(model)
    line = await w.line_for(QUESTION)
    w.played(QUESTION, line)
    assert all(model.prefix not in text for text in tonight.said(Moment.FIRST_QUESTION))


async def test_a_room_transition_keeps_the_question_generated_by_the_existing_reading_path():
    model = Model()
    w, _ = await writer(model)
    question = "What would you bring to a picnic?"
    model.question = question
    line = await w.line_for(Say(Moment.FROM_THE_ROOM, detail=question, bridge=True))
    assert line.from_date and line.text.endswith(question)
    assert len(model.writes) == 2 and len(model.labels) == 1


async def test_clarification_replies_cannot_replace_the_pending_question():
    model = Model()
    w, _ = await writer(model)
    model.prefix = "Theo, what about you?"
    # This model writes the same new question for ordinary replies too.
    line = await w.line_for(
        Say(
            Moment.REPLY, about=Seat.LEFT, detail="Nova, do you mean on holiday?", return_floor=True
        )
    )
    from nova import script

    assert line.text == script.TAKE_YOUR_TIME
    assert line.source is Source.FALLBACK


async def test_fixed_result_and_safety_lines_never_acquire_a_prefix():
    model = Model()
    w, _ = await writer(model)
    line = await w.line_for(Say(Moment.RESULT, text="Thank you, Maya and Theo.", bridge=True))
    assert line.text == "Thank you, Maya and Theo."
    assert model.writes == [] and model.labels == []


async def test_new_speech_cancels_a_prefix_but_keeps_the_required_question():
    from test_rehearsal import Clock, FakeSpeaker

    from nova.rehearsal import Rehearsal

    clock = Clock()
    model = Model(delay=0.2)
    speaker = FakeSpeaker(clock)
    r = Rehearsal(
        REHEARSAL_PAIR,
        model=model,
        speaker=speaker,
        clock=clock,
        voice="fake/voice",
        stt="fake/stt",
        conversational=True,
    )
    await r.prepare()
    r._context.heard(Seat.LEFT, "We got lost on a hiking adventure.")
    base = await r._writer.line_for(replace(QUESTION, bridge=False))
    r._queue_cues([QUESTION])
    await asyncio.sleep(0)
    r.speech_started(Seat.LEFT)
    r.speech_ended(Seat.LEFT)
    clock.now += 21
    await asyncio.wait_for(r.speak_queued(), 0.5)
    assert speaker.texts == [base.text]
    assert not r._optional
    r.close()


async def bridging(clock, model: Model, **options):
    from test_rehearsal import FakeSpeaker

    from nova.rehearsal import Rehearsal

    speaker = FakeSpeaker(clock)
    r = Rehearsal(
        REHEARSAL_PAIR,
        model=model,
        speaker=speaker,
        clock=clock,
        voice="fake/voice",
        stt="fake/stt",
        conversational=True,
        **options,
    )
    await r.prepare()
    r._context.heard(Seat.LEFT, "We got lost on a hiking adventure.")
    return r, speaker


async def test_a_stale_prefix_on_an_overdue_question_waits_only_for_the_brief_opening():
    """Decision 0012: the question alone, in the opening an overdue transition gets, never
    the fixed 1.5 s turn gap a stale prefix used to wait for, which lively talk never left."""
    from test_rehearsal import Clock

    from nova.rehearsal import Prefix

    clock = Clock()
    clock.now = 100
    r, speaker = await bridging(clock, Model(delay=0.2))
    base = await r._writer.line_for(replace(QUESTION, bridge=False))
    r._queue_cues([replace(QUESTION, due=80.0)])  # due 20 s ago: overdue
    await asyncio.sleep(0)
    r.speech_started(Seat.LEFT)  # new speech: the prefix is stale
    r.speech_ended(Seat.LEFT)
    clock.now = 100.45
    await asyncio.wait_for(r.speak_queued(), 0.6)
    assert speaker.texts == [base.text]
    assert r.deliveries[-1].prefix is Prefix.OBSOLETE
    r.close()


async def test_a_prefix_said_with_its_question_is_counted_for_the_rehearsal():
    from test_rehearsal import Clock

    from nova.rehearsal import Prefix, render_report

    clock = Clock()
    r, speaker = await bridging(clock, Model())
    r._queue_cues([replace(QUESTION, due=clock.now)])
    await asyncio.wait_for(r.speak_queued(), 0.5)
    assert speaker.texts[0].startswith("That sounds like an adventure. ")
    assert r.deliveries[-1].prefix is Prefix.SAID
    assert "1 said" in render_report(r)
    r.close()


@pytest.mark.parametrize(
    ("written", "kept"),
    [
        ("The long way home, then.", "The long way home, then."),
        ("From peaks to picnics. Maya, anything you want Theo to know?", "From peaks to picnics."),
        ("Bees on the roof", "Bees on the roof."),
        ("  Bees   on the roof, of course.  ", "Bees on the roof, of course."),
    ],
)
def test_only_a_prefixs_first_sentence_is_kept(written, kept):
    assert first_sentence(written) == kept


async def test_a_prefix_that_says_the_question_again_still_has_it_said_once():
    """The load trial's commonest failure: the model wrote its link, then next_line."""
    question = "Theo, tell us about your proudest parking moment?"
    model = Model(prefix=f"That sounds like an adventure. {question}")
    w, _ = await writer(model)
    line = await w.line_for(QUESTION)
    assert line.prefixed and line.text == f"That sounds like an adventure. {question}"


def test_only_the_turn_just_taken_and_kept_can_be_picked_up():
    context = DateContext()
    context.heard(Seat.LEFT, "We got lost on a hiking adventure.")
    assert context.last_turn().text == "We got lost on a hiking adventure."
    context.heard(Seat.RIGHT, "Mm, yeah.")  # too short to keep: it still came after
    assert context.last_turn() is None
    context.heard(Seat.RIGHT, "I prefer cycling on flat roads.")
    context.spoke(Seat.LEFT)  # a check held her turn back
    assert context.last_turn() is None


async def test_a_prefix_sees_only_what_was_just_said_and_none_when_that_was_held_back():
    model = Model()
    w, _ = await writer(model)
    w._context.heard(Seat.RIGHT, "I learned chess from a library book.")
    line = await w.line_for(QUESTION)
    [prompt] = [p for p in model.writes if "optional link" in p]
    assert line.prefixed and "library book" in prompt and "hiking" not in prompt
    model.writes.clear()
    w._context.spoke(Seat.LEFT)  # the latest turn held back: nothing fresh to pick up
    line = await w.line_for(QUESTION)
    assert not line.prefixed and not any("optional link" in p for p in model.writes)


async def test_a_question_with_no_room_for_a_prefix_never_asks_the_model_for_one():
    """A lightning question is one sentence: any prefix would break its limit, so none is
    written and the model isn't asked (the load trial saw it fail every time)."""
    model = Model()
    w, _ = await writer(model)
    line = await w.line_for(Say(Moment.LIGHTNING_QUESTION, number=1, of=3, bridge=True))
    assert not line.prefixed and not any("optional link" in p for p in model.writes)


async def test_the_rehearsal_spends_its_prefixes_only_where_they_can_be_said():
    from test_rehearsal import Clock

    clock = Clock()
    r, _ = await bridging(clock, Model(), live_bridges=True)
    r.start()
    clock.now = 100
    r._queue_cues([Say(Moment.LIGHTNING_QUESTION, number=1, of=3)])
    r._queue_cues([Say(Moment.FROM_THE_ROOM, detail="Mountains or sea?")])
    lightning, room = [cue for cue in r.cues if isinstance(cue, Say)][-2:]
    assert not lightning.bridge and room.bridge
    r.close()
