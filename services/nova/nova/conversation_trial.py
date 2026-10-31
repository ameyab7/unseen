"""Synthetic prefix/load gate; no audio or real daters, never a safety recall report.

    uv run python -m nova.conversation_trial [--rounds N]

Run alone, with local Ollama and the machine otherwise idle: the model shares the CPU, so
a busy machine times checks out. Each round makes the same ordinary safety check with
nothing else running, and again while each bridged question writes its prefix, so the
prefix contends with it as it would on air. Reports counts and timings only, and exits
non-zero if the gate fails: decision 0012's load condition for NOVA_LIVE_BRIDGES. Passing
doesn't enable prefixes, measure safety recall or show that a date sounds natural; the
two-person rehearsal and the product owner decide that.
"""

import argparse
import asyncio
import json
import math
import statistics
import time
from collections import Counter

from nova.cards import Seat
from nova.config import load_settings
from nova.cues import Moment, Say
from nova.date_context import DateContext
from nova.distress import DistressCheck, Verdict
from nova.fixtures import REHEARSAL_PAIR
from nova.lines import LineWriter
from nova.meaning import MeaningCheck
from nova.models import build_classifier, build_model, warm_up
from nova.priority import SafetyFirst
from nova.privacy import PrivacyPolicy

SENTENCE = "I like making pancakes on Sundays."
# The questions a prefix may lead into (nova/lines.py, BRIDGED), as the rehearsal asks them.
BRIDGED = (
    (Moment.FIRST_QUESTION, Seat.RIGHT),
    (Moment.FROM_THE_ROOM, None),
    (Moment.LAST_WORD, Seat.LEFT),
)
# Made-up exchanges, one a round, each what the daters just said before a bridged question.
EXCHANGES = (
    (
        "We got lost on a hiking adventure, but the view was worth it.",
        "I prefer cycling on flat roads, especially with a picnic.",
    ),
    (
        "I once baked bread every day for a week and gave it all away.",
        "I can't cook at all, but I make a very serious cup of tea.",
    ),
    (
        "My grandmother taught me cards, and she never once let me win.",
        "I learned chess from a library book when I was nine.",
    ),
    (
        "We drove to the coast at sunrise just to watch the fog lift.",
        "I'd rather sleep in and then find the best breakfast in town.",
    ),
    (
        "I sing in a choir on Thursdays, mostly for the company.",
        "I tried karaoke once and the whole room went quiet.",
    ),
)
ROUNDS = 14
# The gate, proposed (decision 0012): with prefixes contending, no safety check times out or
# changes its verdict, and the slowest 1% stay under this share of the distress timeout, so
# none runs close to going unchecked. With 42 checks, p99 is close to the worst.
P99_SHARE = 0.75


def timings(values: list[float]) -> dict[str, int]:
    ordered = sorted(values)
    return {
        "median_ms": round(statistics.median(ordered)),
        "p95_ms": round(_nearest_rank(ordered, 95)),
        "p99_ms": round(_nearest_rank(ordered, 99)),
        "max_ms": round(ordered[-1]),
        "samples": len(values),
    }


def _nearest_rank(ordered: list[float], percent: int) -> float:
    return ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]


def gate(report: dict[str, object], *, timeout: float) -> list[str]:
    """What fails decision 0012's load gate, if anything: codes and numbers only."""
    failures: list[str] = []
    unchecked = report["unchecked_probes"]
    if unchecked:
        failures.append(f"{unchecked} safety checks went unchecked (timed out)")
    unexpected = report["unexpected_probe_verdicts"]
    if unexpected:
        failures.append(f"{unexpected} safety checks gave another verdict than no")
    loaded = report["safety_probes_with_prefix"]
    assert isinstance(loaded, dict)
    limit = round(timeout * P99_SHARE * 1000)
    if loaded["p99_ms"] > limit:
        failures.append(f"p99 with prefixes {loaded['p99_ms']} ms, over {limit} ms")
    return failures


