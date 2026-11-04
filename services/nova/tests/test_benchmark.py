"""The date benchmark (nova/benchmark.py, decision 0009), with fake models: never Ollama.
These check the benchmark itself (the runs, the metrics, the gates, the report); what a
real model does in the scenarios is the benchmark's own on-demand run."""

import asyncio
import dataclasses
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
    main,
    render,
    render_compare,
    render_full_date,
    run_benchmark,
    summary,
    write_packet,
)
from nova.cards import Seat
from nova.conduct_judge import JUDGE_SYSTEM
from nova.rehearsal import Prefix
from nova.scenarios import DEVELOPMENT, FULL_DATES, GEN_Z_DATES, REACT, SCENARIOS, Beat
from nova.steering import Steer

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
        if system == JUDGE_SYSTEM:  # the second conduct check: a put-down
            return '{"aim": "playful", "mutual": "no", "verdict": "unkind"}'
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
    scenarios=SCENARIOS,
    *,
    seeds=1,
    variants=VARIANTS,
    nova=None,
    check=None,
    timeout=1.0,
    echo=None,
    bridges=False,
    steering=True,
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
        echo=echo,
        bridges=bridges,
        steering=steering,
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
        or bool(expect.never)
        or expect.asked_again
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


# --- Full dates and the 2026-10-31 observations (decision 0009, 2026-11-01) -------------


async def test_a_full_date_runs_from_the_doors_to_the_result_both_ways():
    runs = await bench(FULL_DATES)
    assert len(runs) == 2 * len(FULL_DATES)
    assert all(run.valid for run in runs), [(r.scenario, r.invalid) for r in runs if r.invalid]
    assert gate_failures([run.numbers() for run in runs]) == []
    for run in runs:
        date = next(d for d in FULL_DATES if d.name == run.scenario)
        assert run.metrics["reached_result"] == 1.0, run.scenario
        mutual = all(choice == "reveal" for choice in date.choices.values())
        assert run.metrics["mutual"] == float(mutual)
        segments = run.notes[0]
        for segment in ("doors", "first_question", "free_talk", "from_the_room", "time"):
            assert segment in segments, (run.scenario, segments)
        assert ("lightning_round" in segments) is date.unlock
        assert run.metrics["real_s"] >= 0 and run.metrics["date_s"] > 400, "a whole date"


async def test_every_turn_nova_gives_a_dater_on_a_full_date_is_answered():
    """The room's question by both daters, each quick-fire question by both, each last
    word by the dater it's for: whoever Nova hands the floor to speaks next."""
    [run] = await bench(FULL_DATES[:1], variants=(CANDIDATE,))
    said = run.said
    names = {"Esme", "Rory"}
    for k, (who, _) in enumerate(said):
        if who == "Nova" and k + 1 < len(said) and said[k + 1][0] != "Nova":
            assert said[k + 1][0] in names
    turns = [text for who, text in said if who == "Rory"]
    assert turns[0] == "Sorry, what was the question?", "asked again, without naming Nova"
    assert run.gates["every_word_to_nova_answered"]


async def test_live_shows_each_line_as_it_is_said_and_never_a_decision():
    shown: list[str] = []
    await bench(FULL_DATES[1:2], variants=(CANDIDATE,), echo=shown.append)
    text = "\n".join(shown)
    assert "--- The doors (0:00) ---" in text and "--- The decision window" in text
    assert "  Nova: " in text and "[doors_intro | " in text
    assert "  Niko: " in text and "  Talia: " in text
    assert "Niko has decided, privately" in text and "Talia has decided, privately" in text
    decided = [line for line in shown if "decided" in line]
    assert not any(word in line for line in decided for word in ("reveal", "unseen"))
    assert "=== boundary_tester (candidate, seed 1)" in text, "and each date's report after it"
    assert "Safety checks:" in text and "Result: not mutual" in text


async def test_a_full_dates_results_hold_numbers_codes_and_ids_never_words():
    runs = await bench(FULL_DATES[2:], variants=(CANDIDATE,))
    stored = json.dumps([run.numbers() for run in runs])
    for _, text in runs[0].said:
        assert text not in stored
    assert all(note.startswith(("order: ", "hold: ", "fallback: ")) for note in runs[0].notes)


async def test_a_full_date_packet_pairs_both_versions_of_each_date(tmp_path):
    runs = await bench(FULL_DATES[:1])
    write_packet(runs, tmp_path)
    assert (tmp_path / "for-raters" / "packet.md").exists()
    assert set(json.loads((tmp_path / "dialogues.json").read_text(encoding="utf-8"))) == {
        "rambler_and_deadpan-1"
    }


async def test_a_dater_who_asks_again_without_naming_nova_is_answered():
    """Only the conversational floor knows who Nova just asked (decision 0012): the gate is
    the candidate's; the baseline never counts the request as words to Nova."""
    runs = await bench([BY_NAME["asks_again"]])
    candidate = next(run for run in runs if run.variant == CANDIDATE)
    baseline = next(run for run in runs if run.variant == BASELINE)
    assert candidate.gates["answers_asked_again"]
    assert "answers_asked_again" not in baseline.gates
    assert ["Cal", "Sorry, can you repeat the question?"] in [list(s) for s in candidate.said]


async def test_a_pause_mid_thought_is_two_stretches_of_speech_with_quiet_between():
    [run] = await bench([BY_NAME["mid_thought_pause"]], variants=(CANDIDATE,))
    said = [text for who, text in run.said if who == "Gus"]
    first = said.index("So the reason I took up rowing, and this will sound strange, is because")
    assert said[first + 1] == "my grandad rowed the same river every Sunday until he was ninety."
    assert run.gates["stays_out"]


