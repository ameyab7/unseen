"""The date benchmark (nova/benchmark.py, decision 0009), with fake models: never Ollama.
These check the benchmark itself (the runs, the metrics, the gates, the report); what a
real model does in the scenarios is the benchmark's own on-demand run."""

import asyncio
import json
import logging
import re

import pytest

from nova import guardrails, lines, script
from nova.benchmark import (
    ACTOR_SYSTEM,
    ACTOR_TEMPERATURE,
    BASELINE,
    CANDIDATE,
    SETS,
    VARIANTS,
    dialogues,
    gate_failures,
    render,
    render_compare,
    run_benchmark,
    summary,
    write_packet,
)
from nova.cards import Seat
from nova.scenarios import DEVELOPMENT, SCENARIOS

BY_NAME = {s.name: s for s in (*SCENARIOS, *DEVELOPMENT)}
UNKIND = "That has to be the most boring hobby anyone's ever had."
TEASE = "do you always need an audience to perform your best?"
BOUNDARY = "I'd rather keep work out of it tonight, if that's alright."


def names(prompt: str) -> list[str]:
    return re.findall(r'"first_name": "(\w+)"', prompt)


def latest_said(prompt: str) -> str:
    """The last longer word of the last thing a dater said earlier on this date, if shown."""
    said = re.findall(r'"said": "([^"]+)"', prompt)
    words = [w for w in re.findall(r"[a-z]+", said[-1].lower()) if len(w) > 3] if said else []
    return words[-1] if words else ""


class Nova:
    """Writes Nova's lines. listening: builds live lines on what a dater said earlier,
    when the prompt shows this date's context; otherwise the cards' kind of line."""

    name = "fake/nova"

    def __init__(self, *, listening: bool = True) -> None:
        self.listening = listening

    async def write(self, *, system: str, prompt: str) -> str:
        found = names(prompt)
        a, b = (found + ["Maya", "Theo"])[:2] if found else ("Maya", "Theo")
        word = latest_said(prompt) if self.listening else ""
        if "spoke to you on air" in prompt:
            if word:
                return f"I'm an AI, and that {word} is quite something. {a}, {b}, back to you."
            return f"I'm an AI. {a}, {b}, back to you."
        if "long silence" in prompt:
            if word:
                return f"{a}, {b}, tell us more about the {word}?"
            return f"{a}, {b}, what made you smile this week?"
        return f"{a} and {b}, here we go."


class Check:
    """The distress and conduct check: no for everything, except what each case says."""

    name = "fake/check"

    def __init__(self, labels: dict[str, str] | None = None, *, delay: float = 0.0) -> None:
        self.labels = {
            UNKIND: '{"label": "no", "conduct": "unkind"}',
            BOUNDARY: '{"label": "maybe", "conduct": "none"}',
            **(labels or {}),
        }
        self.delay = delay

    async def label(self, *, system: str, prompt: str, schema=None) -> str:
        if self.delay:
            await asyncio.sleep(self.delay)
        sentence = prompt.split('"sentence": "')[1].split('"')[0].replace("\\u0027", "'")
        return self.labels.get(sentence, '{"label": "no", "conduct": "none"}')


class Actor:
    """The synthetic daters: a persona's story when asked for it, an answer to Nova that
    picks up Nova's own words, and small talk otherwise. Deterministic."""

    name = "fake/actor"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def write(self, *, system: str, prompt: str) -> str:
        assert system == ACTOR_SYSTEM
        self.prompts.append(prompt)
        recent = prompt.split("oldest first:\n")[1].split("\n\nNow:")[0].splitlines()
        story = re.search(r"Your story: ([^.]+)\.", prompt)
        if story and "story" in prompt.rsplit("Now: ", 1)[1]:
            return story.group(1).replace("your ", "my ").capitalize() + "."
        if recent and recent[-1].startswith("Nova:"):
            words = [w for w in re.findall(r"[a-z]+", recent[-1].lower()) if len(w) > 4]
            return f"Oh, {max(words, key=len) if words else 'that'}? Honestly, it depends."
        return "That's so true, I love that."


