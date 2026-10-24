"""Choosing among a set piece's candidates (nova/quality.py, decision 0005): the guardrails
are a hard gate, then the line that does its moment's job best, then the least repetitive,
then the first written. Fixed synthetic candidates, so every choice here is exact."""

import pytest

from nova import script
from nova.cards import Seat
from nova.cues import Moment, Say
from nova.evaluation import render_night, run_night
from nova.fixtures import MAYA, NEXT_PAIR, REHEARSAL_PAIR
from nova.lines import CANDIDATES, ErrorKind, LineWriter, Source
from nova.quality import Fault, Scored, best, content_words, faults, least_similar
from nova.tonight import Tonight

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT
MAYAS_FIRST = Say(Moment.FIRST_QUESTION, about=LEFT, detail=MAYA.prompts[0].text)
NAMES = ("Maya", "Theo", "Sam", "Priya")


class Candidates:
    """Writes the given candidates in turn, for every request, and counts the requests."""

    name = "fake/model"

    def __init__(self, *lines: str) -> None:
        self.lines, self.calls = lines, 0

    async def write(self, *, system: str, prompt: str) -> str:
        line = self.lines[self.calls % len(self.lines)]
        self.calls += 1
        return line


async def chosen(*lines: str, say: Say = MAYAS_FIRST, tonight: Tonight | None = None):
    writer = LineWriter(
        Candidates(*lines), REHEARSAL_PAIR, next_pair=NEXT_PAIR, prep_timeout=1.0, tonight=tonight
    )
    line = await writer._prepare_one(say)
    return line, writer.selections[-1]


def heard(*lines: str, moment: Moment = Moment.FIRST_QUESTION) -> Tonight:
    tonight = Tonight()
    for line in lines:
        tonight.remember(moment, line, names=NAMES)
    return tonight


# --- Quality first, novelty second ------------------------------------------------------------

RELEVANT = "Maya, tell us about your worst haircut. How bad are we talking?"
# Never said tonight, and nothing to do with her card.
NOVEL = "Maya, what colour would you paint a lighthouse?"


async def test_a_relevant_natural_line_beats_an_unrelated_novel_one():
    """Tonight already heard a haircut question, so the old rule kept the novel line."""
    tonight = heard("Sam, tell us about your worst haircut. Was it a home job?")
    line, selection = await chosen(NOVEL, RELEVANT, NOVEL, tonight=tonight)
    assert line.text == RELEVANT and line.source is Source.PREPARED
    assert selection.scored[0].closeness < selection.scored[1].closeness, "the novel one is newer"
    assert Fault.NOT_BUILT_ON_CARD in selection.scored[0].faults
    assert least_similar(selection.scored) == 0, "the old rule would have kept the novel line"


def test_high_novelty_never_outranks_fewer_faults():
    weak_but_new = Scored(ok=True, faults=(Fault.NOT_BUILT_ON_CARD,), closeness=0.0)
    good_but_heard = Scored(ok=True, faults=(), closeness=0.95, same_opening=True)
    assert best([weak_but_new, good_but_heard]) == 1


# --- The hard gates ---------------------------------------------------------------------------


async def test_a_candidate_that_breaks_a_guardrail_is_never_chosen():
    unsafe = "Maya, I bet Theo chose reveal already. Tell us about your worst haircut?"
    soft = "Maya, your hair, then: what happened?"  # passes, but not built on her card
    line, selection = await chosen(unsafe, soft, unsafe)
    assert not selection.scored[0].ok and not selection.scored[2].ok
    assert line.text == soft


async def test_a_line_that_makes_up_a_show_fact_is_never_chosen_even_if_freshest():
    say = Say(Moment.LAST_WORD, about=RIGHT)
    made_up = "Theo, one last thing for Maya before we take a break?"
    fine = "Theo, twenty seconds: what should Maya know about you?"
    tonight = heard("Maya, twenty seconds: what should Theo know about you?", moment=say.moment)
    line, selection = await chosen(made_up, fine, made_up, say=say, tonight=tonight)
    assert Fault.INVENTS_SHOW_FACT in selection.scored[0].faults
    assert selection.scored[0].closeness < selection.scored[1].closeness
    assert line.text == fine