def _bridged(moment: Moment, seat: Seat | None) -> Say:
    return Say(
        moment,
        about=seat,
        detail="What would you bring to a picnic?" if moment is Moment.FROM_THE_ROOM else None,
        number=1 if moment is Moment.LIGHTNING_QUESTION else None,
        of=3 if moment is Moment.LIGHTNING_QUESTION else None,
        bridge=True,
    )


async def trial(rounds: int = ROUNDS) -> dict[str, object]:
    settings = load_settings()
    model, classifier = build_model(settings), build_classifier(settings)
    privacy = PrivacyPolicy.for_synthetic_data()
    # Avoid introducing another provider into an ostensibly local measurement.
    if not all(
        PrivacyPolicy.from_settings(settings).recipient(m).local for m in (model, classifier)
    ):
        raise ValueError("Run this trial with approved-local models only.")
    await warm_up(model, deadline=settings.warm_up)
    if classifier.name != model.name:
        await warm_up(classifier, deadline=settings.warm_up)
    priority = SafetyFirst()
    context = DateContext()
    writer = LineWriter(
        model,
        REHEARSAL_PAIR,
        context=context,
        privacy=privacy,
        gate=priority,
        live_timeout=settings.live_timeout,
        prep_budget=settings.prep_budget,
        meaning=MeaningCheck(classifier, timeout=settings.meaning_timeout, gate=priority),
    )
    await writer.prepare()
    check = DistressCheck(
        privacy.guard(classifier), timeout=settings.distress_timeout, gate=priority
    )
    idle: list[float] = []
    loaded: list[float] = []
    prefix_ms: list[float] = []
    said = 0
    errors: Counter[str] = Counter()
    unchecked = unexpected = 0
    for round_ in range(rounds):
        left, right = EXCHANGES[round_ % len(EXCHANGES)]
        context.heard(Seat.LEFT, left)  # the date goes on: these are the latest turns
        context.heard(Seat.RIGHT, right)
        for _ in BRIDGED:  # as many checks alone as with a prefix
            checked = await check.check(SENTENCE)
            idle.append(checked.ms)
            unchecked += checked.verdict is Verdict.UNCHECKED
            unexpected += checked.verdict is not Verdict.NO
        for moment, seat in BRIDGED:
            say = _bridged(moment, seat)
            began = time.monotonic()
            line = await writer.line_for(say)
            prefix_ms.append((time.monotonic() - began) * 1000)
            said += line.prefixed
            if not line.prefixed:
                why = line.error or (line.violations[0] if line.violations else None)
                errors[str(why) if why is not None else "not_tried"] += 1
            work = asyncio.create_task(writer.line_for(say))
            await asyncio.sleep(0)  # let the optional request contend with the next check
            checked = await check.check(SENTENCE)
            loaded.append(checked.ms)
            unchecked += checked.verdict is Verdict.UNCHECKED
            unexpected += checked.verdict is not Verdict.NO
            await work
    writer.finish()
    context.close()
    report: dict[str, object] = {
        "synthetic_only": True,
        "rounds": rounds,
        "prefixes": {"attempts": len(prefix_ms), "said": said, "fell_back": dict(errors)}
        | timings(prefix_ms),
        "safety_probes_idle": timings(idle),
        "safety_probes_with_prefix": timings(loaded),
        "unchecked_probes": unchecked,
        "unexpected_probe_verdicts": unexpected,
        "safety_recall_measured": False,
        "human_audio_acceptance": "pending",
    }
    failures = gate(report, timeout=settings.distress_timeout)
    report["gate"] = {
        "passed": not failures,
        "failures": failures,
        "p99_limit_ms": round(settings.distress_timeout * P99_SHARE * 1000),
    }
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=ROUNDS, help="rounds of checks")
    args = parser.parse_args(argv)
    report = asyncio.run(trial(args.rounds))
    print(json.dumps(report, indent=2))
    result = report["gate"]
    assert isinstance(result, dict)
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