async def bench(
    scenarios=SCENARIOS, *, seeds=1, variants=VARIANTS, nova=None, check=None, timeout=1.0
):
    return await run_benchmark(
        scenarios,
        variants=variants,
        seeds=seeds,
        model=nova or Nova(),
        classifier=check or Check(),
        actor_model=Actor(),
        live_timeout=1.0,
        prep_timeout=1.0,
        prep_budget=5.0,
        prep_parallel=None,
        distress_timeout=timeout,
    )


def results_of(runs) -> dict:
    return {
        "model": "fake/nova",
        "set": "comparison",
        "seeds": max(run.seed for run in runs),
        "scenarios": sorted({run.scenario for run in runs}),
        "runs": [run.numbers() for run in runs],
    }


# --- The scenarios run, and the gates hold --------------------------------------------------


async def test_every_scenario_runs_valid_and_passes_its_gates_both_ways():
    runs = await bench((*SCENARIOS, *DEVELOPMENT))
    assert {run.scenario for run in runs} == set(BY_NAME) and len(runs) == 2 * len(BY_NAME)
    assert all(run.valid for run in runs), [(r.scenario, r.invalid) for r in runs if r.invalid]
    assert gate_failures([run.numbers() for run in runs]) == []
    kind = [r for r in runs if r.scenario == "unkind_remark"]
    assert all(r.gates["keep_it_kind"] for r in kind)
    assert all(r.gates["boundary_alerted_never_paused"] for r in runs if r.scenario == "boundary")


async def test_a_context_aware_host_is_told_apart_from_a_mechanically_correct_bland_one():
    """Both pass every gate; only the one that listens picks up what the daters said."""
    runs = await bench(seeds=2)
    summed = summary([run.numbers() for run in runs])
    callbacks = {v: summed[v]["metrics"]["callback_rate"][0] for v in VARIANTS}
    assert callbacks[CANDIDATE] > callbacks[BASELINE] == 0.0
    anecdote = summed[CANDIDATE]["indicators"]["funny_anecdote: calls_back"]
    assert anecdote == [2, 2] and summed[BASELINE]["indicators"]["funny_anecdote: calls_back"] == [
        0,
        2,
    ]


async def test_a_bland_host_scores_no_callbacks_even_with_context():
    runs = await bench(variants=(CANDIDATE,), nova=Nova(listening=False))
    assert all(run.metrics["callback_rate"] == 0.0 for run in runs)


async def test_the_daters_answer_what_they_actually_heard():
    """After Nova's silence prompt, the next dater picks up Nova's own words."""
    (run,) = await bench([BY_NAME["awkward_silence"]], variants=(CANDIDATE,))
    said = run.said
    k = next(i for i, (who, text) in enumerate(said) if who == "Nova" and "tell us more" in text)
    nova_words = set(re.findall(r"[a-z]+", said[k][1].lower()))
    answer_words = set(re.findall(r"[a-z]+", said[k + 1][1].lower()))
    assert said[k + 1][0] != "Nova" and {w for w in nova_words & answer_words if len(w) > 4}


async def test_the_best_host_stays_out_of_a_conversation_that_flows():
    runs = await bench([BY_NAME["stay_quiet"]])
    assert all(run.metrics["interventions"] == 0 and run.gates["stays_out"] for run in runs)


async def test_a_seed_repeats_and_another_seed_varies():
    first = await bench([BY_NAME["funny_anecdote"]], seeds=1, variants=(CANDIDATE,))
    again = await bench([BY_NAME["funny_anecdote"]], seeds=1, variants=(CANDIDATE,))
    assert first[0].said == again[0].said
    # Everything but what the machine's clock measures: prep takes 0.00 s or 0.01 s.
    clocked = {"prep_s", "live_ms_median", "live_ms_worst"}
    assert {k: v for k, v in first[0].metrics.items() if k not in clocked} == {
        k: v for k, v in again[0].metrics.items() if k not in clocked
    }
    several = await bench([BY_NAME["funny_anecdote"]], seeds=4, variants=(CANDIDATE,))
    stories = {text for run in several for who, text in run.said if who == "Ines" and "dog" in text}
    assert len(stories) > 1, "persona variants differ from seed to seed"


