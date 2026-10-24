"""The meaning check's labelled set (nova/meaning_set.py) and its evaluation: every
category in both halves, the halves apart, and the gate. Held-out lines are never written
out here, and every check on them is a plain boolean or a count."""

import pytest

from nova import evaluation
from nova.evaluation import (
    MEANING_DEV,
    MEANING_HELD_OUT,
    main,
    meaning_misses,
    render_meaning,
    run_meaning,
)
from nova.meaning import MEANING_SYSTEM, Meaning
from nova.meaning_set import DEV_HALF, HELD_OUT_HALF
from nova.tonight import similarity

NEAR = 0.6  # the dev sets' guard against paraphrasing a held-out sentence


@pytest.mark.parametrize("half", [DEV_HALF, HELD_OUT_HALF], ids=["dev", "held-out"])
def test_each_half_has_every_category_and_lines_that_should_pass(half):
    labels = {s.expected for s in half}
    assert labels == set(Meaning) - {Meaning.UNCHECKED}
    fine = sum(s.expected is Meaning.FINE for s in half)
    assert fine >= 10 and fine / len(half) >= 0.3, "false rejections need a measure too"


def test_the_halves_never_paraphrase_each_other():
    dev = [s.line for s in DEV_HALF]
    close = [
        k for k, s in enumerate(HELD_OUT_HALF) if max(similarity(s.line, d) for d in dev) >= NEAR
    ]
    assert not close, f"held-out lines at positions {close} paraphrase a dev line"


def test_no_held_out_line_appears_in_the_checks_rules():
    assert not any(s.line in MEANING_SYSTEM for s in HELD_OUT_HALF)


def answering(labels) -> object:
    """A fake classifier that answers the meaning check from the set's own labels."""
    by_line = {s.line: s.expected.value for half in (DEV_HALF, HELD_OUT_HALF) for s in half}

    class Labeller:
        name = "fake/labeller"

        async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
            line = next(text for text in by_line if f'"line": {_json(text)}' in prompt)
            return f'{{"label": "{labels(by_line[line])}"}}'

    return Labeller()


def _json(text: str) -> str:
    import json

    return json.dumps(text)


async def test_a_check_that_is_always_right_passes_the_gate():
    results = await run_meaning(answering(lambda right: right), timeout=1.0, samples=DEV_HALF)
    assert meaning_misses(results) == []
    assert "PASSED" in render_meaning(results, model_name="fake", set_name=MEANING_DEV)


async def test_a_check_that_passes_everything_misses_every_flagged_line():
    results = await run_meaning(answering(lambda _: "fine"), timeout=1.0, samples=DEV_HALF)
    flagged = sum(s.expected is not Meaning.FINE for s in DEV_HALF)
    assert meaning_misses(results) == [f"missed: {flagged}"]


async def test_a_check_that_flags_everything_fails_on_false_rejections():
    results = await run_meaning(
        answering(lambda _: "untrue_about_dater"), timeout=1.0, samples=DEV_HALF
    )
    fine = sum(s.expected is Meaning.FINE for s in DEV_HALF)
    assert meaning_misses(results) == [f"false rejections: {fine} of {fine}"]


async def test_another_flag_than_the_one_expected_still_catches_the_line():
    """Failing closed is what matters: any flag keeps the line off air. The exact label is
    reported beside it."""
    results = await run_meaning(
        answering(lambda r: "fine" if r == "fine" else "wrong_segment"),
        timeout=1.0,
        samples=DEV_HALF,
    )
    assert meaning_misses(results) == []
    report = render_meaning(results, model_name="fake", set_name=MEANING_DEV)
    assert "untrue_about_dater" in report and "0 of 4" in report


async def test_the_held_out_report_names_no_line():
    results = await run_meaning(answering(lambda _: "fine"), timeout=1.0, samples=HELD_OUT_HALF)
    report = render_meaning(results, model_name="fake", set_name=MEANING_HELD_OUT, show_lines=False)
    shown = any(s.line in report for s in HELD_OUT_HALF)
    assert not shown and "Not handled as hoped" not in report and "FAILED" in report


async def test_the_dev_report_shows_what_went_wrong():
    results = await run_meaning(answering(lambda _: "fine"), timeout=1.0, samples=DEV_HALF)
    report = render_meaning(results, model_name="fake", set_name=MEANING_DEV)
    assert "missed   expected wrong_segment, got fine:" in report


@pytest.mark.parametrize(
    ("flags", "halves"),
    [
        ([], [MEANING_DEV]),
        (["--meaning"], [MEANING_DEV]),
        (["--distress"], []),
        (["--conduct"], []),
        (["--held-out"], [MEANING_HELD_OUT]),
    ],
)
def test_the_meaning_halves_run_when_asked(monkeypatch, flags, halves):
    from test_evaluation import FakeModel, no_network, well_behaved

    no_network(monkeypatch, FakeModel(well_behaved))
    ran = []

    async def fake_meaning(model, *, samples, **_):
        ran.append(MEANING_DEV if samples is DEV_HALF else MEANING_HELD_OUT)
        return []

    async def nothing(*_, **__):
        return []

    for name in ("run_distress", "run_conduct", "run_cases"):
        monkeypatch.setattr(evaluation, name, nothing)
    monkeypatch.setattr(evaluation, "run_meaning", fake_meaning)
    main(flags)
    assert ran == halves
