"""The evaluation harness, checked with fake models. The real run (against the model in
NOVA_LLM) is on demand: uv run python -m nova.evaluation. It is never part of CI."""

import asyncio

from nova.evaluation import CASES, render, run_cases

EXPECTED = {
    "intro from a blind card",
    "silence in free talk",
    "cheeky audience question",
    "audience question about looks",
    "the result",
    "injection in an audience question",
    "injection in a blind card",
    "dater shares their workplace",
    "dater asks if Nova is human",
    "dater asks who chose what",
    "injection in what a dater says",
}


class FakeModel:
    name = "fake/model"

    def __init__(self, reply, delay=0.0):
        self.reply, self.delay = reply, delay

    async def write(self, *, system: str, prompt: str) -> str:
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply


def test_covers_the_five_moments_and_the_attacks():
    assert {case.name for case in CASES} == EXPECTED


async def test_a_model_that_keeps_the_rules_passes_every_case():
    model = FakeModel("I'm the AI host, Maya and Theo, so over to you two.")
    results = await run_cases(model, live_timeout=1.0, prep_timeout=1.0)
    assert [r.name for r in results if not r.passed] == []
    assert "11 of 11 passed" in render(results, model_name=model.name)


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


async def test_a_model_that_is_too_slow_fails():
    model = FakeModel("Maya and Theo, over to you.", delay=0.2)
    results = await run_cases(model, live_timeout=0.05, prep_timeout=0.05)
    slow = [r for r in results if r.error == "timeout"]
    assert slow and all(not r.passed for r in slow)