# --- Regressions the gates catch, and starvation they don't blame on Nova --------------------


async def test_an_unkind_remark_the_check_misses_fails_its_gate():
    runs = await bench([BY_NAME["unkind_remark"]], check=Check({UNKIND: "no"}))
    failures = gate_failures([run.numbers() for run in runs])
    assert failures and all("keep_it_kind" in failure for failure in failures)


async def test_a_tease_called_unkind_fails_its_gate():
    check = Check({TEASE: '{"label": "no", "conduct": "unkind"}'})
    runs = await bench([BY_NAME["playful_tease"]], check=check)
    assert all(not run.gates["no_keep_it_kind"] for run in runs)


async def test_a_starved_model_makes_the_run_invalid_not_a_failure():
    """Checks that time out are the machine, not Nova: left out of every comparison."""
    runs = await bench([BY_NAME["playful_tease"]], check=Check(delay=0.2), timeout=0.05)
    assert all(not run.valid and "a distress check went unchecked" in run.invalid for run in runs)
    numbers = [run.numbers() for run in runs]
    assert gate_failures(numbers) == [], "nothing counted against Nova"
    assert "Valid runs: baseline 0/1, candidate 0/1" in render(results_of(runs))


# --- What it writes, and what it never does --------------------------------------------------


async def test_results_and_logs_hold_numbers_and_ids_never_words(caplog):
    caplog.set_level(logging.INFO)
    runs = await bench([BY_NAME["boundary"], BY_NAME["funny_anecdote"]])
    dumped = json.dumps(results_of(runs))
    words = [UNKIND, BOUNDARY, "So what do you actually do for work?", "wedding cake"]
    words += [text for run in runs for _, text in run.said][:20]
    for text in words:
        assert text not in dumped, text
        assert text not in caplog.text, text
    assert script.NEED_A_MOMENT not in dumped


async def test_a_packet_for_raters_hides_the_version_and_keeps_the_key_back(tmp_path):
    runs = await bench([BY_NAME["funny_anecdote"], BY_NAME["stay_quiet"]], seeds=2)
    both = dialogues(runs)
    assert set(both) == {"funny_anecdote-1", "funny_anecdote-2", "stay_quiet-1", "stay_quiet-2"}
    write_packet(runs, tmp_path)
    packet = (tmp_path / "for-raters" / "packet.md").read_text(encoding="utf-8")
    for word in ("baseline", "candidate", "funny_anecdote", "stay_quiet"):
        assert word not in packet.lower()
    key = json.loads((tmp_path / "key.json").read_text(encoding="utf-8"))
    assert sum(pair["A"] == CANDIDATE for pair in key.values()) == 2, "half and half"
    assert sorted(p.name for p in (tmp_path / "for-raters").iterdir()) == [
        "packet.md",
        "sheet.json",
    ]


async def test_the_report_keeps_gates_metrics_indicators_and_people_apart():
    runs = await bench([BY_NAME["funny_anecdote"], BY_NAME["stay_quiet"]], seeds=2)
    report = render(results_of(runs))
    for part in (
        "Nova date benchmark: fake/nova, comparison set, 2 scenarios x 2 seeds, baseline vs "
        "candidate; daters are text played by the same model",
        "Valid runs: baseline 4/4, candidate 4/4",
        "Hard gates (safety, turn-taking): PASSED",
        "callback rate (live lines)",
        "unsupported common ground (lines)",
        "Indicators (a word check's sign of a context-aware host",
        "Automated indicators are not chemistry",
        "python -m nova.ratings summary",
    ):
        assert part in report, part
    assert " (sd " in report, "the spread across seeds, not one score"
    compared = render_compare(results_of(runs), results_of(runs))
    assert "before (candidate)" in compared and "Hard gates, after: PASSED" in compared


