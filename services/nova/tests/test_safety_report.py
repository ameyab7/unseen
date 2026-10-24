"""The frozen safety evaluation (nova/safety_report.py): fresh reloads, worst outcomes, the
recorded baseline, and nothing protected ever shown. No model and no network: the
classifier is faked, and so is Ollama."""

from pathlib import Path

import pytest

from nova import conduct_set, evaluation, independent_set, safety_report
from nova.distress import LIGHT_SYSTEM
from nova.distress_set import DISTRESS_SET, Sample
from nova.evaluation import main
from nova.safety_report import (
    BASELINE,
    Counts,
    SetRun,
    ollama_reload,
    passed,
    render,
    run_set,
    single,
    stability,
    worst,
    worst_conduct,
)

SAMPLES = (
    Sample("placeholder quokka stop", True),
    Sample("placeholder quokka tea", False),
    Sample("placeholder quokka number", True, boundary=True),
)


class Classifier:
    """Answers each sentence from `labels` (a label per pass, or one for all), counting the
    calls per sentence; the second question is always false. Records the order asked."""

    name = "fake/classifier"

    def __init__(self, labels: dict[str, object]) -> None:
        self.labels, self.calls, self.asked = labels, {}, []

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        if system == LIGHT_SYSTEM:
            return '{"light": false}'
        said = prompt.split('"sentence": "')[1].split('"')[0]
        self.asked.append(said)
        k = self.calls[said] = self.calls.get(said, -1) + 1
        answer = self.labels.get(said, "no")
        label = answer[k % len(answer)] if isinstance(answer, list) else answer
        return f'{{"label": "{label}", "conduct": "none"}}'


async def run(model, samples=SAMPLES, *, passes=3, protected=False, conduct=(), reload=None):
    return await run_set(
        model,
        name="dev",
        protected=protected,
        samples=samples,
        conduct=conduct,
        passes=passes,
        reload=reload,
        timeout=1.0,
        grace=0.6,
        max_wait=1.5,
    )


def test_the_recorded_baseline_is_kept_as_it_was_recorded():
    """Prompt B's figures, 2026-10-23 (decision 0005): never re-derived or improved."""
    dev, held_out = BASELINE["dev"], BASELINE["held-out"]
    assert dev == Counts(155, 150, 73, 70, 0, 3, 9, 9, 0, 0, 73, 71, 0, 2, (5, 5))
    assert held_out == Counts(106, 100, 52, 48, 1, 3, 1, 1, 0, 0, 53, 51, 0, 2, (3, 4))
    assert BASELINE["independent"] is None, "0/0: not yet written"
    for c in (dev, held_out):
        assert c.handled == c.distress_paused + c.refusals_alerted + c.ordinary_quiet
        assert c.total == c.distress + c.refusals + c.ordinary


async def test_every_pass_follows_a_reload_and_the_first_keeps_the_sets_order():
    reloads = []

    async def reload():
        reloads.append(len(model.asked))

    model = Classifier({"placeholder quokka stop": "yes", "placeholder quokka number": "maybe"})
    result = await run(model, reload=reload)
    assert reloads == [0, 3, 6], "a reload before each pass of three sentences"
    assert model.asked[:3] == [s.sentence for s in SAMPLES], "pass 1 is the single run"
    assert all(len(p) == 3 for p in result.distress)
    for each in result.distress:
        assert [r.sample for r in each] == list(SAMPLES), "results back in the set's order"
    assert result.reloaded


async def test_later_passes_are_shuffled_the_same_way_every_time():
    first, second = Classifier({}), Classifier({})
    await run(first, samples=tuple(DISTRESS_SET[:8]))
    await run(second, samples=tuple(DISTRESS_SET[:8]))
    assert first.asked == second.asked, "seeded"
    assert first.asked[8:16] != first.asked[:8], "pass 2 comes in another order"


async def test_a_sentence_that_changes_counts_at_its_worst_and_is_listed():
    model = Classifier({"placeholder quokka stop": ["yes", "no", "yes"]})
    result = await run(model)
    assert single(result).distress_paused == 1 and worst(result).distress_missed == 1
    assert stability(result).changed == (
        ("placeholder quokka stop", ("paused", "missed", "paused")),
    )
    assert not passed(result), "the gate is judged on the worst outcome"
    report = render({"dev": result}, model_name="fake")
    assert "placeholder quokka stop: paused, missed, paused" in report
    assert "1 of 3 sentences changed outcome" in report


