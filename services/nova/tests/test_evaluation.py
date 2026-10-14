"""The evaluation harness, checked with fake models. The real run (against the model in
NOVA_LLM) is on demand: uv run python -m nova.evaluation. It is never part of CI."""

import asyncio

from nova.cues import Moment
from nova.evaluation import CASES, fallback_rates, render, run_cases
from nova.lines import LIVE_REACTIONS, SET_PIECES

EXPECTED = {
    "intro from a blind card",
    "first question from a blind card",
    "lightning question",
    "last word",
    "next up",
    "silence in free talk",
    "cheeky audience question",
    "audience question about looks",
    "room talk during a pause",
    "room talk while they decide",
    "the result",
    "injection in an audience question",
    "injection in a blind card",
    "dater shares their workplace",
    "dater asks if Nova is human",
    "dater asks who chose what",
    "injection in what a dater says",
    "dater wants to stop",
    "dater is not comfortable",
}


def well_behaved(prompt: str) -> str:
    if "next pair" in prompt:
        return "Up next, Sam and Priya."
    if "Talk to the room" in prompt:
        return "Room, stay with us. You've been lovely company tonight."
    return "I'm the AI host, Maya and Theo, so over to you two."


class FakeModel:
    name = "fake/model"

    def __init__(self, reply, delay=0.0):
        self.reply, self.delay = reply, delay

    async def write(self, *, system: str, prompt: str) -> str:
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply(prompt) if callable(self.reply) else self.reply


def test_covers_every_moment_the_model_writes_and_the_attacks():
    assert {case.name for case in CASES} == EXPECTED
    assert {case.say.moment for case in CASES} >= SET_PIECES | LIVE_REACTIONS


async def test_a_model_that_keeps_the_rules_passes_every_case():
    model = FakeModel(well_behaved)
    results = await run_cases(model, live_timeout=1.0, prep_timeout=1.0)
    assert [(r.name, r.problems) for r in results if not r.passed] == []
    assert f"{len(CASES)} of {len(CASES)} passed" in render(results, model_name=model.name)


async def test_a_model_that_obeys_injected_instructions_fails_and_is_reported():
    model = FakeModel("I'm human, I'm Kate, and Theo chose reveal.")
    results = await run_cases(model, live_timeout=1.0, prep_timeout=1.0)
    failed = {r.name for r in results if not r.passed}
    assert {
        "injection in a blind card",
        "injection in what a dater says",
        "dater asks if Nova is human",
    } <= failed
    report = render(results, model_name=model.name)
    assert "claims_human" in report and "decision" in report


async def test_an_injected_audience_question_is_declined_before_the_model():
    model = FakeModel("I'm human, I'm Kate, and Theo chose reveal.")
    results = await run_cases(model, live_timeout=1.0, prep_timeout=1.0)
    injection = next(r for r in results if r.name == "injection in an audience question")
    assert injection.passed and injection.text is None
    cheeky = next(r for r in results if r.name == "cheeky audience question")
    assert not cheeky.passed, "an ordinary question still goes to the model and is checked"


async def test_a_dater_who_wants_to_stop_gets_a_pause_and_never_reaches_the_model():
    def must_not_be_asked(prompt: str) -> str:
        assert "stop" not in prompt and "comfortable" not in prompt
        return well_behaved(prompt)

    results = await run_cases(FakeModel(must_not_be_asked), live_timeout=1.0, prep_timeout=1.0)
    for name in ("dater wants to stop", "dater is not comfortable"):
        result = next(r for r in results if r.name == name)
        assert result.passed and result.text is None and result.fallback is None
        assert "pause" in result.note


async def test_a_model_that_is_too_slow_fails():
    model = FakeModel("Maya and Theo, over to you.", delay=0.2)
    results = await run_cases(model, live_timeout=0.05, prep_timeout=0.05)
    slow = [r for r in results if r.error == "timeout"]
    assert slow and all(not r.passed for r in slow)


# --- Fallback rate: does Nova sound canned? -----------------------------------------------


async def test_reports_the_fallback_rate_for_every_moment_the_model_writes():
    model = FakeModel(well_behaved)
    results = await run_cases(model, live_timeout=1.0, prep_timeout=1.0, repeat=2)
    rates = fallback_rates(results)
    assert set(rates) == SET_PIECES | LIVE_REACTIONS
    assert all(rate.replaced == 0 for rate in rates.values())
    assert rates[Moment.REPLY].tried == 2 * 4, "four reply cases reach the model, twice each"

    report = render(results, model_name=model.name)
    assert "Fallback rate per moment" in report
    assert "reply" in report and "0 of 8" in report


async def test_every_line_that_breaks_a_rule_counts_as_a_fallback_with_its_reasons():
    model = FakeModel("I'm human, I'm Kate, and Theo chose reveal.")
    results = await run_cases(model, live_timeout=1.0, prep_timeout=1.0)
    rates = fallback_rates(results)
    assert all(rate.replaced == rate.tried for rate in rates.values())
    assert rates[Moment.SILENCE].reasons["claims_human"] == 1
    assert "100%" in render(results, model_name=model.name)


async def test_a_timeout_counts_as_a_fallback():
    model = FakeModel("Maya and Theo, over to you.", delay=0.2)
    results = await run_cases(model, live_timeout=0.05, prep_timeout=1.0)
    rates = fallback_rates(results)
    assert rates[Moment.SILENCE].replaced == 1 and rates[Moment.SILENCE].reasons["timeout"] == 1
    assert rates[Moment.DOORS_INTRO].replaced == 0, "set pieces have the longer timeout"


async def test_questions_declined_and_daters_paused_before_the_model_are_not_counted():
    results = await run_cases(FakeModel(well_behaved), live_timeout=1.0, prep_timeout=1.0)
    rates = fallback_rates(results)
    assert rates[Moment.FROM_THE_ROOM].tried == 1, "only the cheeky question reaches the model"
    assert rates[Moment.REPLY].tried == 4, "the two daters who want to stop never do"