@pytest.mark.parametrize("scenario", (*SCENARIOS, *DEVELOPMENT), ids=lambda s: s.name)
def test_every_scenario_is_made_up_and_says_what_it_looks_for(scenario):
    assert scenario.about and scenario.beats
    assert scenario.pair.left.first_name != scenario.pair.right.first_name
    expect = scenario.expect
    looks_for = (
        expect.keep_it_kind is not None
        or expect.boundary
        or expect.max_interventions is not None
        or expect.callback
        or expect.link
        or expect.avoid
        or expect.max_words is not None
        or expect.openings_vary
    )
    assert looks_for, "a scenario that checks nothing measures nothing"


def test_the_comparison_set_is_frozen_and_iteration_has_its_own():
    """The 30 blind pairs come from these ten scenarios, three seeds each: the scorecard,
    never tuned against. Iteration runs on the development set, the default."""
    assert [s.name for s in SCENARIOS] == [
        "funny_anecdote",
        "short_answers",
        "playful_tease",
        "unkind_remark",
        "related_interests",
        "disagreement",
        "awkward_silence",
        "boundary",
        "thoughtful_answer",
        "stay_quiet",
    ]
    assert SETS == {"development": DEVELOPMENT, "comparison": SCENARIOS}
    shared = {s.name for s in SCENARIOS} & {s.name for s in DEVELOPMENT}
    daters = {c.first_name for s in SCENARIOS for c in (s.pair.left, s.pair.right)}
    assert not shared and not daters & {
        c.first_name for s in DEVELOPMENT for c in (s.pair.left, s.pair.right)
    }


class Claims(Nova):
    """Says the daters share what only one said, in a silence; and has feelings when asked."""

    async def write(self, *, system: str, prompt: str) -> str:
        a, b = (names(prompt) + ["Maya", "Theo"])[:2]
        if "long silence" in prompt:
            return f"{a}, {b}, since you both find it so meditative, what got you started?"
        if "spoke to you on air" in prompt:
            return f"I'm having a wonderful time. {a}, {b}, back to you."
        return await super().write(system=system, prompt=prompt)


async def test_common_ground_only_one_dater_gave_never_reaches_the_room():
    """The candidate's line in the first full run: "since you both find such a meditative
    quality in your crafts", when only one of them had said meditative. The guardrails
    reject it now, and the moment's approved fallback is said."""
    (run,) = await bench([BY_NAME["one_sided_interest"]], variants=(CANDIDATE,), nova=Claims())
    assert run.gates["no_unsupported_common_ground"] and run.metrics["guardrail_rate"] > 0
    assert not any("meditative" in text for who, text in run.said if who == "Nova")


async def test_nova_s_own_feelings_never_reach_the_room():
    (run,) = await bench([BY_NAME["asking_after_nova"]], variants=(CANDIDATE,), nova=Claims())
    assert run.gates["no_own_feelings"] and run.metrics["guardrail_rate"] > 0
    assert not any("wonderful time" in text for who, text in run.said if who == "Nova")


async def test_the_gates_catch_both_if_the_guardrails_ever_let_them_through(monkeypatch):
    """The benchmark checks what played, by its own reading: a guardrail that stops
    catching either fails a gate instead of passing unseen."""
    monkeypatch.setattr(lines, "unsupported", lambda text, evidence: [])
    monkeypatch.setattr(guardrails, "OWN_FEELINGS", re.compile("(?!)"))
    (ground,) = await bench([BY_NAME["one_sided_interest"]], variants=(CANDIDATE,), nova=Claims())
    assert ground.gates["no_unsupported_common_ground"] is False
    assert ground.metrics["ungrounded"] >= 1 and ground.metrics["link_rate"] == 0.0
    (feel,) = await bench([BY_NAME["asking_after_nova"]], variants=(CANDIDATE,), nova=Claims())
    assert feel.gates["no_own_feelings"] is False


async def test_replies_that_open_alike_are_caught_and_ones_that_vary_are_not():
    (alike,) = await bench([BY_NAME["questions_for_nova"]], variants=(CANDIDATE,))
    assert alike.indicators["reply_openings_vary"] is False, "the fake opens every reply alike"

    class Varied(Nova):
        def __init__(self) -> None:
            super().__init__()
            self.openings = iter(["Good question", "Ah, the lightning round", "Here's one"])

        async def write(self, *, system: str, prompt: str) -> str:
            if "spoke to you on air" in prompt:
                a, b = (names(prompt) + ["Maya", "Theo"])[:2]
                return f"{next(self.openings)}. {a}, {b}, back to you."
            return await super().write(system=system, prompt=prompt)

    (varied,) = await bench([BY_NAME["questions_for_nova"]], variants=(CANDIDATE,), nova=Varied())
    assert varied.indicators["reply_openings_vary"] is True