async def test_a_protected_set_names_a_changed_sentence_by_position_only():
    samples = tuple(DISTRESS_SET[:5])
    flips = {s.sentence: ["yes", "no"] for s in samples}
    result = await run(Classifier(flips), samples=samples, passes=2, protected=True)
    report = render({"held-out": result}, model_name="fake")
    shown = any(s.sentence in report or s.sentence[:20] in report for s in samples)
    labels = [label for label, _ in stability(result).changed]
    # The instant layer pauses some of these without a model: only the others can change.
    assert not shown and labels and all(label.startswith("#") for label in labels)


async def test_without_a_reload_the_report_says_the_passes_were_separate_requests():
    result = await run(Classifier({}), passes=2)
    assert not result.reloaded
    assert "no reload: not on Ollama" in render({"dev": result}, model_name="fake")


def harassment(found: str) -> evaluation.ConductResult:
    sample = conduct_set.ConductSample("placeholder press", "harassment")
    return evaluation.ConductResult(sample, frozenset({found} if found else ()))


def test_conduct_is_caught_only_if_every_pass_caught_it():
    caught = worst_conduct([[harassment("harassment")], [harassment("")]])
    assert caught[0].found == frozenset()
    ordinary = conduct_set.ConductSample("placeholder chat", conduct_set.NONE)
    acted = worst_conduct([[evaluation.ConductResult(ordinary, frozenset())]] * 2)
    assert acted[0].found == frozenset()


async def test_harassment_comes_from_the_sets_conduct_half_across_its_own_reloads():
    reloads = []

    async def reload():
        reloads.append("reload")

    half = [conduct_set.ConductSample("placeholder quokka press", "harassment")]
    result = await run(Classifier({}), passes=2, conduct=half, reload=reload)
    assert len(reloads) == 4, "distress and conduct each reloaded, every pass"
    assert worst(result).harassment == (0, 1)
    assert "harassment" in render({"dev": result}, model_name="fake")


def test_a_set_that_isnt_written_is_reported_as_zero_of_zero():
    report = render({"independent": None}, model_name="fake")
    assert "Independent set: 0/0, not yet written" in report


async def test_an_ollama_model_is_unloaded_then_warmed_afresh(monkeypatch):
    posted, listed, warmed = [], [["gemma4:26b"], []], []
    monkeypatch.setattr(safety_report, "_post", lambda url, body: posted.append((url, body)))
    monkeypatch.setattr(safety_report, "_loaded", lambda url: listed.pop(0))

    async def warm():
        warmed.append(True)

    model = type("M", (), {"name": "ollama/gemma4:26b"})()
    reload = ollama_reload(model, "http://localhost:11434/", warm)
    assert reload is not None
    await reload()
    assert posted == [
        ("http://localhost:11434/api/generate", {"model": "gemma4:26b", "keep_alive": 0})
    ]
    assert listed == [] and warmed == [True], "waited until it was gone, then loaded it"


def test_a_model_elsewhere_has_no_reload():
    model = type("M", (), {"name": "anthropic/claude-haiku-5-5"})()
    assert ollama_reload(model, "http://localhost:11434", lambda: None) is None


def test_the_safety_run_reports_every_set_and_the_unwritten_independent_one(
    monkeypatch, tmp_path, capsys
):
    from test_evaluation import FakeModel, knows, no_network

    no_network(monkeypatch, FakeModel(label=knows))
    monkeypatch.setattr(independent_set, "DEFAULT_PATH", tmp_path / "distress.json")
    calls = []

    async def run_set(model, *, name, samples, conduct, passes, **_):
        calls.append((name, len(samples), len(conduct), passes))
        one = tuple(evaluation.DistressResult(s, _heard()) for s in samples)
        return SetRun(name, name != "dev", (one,) * passes, (), reloaded=False)

    monkeypatch.setattr(safety_report, "run_set", run_set)
    code = main(["--safety", "--fresh", "2"])
    out = capsys.readouterr().out
    assert calls == [
        ("dev", 155, len(conduct_set.DEV_HALF), 2),
        ("held-out", 106, len(conduct_set.HELD_OUT_HALF), 2),
    ]
    assert "Development set (155 sentences)" in out and "Held-out set (106 sentences)" in out
    assert "No independent set at" in out and code == 1


def _heard() -> evaluation.Heard:
    return evaluation.Heard(evaluation.Layer.NONE, paused=False, alerted=False, reply=False)


@pytest.mark.parametrize("name", ["dev", "held-out"])
def test_the_report_columns_are_single_run_worst_and_baseline(name):
    one = tuple(evaluation.DistressResult(s, _heard()) for s in SAMPLES)
    report = render({name: SetRun(name, False, (one, one))}, model_name="fake")
    assert "single run" in report and "worst of 2" in report and "baseline" in report
    assert Path(safety_report.__file__).name == "safety_report.py"
