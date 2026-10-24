"""The date's soft arc (decision 0005): guidance for each moment's live lines, never a
script. The show runner keeps the running order, its timing and every cue; the arc only
shapes the words of a reply or a silence prompt it already asked for. These are synthetic
checks of the prompts and the flow. They can't show that two people get on: that takes
someone listening to real dates."""

import pytest

from nova import prompts
from nova.cards import Seat
from nova.cues import Moment, Say, Segment
from nova.date_context import DateContext
from nova.decisions import InMemoryDecisionDesk
from nova.distress import DistressCheck
from nova.evaluation import FakeClock, hear_on_air
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.lines import LineWriter
from nova.rehearsal import Rehearsal
from nova.show_runner import NoVotes, ShowRunner
from nova.timing import FULL

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT
NAMES = {LEFT: "Maya", RIGHT: "Theo"}
POTTERY = "I've started doing pottery on Sunday mornings, my bowls are all wonky."
SPOONS = "I've been teaching myself to carve wooden spoons, they're mostly lumpy."
NIGHT_OWL = "I'm a proper night owl, I'm at my best after midnight."
EARLY = "I'm up at six every day, I love an early start."
WORK = "So what do you actually do for work?"
DECLINED = "I'd rather keep work out of it tonight, if that's alright."


class Fake:
    """Writes every line the same way, answers the check per sentence ("no" by default),
    and keeps every prompt."""

    name = "fake/model"

    def __init__(self, labels: dict[str, str] | None = None) -> None:
        self.labels = labels or {}
        self.prompts: list[str] = []

    async def write(self, *, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return "Maya, Theo, over to you."

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        sentence = prompt.split('"sentence": "')[1].split('"')[0]
        return self.labels.get(sentence, "no")


def instructions(prompt: str) -> str:
    return " ".join(prompt.split("<data>")[0].split())


def data(prompt: str) -> str:
    return prompt.split("<data>")[1]


def writer(model: Fake, ctx: DateContext | None = None, segment: Segment | None = None):
    w = LineWriter(model, REHEARSAL_PAIR, next_pair=NEXT_PAIR, live_timeout=1.0, context=ctx)
    if segment is not None:
        w.entered(segment)
    return w


def context(*turns: tuple[Seat, str]) -> DateContext:
    ctx = DateContext()
    for seat, said in turns:
        if said:
            ctx.heard(seat, said)
        else:
            ctx.spoke(seat)
    return ctx


REPLY = Say(Moment.REPLY, about=LEFT, detail="Nova, you won't believe what the dog did next.")
SILENCE = Say(Moment.SILENCE)


# --- A funny story keeps going --------------------------------------------------------------


def test_no_silence_prompt_or_audience_question_cuts_into_a_dater_still_talking():
    """The arc never adds a turn: a dater telling a long story gets no prompt, and the
    audience question waits for them, as the runner always did (decision 0004)."""
    runner = ShowRunner(REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=NoVotes(), first_up=LEFT)
    runner.start(0.0)
    now = FULL.free_talk + 5.0
    runner.tick(now)
    runner.speech_started(now, LEFT)
    said: list[Say] = []
    while now < FULL.from_the_room + 4.0:  # a 2.5-minute story, into the audience questions
        now += 0.5
        said += [cue for cue in runner.tick(now) if isinstance(cue, Say)]
    assert not [s for s in said if s.moment in (Moment.SILENCE, Moment.FROM_THE_ROOM)]


async def test_mid_story_a_reply_is_a_few_words_and_hands_straight_back():
    model = Fake()
    await writer(model, segment=Segment.FREE_TALK).line_for(REPLY)
    told = instructions(model.prompts[-1])
    assert prompts.REPLY_BRIEF.strip() in told
    assert "a few words and hand straight back" in told


# --- Links, differences, short answers, boundaries, nothing in common -----------------------


async def test_a_real_link_between_different_interests_may_be_made():
    model = Fake()
    await writer(model, context((LEFT, POTTERY), (RIGHT, SPOONS)), Segment.FREE_TALK).line_for(
        SILENCE
    )
    told, shown = instructions(model.prompts[-1]), data(model.prompts[-1])
    assert "a link between their answers when there's a real one" in told
    assert "never invent something they share" in told
    assert POTTERY in shown and SPOONS in shown
    assert shown.index('"by": "Maya"') < shown.index(POTTERY) < shown.index('"by": "Theo"')


@pytest.mark.parametrize("say", [REPLY, SILENCE])
async def test_a_difference_between_them_is_never_played_up(say):
    model = Fake()
    await writer(model, context((LEFT, NIGHT_OWL), (RIGHT, EARLY)), Segment.FREE_TALK).line_for(say)
    assert "play up a difference between them" in instructions(model.prompts[-1])


async def test_short_answers_give_nothing_to_build_on_and_an_easier_question():
    model = Fake()
    await writer(model, context((LEFT, "Yeah, fine."), (RIGHT, "Ha.")), Segment.FREE_TALK).line_for(
        SILENCE
    )
    assert "this_date" not in model.prompts[-1], "too short to keep: the cards, as before"
    assert prompts.CARDS in instructions(model.prompts[-1])
    model = Fake()
    three = context((LEFT, "Yeah, pretty good."), (RIGHT, "Not much, really."))
    await writer(model, three, Segment.FREE_TALK).line_for(SILENCE)
    assert "after short answers, ask something easier" in instructions(model.prompts[-1])


async def test_a_calm_boundary_is_never_an_open_loop_for_nova_to_bring_back():
    """Theo asks about work; Maya declines. The check calls a calm boundary a maybe
    (decision 0005), so her words aren't kept, and his question is answered, not open."""
    ctx = DateContext()
    model = Fake({DECLINED: "maybe"})
    check = DistressCheck(model, timeout=1.0)
    await hear_on_air(check, WORK, seat=RIGHT, context=ctx)
    assert [turn.text for turn in ctx.unanswered()] == [WORK], "open, until she speaks"
    await hear_on_air(check, DECLINED, seat=LEFT, other=(WORK,), context=ctx)
    assert ctx.unanswered() == [] and DECLINED not in ctx.spoken()
    await writer(model, ctx, Segment.FREE_TALK).line_for(SILENCE)
    assert "unanswered" not in data(model.prompts[-1])
    assert DECLINED not in model.prompts[-1]
    assert WORK not in model.prompts[-1], "nor the question she declined, among earlier turns"


def test_a_short_answer_is_not_a_declined_question():
    """Too short to keep isn't held back: the question stays among the earlier turns."""
    ctx = context((RIGHT, "Do you like jazz at all?"), (LEFT, "Nope."))
    summary = ctx.summary(NAMES)
    assert summary is not None and summary["earlier"][0]["said"] == "Do you like jazz at all?"
    assert ctx.unanswered() == [], "answered, if briefly"


def test_a_question_answered_with_a_personal_detail_is_hidden_too():
    ctx = context((RIGHT, "Where do you live, roughly?"), (LEFT, "i live on mercer street"))
    assert ctx.summary(NAMES) is None


async def test_with_no_link_between_them_nova_asks_one_open_question():
    model = Fake()
    unrelated = context(
        (LEFT, "My train got cancelled twice this week, it was chaos."),
        (RIGHT, "I've been rewatching a cartoon I loved as a kid."),
    )
    await writer(model, unrelated, Segment.FREE_TALK).line_for(SILENCE)
    told = instructions(model.prompts[-1])
    assert "when nothing links them, one open question" in told
    assert "otherwise on a light detail from each of their blind cards" in told


async def test_deeper_only_when_their_own_answers_open_the_door():
    model = Fake()
    await writer(model, context((LEFT, POTTERY), (RIGHT, SPOONS)), Segment.FREE_TALK).line_for(
        SILENCE
    )
    told = instructions(model.prompts[-1])
    assert "a little deeper only when their own answers have opened that door" in told
    assert "never into anything personal or painful" in told


# --- The moment, as structured context -------------------------------------------------------


@pytest.mark.parametrize(
    ("segment", "aim"),
    [
        (Segment.FIRST_QUESTION, "invite the story behind what they said"),
        (Segment.FREE_TALK, "only when it genuinely fits"),
        (Segment.FROM_THE_ROOM, "only when it genuinely fits"),
        (Segment.LIGHTNING_ROUND, "keep its quick, playful pace"),
        (Segment.LAST_WORD, "don't sum it up or hint at how it went"),
    ],
)
async def test_a_reply_is_told_where_the_date_is_and_the_aim_there(segment, aim):
    model = Fake()
    await writer(model, segment=segment).line_for(REPLY)
    assert aim in instructions(model.prompts[-1])
    assert f'"segment": "{segment.replace("_", " ")}"' in data(model.prompts[-1])


async def test_without_a_segment_the_prompts_are_as_before():
    """The evaluation's older cases, and anything that never hears a segment."""
    told, untold = Fake(), Fake()
    await writer(told).line_for(REPLY)
    await writer(untold).line_for(REPLY)
    assert told.prompts == untold.prompts and '"moment"' not in told.prompts[0]
    assert prompts.REPLY_BRIEF.strip() not in instructions(told.prompts[0])


async def test_the_arc_shapes_words_never_set_pieces_turns_or_model_calls():
    """Set pieces are written before the date: a segment changes none of their prompts, and
    a live cue still makes exactly one model call."""
    for say in (Say(Moment.DOORS_INTRO), Say(Moment.LAST_WORD, about=LEFT), Say(Moment.OUTRO)):
        plain, entered = Fake(), Fake()
        await writer(plain).attempt_for(say)
        await writer(entered, segment=Segment.LAST_WORD).attempt_for(say)
        assert plain.prompts == entered.prompts, say.moment
    model = Fake()
    await writer(model, segment=Segment.FREE_TALK).line_for(REPLY)
    assert len(model.prompts) == 1


def test_the_first_question_asks_for_a_story_or_an_opinion():
    told = instructions(prompts.first_question(REHEARSAL_PAIR, LEFT))
    assert "Ask for a story or an opinion, not a one-word fact" in told


class Speaker:
    async def speak(self, text: str) -> float | None:
        return 0.0

    def stop(self) -> None:
        pass


async def test_the_rehearsal_tells_the_writer_each_segment_as_the_runner_starts_it():
    clock = FakeClock()
    r = Rehearsal(
        REHEARSAL_PAIR,
        model=Fake(),
        speaker=Speaker(),
        voice="fake/voice",
        stt="deepgram/nova-3",
        clock=clock,
        timing=FULL,
        next_pair=NEXT_PAIR,
        first_up=LEFT,
    )
    r.start()
    seen: list[Segment | None] = []
    while clock.now < FULL.lightning + 5.0:
        clock.now += 0.5
        r.tick()
        await r.speak_queued()
        if r._writer.segment not in seen:
            seen.append(r._writer.segment)
    assert seen[:4] == [
        Segment.DOORS,
        Segment.FIRST_QUESTION,
        Segment.FREE_TALK,
        Segment.FROM_THE_ROOM,
    ]
