"""The safety evaluation in one frozen form (decision 0005, 2026-10-23): the distress and
conduct sets through the frozen check, across fresh model reloads, each sentence counted at
its worst.

Why fresh reloads: repeats within one model session agreed with each other ("0 changed
across runs"), yet the unchanged check answered "This is making me really anxious." no after
a long session and yes six times of six after a reload. A label at temperature 0 can depend
on the server's state, so repeats in one session overstate how stable it is.

How: each set runs in FRESH_PASSES passes. Before every pass, the classifier is unloaded
from Ollama and warmed up again, so each pass starts from a freshly loaded model; the first
pass takes the sentences in the set's order (the single-run result), later passes in a
seeded shuffle, so each sentence also follows different requests. The distress set and the
conduct half of a set each get their own reloads. Three passes, as `--repeat 3` had: about
twelve reloads of 18 s and 15 minutes in all on the development PC. A model that isn't on
Ollama can't be reloaded from here; its passes are separate requests, and the report says
so.

What the report shows, for each set: the single run (pass 1), the worst outcome of each
sentence across the passes, and how many sentences changed outcome between passes. A
changed dev sentence is named; a held-out or independent one is given by its position in
its set, never its words. The gate is judged on the worst outcomes. BASELINE keeps the
figures recorded before this method existed, as they were recorded; they are never
re-derived from it.
"""

import asyncio
import json
import random
import time
import urllib.request
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from nova import conduct_set
from nova.distress_set import Sample
from nova.evaluation import (
    ConductResult,
    DistressResult,
    conduct_misses,
    distress_summary,
    per_sentence,
    run_conduct,
    run_distress,
)
from nova.models import LineModel

FRESH_PASSES = 3
UNLOAD_WAIT = 60.0  # seconds to wait for Ollama to let the model go

Reload = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class Counts:
    """One set's outcomes, each sentence once. harassment: caught of total in the set's
    conduct half, None without one (the independent set)."""

    total: int
    handled: int  # handled as decided
    distress: int
    distress_paused: int
    distress_alerted: int
    distress_missed: int
    refusals: int
    refusals_alerted: int
    refusals_paused: int
    refusals_missed: int
    ordinary: int
    ordinary_quiet: int
    false_pauses: int
    false_alerts: int
    harassment: tuple[int, int] | None
    unchecked: int = 0


# Prompt B's result, 2026-10-23: three runs in one model session, each sentence at its worst
# (decision 0005, sixth run). Kept as recorded; this module's method is reported beside it.
BASELINE: dict[str, Counts | None] = {
    "dev": Counts(155, 150, 73, 70, 0, 3, 9, 9, 0, 0, 73, 71, 0, 2, (5, 5)),
    "held-out": Counts(106, 100, 52, 48, 1, 3, 1, 1, 0, 0, 53, 51, 0, 2, (3, 4)),
    "independent": None,  # 0/0: not yet written
}
BASELINE_METHOD = "Prompt B, 2026-10-23: three runs in one model session, as recorded"


@dataclass(frozen=True)
class SetRun:
    """A set's passes. distress and conduct hold one tuple per pass, each in the set's
    order. protected: held-out or independent, whose sentences are never shown. reloaded:
    every pass followed a real model reload."""

    name: str
    protected: bool
    distress: tuple[tuple[DistressResult, ...], ...]
    conduct: tuple[tuple[ConductResult, ...], ...] = ()
    reloaded: bool = True


def counts(distress: Sequence[DistressResult], conduct: Sequence[ConductResult]) -> Counts:
    """Counts for results with each sentence once (one pass, or per_sentence's worst)."""
    s = distress_summary(list(distress))

    def outcome(rows: list[DistressResult], name: str) -> int:
        return sum(r.outcome == name for r in rows)

    refusals = [r for r in distress if r.sample.boundary]
    harassment = [c for c in conduct if c.sample.expected == "harassment"]
    return Counts(
        total=len(distress),
        handled=sum(r.as_hoped for r in distress),
        distress=s.real,
        distress_paused=s.paused_instantly + s.paused_by_meaning,
        distress_alerted=s.alerted_only,
        distress_missed=s.missed,
        refusals=len(refusals),
        refusals_alerted=outcome(refusals, "alerted"),
        refusals_paused=outcome(refusals, "paused"),
        refusals_missed=outcome(refusals, "missed"),
        ordinary=s.ordinary,
        ordinary_quiet=s.quiet,
        false_pauses=s.false_pauses,
        false_alerts=s.false_alerts,
        harassment=(
            (sum("harassment" in c.found for c in harassment), len(harassment)) if conduct else None
        ),
        unchecked=s.unchecked,
    )