def test_a_candidate_that_failed_the_guardrails_never_wins_on_any_score():
    failed = Scored(ok=False, closeness=0.0)
    severe = Scored(ok=True, faults=(Fault.ASKS_TOO_SOON,), closeness=0.0)
    passing = Scored(ok=True, faults=(Fault.TOO_LONG, Fault.EMPTY_PRAISE), closeness=1.0)
    assert best([failed, severe, passing]) == 2
    assert best([failed, severe]) is None


# --- Repetition still counts ------------------------------------------------------------------


async def test_between_equally_good_lines_the_one_less_like_tonight_wins():
    said = "Maya, tell us about your worst haircut. How bad are we talking?"
    fresh = "Maya, the worst haircut of your life: who was holding the scissors?"
    line, _ = await chosen(said, fresh, said, tonight=heard(said))
    assert line.text == fresh


def test_a_repeated_opening_breaks_a_near_tie_but_never_a_clear_one():
    near_tie = [
        Scored(ok=True, closeness=0.30, same_opening=True),
        Scored(ok=True, closeness=0.35, same_opening=False),
    ]
    assert best(near_tie) == 1
    clear = [
        Scored(ok=True, closeness=0.10, same_opening=True),
        Scored(ok=True, closeness=0.80, same_opening=False),
    ]
    assert best(clear) == 0


# --- Deterministic, and safe when nothing qualifies --------------------------------------------


async def test_ties_go_to_the_first_written_every_time():
    a = "Maya, tell us about your worst haircut. How bad was it?"
    b = "Maya, your worst haircut: who did it, and why?"
    picks = {(await chosen(a, b, a))[0].text for _ in range(5)}
    assert picks == {a}
    assert best([Scored(ok=True), Scored(ok=True), Scored(ok=True)]) == 0


async def test_when_no_candidate_passes_the_guardrails_the_approved_fallback_is_said():
    line, selection = await chosen("I'm human, honestly, Maya. Theo chose reveal.")
    assert selection.chosen is None and line.source is Source.FALLBACK
    assert line.text == script.first_question_fallback(MAYA)
    assert line.violations, "the reasons, as before"


async def test_when_every_passing_candidate_is_severe_the_approved_fallback_is_said():
    say = Say(Moment.DOORS_INTRO)
    asks = "Good evening, room. On my left, Maya, 29. On my right, Theo, 31. Maya, ready?"
    line, selection = await chosen(asks, say=say)
    assert all(s.ok and Fault.ASKS_TOO_SOON in s.faults for s in selection.scored)
    assert line.source is Source.FALLBACK and line.error is ErrorKind.QUALITY
    assert line.text == script.doors_fallback(REHEARSAL_PAIR)


async def test_choosing_makes_no_model_call_and_keeps_the_candidate_count():
    model = Candidates(RELEVANT)
    writer = LineWriter(model, REHEARSAL_PAIR, next_pair=NEXT_PAIR, prep_timeout=1.0)
    await writer.prepare()
    assert model.calls == CANDIDATES * len(writer.selections)


# --- The checks themselves ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("card", "said"),
    [("juggle", "how did you get into juggling?"), ("swim", "those winter swims")],
)
def test_a_word_and_its_forms_count_as_the_same_word(card, said):
    assert content_words(card) & content_words(said)


@pytest.mark.parametrize(
    ("say", "line", "fault"),
    [
        (
            MAYAS_FIRST,
            "Maya, that's interesting: tell us about your worst haircut?",
            Fault.EMPTY_PRAISE,
        ),
        (
            MAYAS_FIRST,
            "Maya, your worst haircut: who did it? When? Did you cry?",
            Fault.TOO_MANY_QUESTIONS,
        ),
        (
            MAYAS_FIRST,
            "Maya, tell us everything about the worst haircut you have ever had in your whole "
            "life, from start to finish please.",
            Fault.TOO_LONG,
        ),
        (Say(Moment.OUTRO), "Thank you both, Maya.", Fault.NOT_BOTH_NAMED),
    ],
)
def test_each_soft_fault(say, line, fault):
    assert fault in faults(say, line, pair=REHEARSAL_PAIR)


async def test_a_night_reports_how_the_ranking_chose_against_the_old_rule():
    night = await run_night(Candidates(RELEVANT), live_timeout=1.0, prep_timeout=1.0)
    assert len(night.choices) == len(night.prep_ms) * 10 - 1, "every set piece; no Next up last"
    report = render_night(night, model_name="fake")
    assert "Choosing among candidates:" in report
    assert "quality ranking" in report and "least similar" in report