def test_full_dates_use_their_own_made_up_daters():
    others = {c.first_name for s in (*SCENARIOS, *DEVELOPMENT) for c in (s.pair.left, s.pair.right)}
    seen: set[str] = set()
    for date in (*FULL_DATES, *GEN_Z_DATES):
        names = {date.pair.left.first_name, date.pair.right.first_name}
        assert date.about and date.talk and date.questions
        assert names.isdisjoint(others) and names.isdisjoint(seen), date.name
        seen |= names
        assert set(date.choices) == set(Seat) and set(date.choices.values()) <= {
            "reveal",
            "unseen",
        }


def test_each_gen_z_date_is_written_for_a_step_in_or_for_nova_staying_out():
    """Decision 0013's scenarios: every kind of step-in the show runner makes from the
    signals has a date written for it, and one date is written for Nova to stay out."""
    for date in GEN_Z_DATES:
        assert date.expect.steer is not None, date.name
        assert set(date.expect.steer) <= {str(kind) for kind in Steer}, date.name
        cards = (date.pair.left, date.pair.right)
        assert all(21 <= card.age <= 29 for card in cards), "Gen Z, and adults"
    kinds = {kind for date in GEN_Z_DATES for kind in date.expect.steer or ()}
    assert kinds == {str(kind) for kind in Steer}
    assert any(date.expect.steer == () for date in GEN_Z_DATES)


async def test_gen_z_dates_run_from_the_doors_to_the_result_and_say_how_nova_stepped_in():
    boundary = '{"label": "maybe", "conduct": "none"}'  # a calm boundary, as the check calls it
    runs = await bench(
        GEN_Z_DATES, check=Check({"Ngl I'd rather not say, it's too embarrassing.": boundary})
    )
    assert len(runs) == 2 * len(GEN_Z_DATES)
    assert all(run.valid for run in runs), [(r.scenario, r.invalid) for r in runs if r.invalid]
    assert gate_failures([run.numbers() for run in runs]) == []
    for run in runs:
        date = next(d for d in GEN_Z_DATES if d.name == run.scenario)
        assert run.metrics["reached_result"] == 1.0, run.scenario
        assert ("lightning_round" in run.notes[0]) is date.unlock
        # Only today's Nova steps in, so only its runs say whether it did as the date hoped.
        assert ("steps_in_as_hoped" in run.indicators) is (run.variant == CANDIDATE)
    # The fake daters never give the signals, so Nova stays out: right only where it should.
    hoped = {r.scenario: r.indicators["steps_in_as_hoped"] for r in runs if r.variant == CANDIDATE}
    assert hoped == {date.name: date.expect.steer == () for date in GEN_Z_DATES}
    [quiet] = await bench(GEN_Z_DATES[:1], variants=(CANDIDATE,), steering=False)
    assert "steps_in_as_hoped" not in quiet.indicators, "nothing to judge with steering off"


def test_dates_are_chosen_only_for_full_dates(capsys):
    """--dates without --full would be ignored: it stops before any model is built."""
    with pytest.raises(SystemExit) as stopped:
        main(["--dates", "gen_z"])
    assert stopped.value.code == 2 and "use it with --full" in capsys.readouterr().err


async def test_bridges_come_only_with_the_switch_and_only_to_today_s_nova():
    """NOVA_LIVE_BRIDGES stays off on the real show (decision 0012); --bridges turns them
    on for the candidate alone, which then says what became of each place one may go."""
    for run in await bench(FULL_DATES[:1]):
        assert run.metrics["bridges_on"] == run.metrics["bridges_tried"] == 0.0
        assert not any(note.startswith("bridge at") for note in run.notes)
    runs = {run.variant: run for run in await bench(FULL_DATES[:1], bridges=True)}
    baseline, candidate = runs[BASELINE], runs[CANDIDATE]
    assert baseline.metrics["bridges_on"] == baseline.metrics["bridges_tried"] == 0.0
    assert candidate.metrics["bridges_on"] == 1.0 and candidate.metrics["bridges_tried"] >= 1
    spots = [note for note in candidate.notes if note.startswith("bridge at")]
    assert [note.split(":")[0] for note in spots] == [
        "bridge at first_question (right)",
        "bridge at from_the_room",
        "bridge at last_word (left)",
    ]
    assert all("not tried" in note or any(f": {fate}" in note for fate in Prefix) for note in spots)
    # Every rejected bridge says why, the ones the line writer never asked for included.
    assert all(note.endswith(")") for note in spots if f": {Prefix.REJECTED}" in note)
    shown = render_full_date(candidate)
    assert "  Bridges: " in shown and "  Bridges: " not in render_full_date(baseline)


async def test_two_put_downs_get_one_kind_line_then_the_moderator_never_a_pause():
    """Nova never pauses the show for conduct by itself (decision 0006, 2026-11-02): the
    first put-down gets "Let's keep it kind.", the second alerts the moderator."""
    date = dataclasses.replace(
        FULL_DATES[0],
        name="two_put_downs",
        talk=(Beat(Seat.LEFT, line=UNKIND), Beat(Seat.RIGHT, REACT), Beat(Seat.LEFT, line=UNKIND)),
    )
    [run] = await bench([date], variants=(CANDIDATE,))
    assert run.metrics["kind_lines"] == 1 and run.metrics["show_pauses"] == 0
    assert run.metrics["alerts"] >= 1
    assert run.metrics["reached_result"] == 1.0
    assert not any(note.startswith("hold: show_pause") for note in run.notes)
    # The doors and the result, Nova's alone, don't count against its share of the
    # conversation (2026-11-02): only the whole show's share.
    assert 0 < run.metrics["nova_share_talk"] < run.metrics["nova_share"]