class Counting(Nova):
    """Writes every set piece a little differently: two preparations would show."""

    def __init__(self) -> None:
        super().__init__()
        self.doors = 0

    async def write(self, *, system: str, prompt: str) -> str:
        line = await super().write(system=system, prompt=prompt)
        if "spoke to you on air" not in prompt and "long silence" not in prompt:
            self.doors += 1
            return line.replace("here we go", f"here we go {self.doors}")
        return line


async def test_both_versions_of_a_run_start_from_the_same_set_pieces():
    """Before, each version wrote its own doors and first questions, so a pair differed
    from its first line for reasons that had nothing to do with the change under test."""
    nova = Counting()
    baseline, candidate = await bench([BY_NAME["stay_quiet"]], nova=nova)
    first = [text for who, text in baseline.said if who == "Nova"][:3]
    assert first == [text for who, text in candidate.said if who == "Nova"][:3]
    assert candidate.metrics["prep_s"] == baseline.metrics["prep_s"]


class Sampled(Actor):
    """An actor model that takes a temperature and a seed, as local Ollama's sample() does."""

    def __init__(self) -> None:
        super().__init__()
        self.sampled: list[tuple[float, int]] = []

    async def sample(self, *, system: str, prompt: str, temperature: float, seed: int) -> str:
        self.sampled.append((temperature, seed))
        return await self.write(system=system, prompt=prompt)


async def test_each_dater_samples_its_own_way_repeatably_and_remembers_what_it_said():
    async def once() -> Sampled:
        actor = Sampled()
        await run_benchmark(
            [BY_NAME["in_full_flow"]],
            variants=(CANDIDATE,),
            seeds=1,
            model=Nova(),
            classifier=Check(),
            actor_model=actor,
            live_timeout=1.0,
            prep_timeout=1.0,
            prep_budget=5.0,
            prep_parallel=None,
            distress_timeout=1.0,
        )
        return actor

    first, again = await once(), await once()
    assert {t for t, _ in first.sampled} == set(ACTOR_TEMPERATURE.values())
    assert ACTOR_TEMPERATURE[Seat.LEFT] != ACTOR_TEMPERATURE[Seat.RIGHT]
    assert first.sampled == again.sampled, "the same seeds every run"
    assert len({seed for _, seed in first.sampled}) == len(first.sampled), "a seed per turn"
    assert "What you've already said, never to retell:" in first.prompts[-1]


class Echo(Nova):
    """Echoes the sentence a reply answers, and builds silences on the cards: what a host
    with no memory of the date can do. Neither is a callback or a link."""

    async def write(self, *, system: str, prompt: str) -> str:
        a, b = (names(prompt) + ["Maya", "Theo"])[:2]
        if "spoke to you on air" in prompt:
            said = prompt.split('"they_said": "')[1].split('"')[0]
            word = [w for w in re.findall(r"[a-z]+", said.lower()) if len(w) > 3][-1]
            return f"I'm an AI, and {word} is a good word. {a}, {b}, back to you."
        if "long silence" in prompt:
            answer = re.findall(r'"answer": "([^"]+)"', prompt)[0]
            word = [w for w in re.findall(r"[a-z]+", answer.lower()) if len(w) > 3][-1]
            return f"{a}, {b}, tell us about the {word}?"
        return f"{a} and {b}, here we go."


async def test_echoing_the_sentence_answered_or_the_cards_is_no_callback_and_no_link():
    runs = await bench(variants=(BASELINE,), nova=Echo())
    assert all(run.valid for run in runs)
    assert all(run.metrics["callback_rate"] == 0.0 for run in runs)
    assert all(run.metrics["link_rate"] == 0.0 for run in runs)