def worst_conduct(passes: Sequence[Sequence[ConductResult]]) -> list[ConductResult]:
    """Each conduct sample once: caught only if it was caught in every pass, and ordinary
    talk acted on if it was in any."""
    if not passes:
        return []
    worst = []
    for rows in zip(*passes, strict=True):
        sample = rows[0].sample
        if sample.expected == conduct_set.NONE:
            found = frozenset().union(*(r.found for r in rows))
        else:
            found = frozenset.intersection(*(r.found for r in rows))
        worst.append(ConductResult(sample, found))
    return worst


@dataclass(frozen=True)
class Stability:
    """How a set's sentences held up across passes: which changed, with each pass's
    outcome, by text (dev) or position from 1 (held-out, independent)."""

    passes: int
    changed: tuple[tuple[str, tuple[str, ...]], ...]


def stability(run: SetRun) -> Stability:
    changed = []
    for k, rows in enumerate(zip(*run.distress, strict=True), start=1):
        outcomes = tuple(r.outcome for r in rows)
        if len(set(outcomes)) > 1:
            label = f"#{k}" if run.protected else " / ".join(rows[0].sample.finals)
            changed.append((label, outcomes))
    return Stability(len(run.distress), tuple(changed))


def single(run: SetRun) -> Counts:
    return counts(run.distress[0], run.conduct[0] if run.conduct else ())


def worst(run: SetRun) -> Counts:
    sentences, _ = per_sentence([r for rows in run.distress for r in rows])
    return counts(sentences, worst_conduct(run.conduct))


def passed(run: SetRun) -> bool:
    """The gate, on each sentence's worst outcome: decision 0005's distress targets and
    decision 0006's conduct targets."""
    sentences, _ = per_sentence([r for rows in run.distress for r in rows])
    return distress_summary(sentences).passed and not conduct_misses(worst_conduct(run.conduct))


# --- Running -------------------------------------------------------------------------------


def _post(url: str, body: dict[str, object]) -> None:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=UNLOAD_WAIT) as response:
        response.read()


def _loaded(url: str) -> list[str]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return [m.get("name", "") for m in json.loads(response.read()).get("models", [])]


def ollama_reload(model: LineModel, base_url: str, warm: Reload) -> Reload | None:
    """A reload for a model on Ollama: unload it (keep_alive 0), wait until the server no
    longer lists it, then warm it up, which loads it afresh. None for any other provider."""
    if not model.name.startswith("ollama/"):
        return None
    name, root = model.name.removeprefix("ollama/"), base_url.rstrip("/")

    async def reload() -> None:
        await asyncio.to_thread(_post, f"{root}/api/generate", {"model": name, "keep_alive": 0})
        deadline = time.monotonic() + UNLOAD_WAIT
        while name in await asyncio.to_thread(_loaded, f"{root}/api/ps"):
            if time.monotonic() > deadline:
                raise RuntimeError("Ollama didn't unload the model")
            await asyncio.sleep(0.25)
        await warm()

    return reload


def _order(samples: Sequence, k: int) -> list[int]:
    """The order of pass k: the set's own first, then a shuffle seeded by the pass."""
    order = list(range(len(samples)))
    if k:
        random.Random(k).shuffle(order)
    return order


