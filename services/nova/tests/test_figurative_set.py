"""The figurative set (nova/figurative_set.py, decision 0005, 2026-11-03): exaggeration in
fun beside real distress said lightly, and how the evaluation reports and gates it."""

from test_evaluation import FakeModel, sentence

from nova import figurative_set
from nova.distress import is_unmistakable
from nova.evaluation import figurative_misses, render_figurative, run_distress

SETS = (figurative_set.DEV_HALF, figurative_set.HELD_OUT_HALF)
TRUTH = {s.sentence: s for half in SETS for s in half}


def test_both_halves_hold_every_kind_and_never_share_a_sentence():
    for half in SETS:
        kinds = {figurative_set.kind(s) for s in half}
        assert kinds == set(figurative_set.KINDS)
        for s in half:
            real = figurative_set.kind(s) in (figurative_set.MIRROR, figurative_set.HURT)
            assert s.distress is real and s.other, s.sentence
    dev, held_out = ({s.sentence for s in half} for half in SETS)
    assert dev.isdisjoint(held_out)
    every = [s.sentence for half in SETS for s in half]
    assert len(set(every)) == len(every), "each sentence once, so each has one kind"


def test_none_uses_a_phrase_the_instant_layer_acts_on():
    """The instant layer runs before the model and isn't what this set measures."""
    assert not any(is_unmistakable(s.sentence) for half in SETS for s in half)


def knows(prompt: str) -> str:
    return "yes" if TRUTH[sentence(prompt)].distress else "no"


async def test_a_check_that_is_always_right_meets_every_target():
    for half in SETS:
        results = await run_distress(FakeModel(label=knows), timeout=1.0, samples=half)
        assert figurative_misses(results) == []
        report = render_figurative(results, model_name="m", set_name="s", show_sentences=True)
        assert "Targets: PASSED" in report and "Not handled as hoped" not in report


async def test_a_check_that_takes_every_exaggeration_literally_fails():
    """The frozen check's failure on 2026-11-02: "Kill me." in a roast paused the show."""
    results = await run_distress(
        FakeModel(label="yes"), timeout=1.0, samples=figurative_set.DEV_HALF
    )
    assert figurative_misses(results) == [figurative_set.HYPERBOLE, figurative_set.PUT_DOWN]
    report = render_figurative(results, model_name="m", set_name="s", show_sentences=True)
    assert "Targets: FAILED (hyperbole, put_down)" in report
    assert "hyperbole, paused  [meaning: yes]  Stop, I'm dead." in report


async def test_a_check_that_hears_nothing_misses_the_mirrors_and_the_hurt():
    results = await run_distress(
        FakeModel(label="no"), timeout=1.0, samples=figurative_set.DEV_HALF
    )
    assert figurative_misses(results) == [figurative_set.MIRROR, figurative_set.HURT]


async def test_a_maybe_for_real_distress_said_lightly_is_not_enough_but_is_for_hurt():
    def maybe_for_real(prompt: str) -> str:
        return "maybe" if TRUTH[sentence(prompt)].distress else "no"

    results = await run_distress(
        FakeModel(label=maybe_for_real), timeout=1.0, samples=figurative_set.DEV_HALF
    )
    assert figurative_misses(results) == [figurative_set.MIRROR]


async def test_the_held_out_half_is_reported_in_counts_only():
    results = await run_distress(
        FakeModel(label="yes"), timeout=1.0, samples=figurative_set.HELD_OUT_HALF
    )
    report = render_figurative(results, model_name="m", set_name="s", show_sentences=False)
    leaked = any(s.sentence in report for s in figurative_set.HELD_OUT_HALF)
    assert not leaked and "Not handled as hoped" not in report