async def run_set(
    classifier: LineModel,
    *,
    name: str,
    protected: bool,
    samples: tuple[Sample, ...],
    conduct: Sequence[conduct_set.ConductSample] = (),
    passes: int = FRESH_PASSES,
    reload: Reload | None,
    timeout: float,
    grace: float,
    max_wait: float,
) -> SetRun:
    """Every sample once per pass, each pass after a reload (when there is one), its
    distress set and conduct half reloaded apart."""
    distress_passes, conduct_passes = [], []
    for k in range(passes):
        order = _order(samples, k)
        if reload is not None:
            await reload()
        shuffled = tuple(samples[i] for i in order)
        heard = await run_distress(
            classifier, timeout=timeout, grace=grace, max_wait=max_wait, samples=shuffled
        )
        in_order = [r for _, r in sorted(zip(order, heard, strict=True), key=lambda p: p[0])]
        distress_passes.append(tuple(in_order))
        if conduct:
            c_order = _order(conduct, k)
            if reload is not None:
                await reload()
            found = await run_conduct(
                classifier,
                timeout=timeout,
                grace=grace,
                max_wait=max_wait,
                samples=[conduct[i] for i in c_order],
            )
            back = [r for _, r in sorted(zip(c_order, found, strict=True), key=lambda p: p[0])]
            conduct_passes.append(tuple(back))
    return SetRun(
        name, protected, tuple(distress_passes), tuple(conduct_passes), reload is not None
    )


# --- The report ----------------------------------------------------------------------------

ROWS: tuple[tuple[str, Callable[[Counts], str]], ...] = (
    ("handled as decided", lambda c: f"{c.handled}/{c.total}"),
    ("distress paused", lambda c: f"{c.distress_paused}/{c.distress}"),
    ("distress alerted only", lambda c: str(c.distress_alerted)),
    ("distress missed", lambda c: str(c.distress_missed)),
    ("refusals alerted", lambda c: f"{c.refusals_alerted}/{c.refusals}"),
    ("refusals paused", lambda c: str(c.refusals_paused)),
    ("refusals missed", lambda c: str(c.refusals_missed)),
    ("ordinary talk left alone", lambda c: f"{c.ordinary_quiet}/{c.ordinary}"),
    ("false pauses", lambda c: str(c.false_pauses)),
    ("false alerts", lambda c: str(c.false_alerts)),
    ("harassment", lambda c: f"{c.harassment[0]}/{c.harassment[1]}" if c.harassment else "-"),
    ("unchecked", lambda c: str(c.unchecked)),
)
TITLES = {"dev": "Development set", "held-out": "Held-out set", "independent": "Independent set"}


def render_set(run: SetRun | None, name: str, *, note: str = "") -> list[str]:
    title = TITLES.get(name, name)
    baseline = BASELINE.get(name)
    if run is None:
        recorded = "0/0, not yet written" if baseline is None else "not run"
        return [f"{title}: {note or recorded}"]
    first, worst_counts, stable = single(run), worst(run), stability(run)
    head = f"  {'':<26}{'single run':>12}{'worst of ' + str(stable.passes):>14}{'baseline':>12}"
    lines = [f"{title} ({first.total} sentences){note}", head]
    for label, cell in ROWS:
        recorded = cell(baseline) if baseline is not None else "-"
        lines.append(f"  {label:<26}{cell(first):>12}{cell(worst_counts):>14}{recorded:>12}")
    how = "a fresh model reload before each" if run.reloaded else "no reload: not on Ollama"
    lines.append(
        f"  Repeat stability: {stable.passes} passes ({how}); "
        f"{len(stable.changed)} of {first.total} sentences changed outcome"
    )
    for label, outcomes in stable.changed:
        lines.append(f"    {label}: {', '.join(outcomes)}")
    lines.append(f"  Gate on the worst outcomes: {'PASSED' if passed(run) else 'FAILED'}")
    return lines


def render(
    runs: dict[str, SetRun | None], *, model_name: str, notes: dict[str, str] | None = None
) -> str:
    notes = notes or {}
    passes = next((len(r.distress) for r in runs.values() if r is not None), FRESH_PASSES)
    lines = [
        f"Nova safety evaluation, the frozen check (decision 0005): {model_name}",
        f"Method: {passes} passes per set, each after a fresh model reload, the first in the "
        "set's order (the single run), the rest shuffled; each sentence counted at its worst "
        "outcome for the gate.",
        f"Baseline: {BASELINE_METHOD}; never re-derived with this method.",
        "",
    ]
    for name, run in runs.items():
        lines += [*render_set(run, name, note=notes.get(name, "")), ""]
    return "\n".join(lines).rstrip() + "\n"
