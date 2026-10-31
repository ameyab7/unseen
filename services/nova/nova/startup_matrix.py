"""The startup rehearsal's trials and report (decision 0011): how long Nova takes to be ready
from cold and from warm, and what the doors-hold does when a model is down or missing. The
LiveKit side is nova/startup_live.py; this part is pure, and tested
(tests/test_startup_matrix.py).

Kinds of trial, each a new room and session, as a show's first date would be:
- cold: the model unloaded from Ollama first, so the warm-up waits for a load;
- warm: the model already loaded;
- outage: Ollama unreachable for OUTAGE seconds from the start (the harness's own proxy
  doesn't listen yet), longer than the warm-up and the prep budget, so the doors hold and
  Nova must try again and open on its own;
- missing: a model Ollama hasn't got, a failure that can't pass: the moderator must be told
  at once that Nova won't be ready, and ends the date.

Numbers and codes only: nothing anyone said.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

AGENT_NAME = "nova-startup"
TOPIC = "unseen.startup"
OUTAGE = 45.0  # seconds Ollama is unreachable in an outage trial
MISSING_MODEL = "nova-not-pulled"  # a model Ollama hasn't got
BOUND_MS = 60_000  # the product owner's bound for ready from the session's start (0005)


class Kind(StrEnum):
    COLD = "cold"
    WARM = "warm"
    OUTAGE = "outage"
    MISSING = "missing"


# In order: cold before warm, so each warm trial follows a load.
PLAN = (
    Kind.COLD,
    Kind.WARM,
    Kind.COLD,
    Kind.WARM,
    Kind.COLD,
    Kind.WARM,
    Kind.OUTAGE,
    Kind.MISSING,
)


@dataclass
class Trial:
    """One session's startup, as Nova's side reported it (the `opened` event's numbers),
    with the harness's own timing from joining the room."""

    index: int
    kind: Kind
    opened: str = ""  # Opened's code: ready, recovered, moderator, ended
    startup_ms: int | None = None  # the warm-up's start to the decision
    warm_up_ms: int | None = None
    warm_up_error: str | None = None
    prep_ms: int = 0
    held_ms: int | None = None
    retries: int = 0
    reprepared: int = 0
    fallbacks: int = 0
    set_pieces: int = 0
    alerts: list[str] = field(default_factory=list)
    first_line: str = ""  # the first line's source once it opened: prepared or fallback
    # The harness: from joining the room to Nova's report, sent once its first line has played.
    joined_to_report_ms: int | None = None


@dataclass(frozen=True)
class Outcome:
    passed: bool
    why: str = ""


def outcome(trial: Trial) -> Outcome:
    """What each kind must show."""
    match trial.kind:
        case Kind.COLD | Kind.WARM:
            if trial.opened != "ready":
                return Outcome(False, f"opened {trial.opened or 'never'}, not ready in time")
            if trial.startup_ms is None or trial.startup_ms > BOUND_MS:
                return Outcome(False, "past the 60 s bound")
            if "nova_not_ready" in trial.alerts:
                return Outcome(False, "the moderator was told it wasn't ready")
        case Kind.OUTAGE:
            if trial.opened != "recovered":
                return Outcome(False, f"opened {trial.opened or 'never'}, not recovered")
            if trial.alerts[-2:] != ["nova_not_ready", "nova_ready"]:
                return Outcome(False, "the moderator wasn't told not ready, then ready")
            if trial.retries < 1 or trial.fallbacks == trial.set_pieces:
                return Outcome(False, "no try in the hold, or opened on fallbacks alone")
        case Kind.MISSING:
            if trial.opened != "ended":
                return Outcome(False, f"opened {trial.opened or 'never'}: it never should")
            if trial.alerts[-2:] != ["nova_not_ready", "nova_still_not_ready"]:
                return Outcome(False, "the moderator wasn't told it won't be ready")
            if trial.retries:
                return Outcome(False, "tried again after a failure that can't pass")
    return Outcome(True)


def _ms(values: Sequence[int]) -> str:
    if not values:
        return "-"
    return f"median {statistics.median(values) / 1000:.1f} s, worst {max(values) / 1000:.1f} s"


def render(trials: Sequence[Trial], *, started: str, model: str) -> str:
    """The report: each trial, then the measures the task asks for."""
    rows = [
        f"Nova startup rehearsal, {started}: {len(trials)} sessions, model {model}",
        "",
        f"  {'#':>2} {'kind':<8}{'opened':<11}{'ready in':>10}{'warm-up':>10}{'prep':>8}"
        f"{'held':>8}{'tries':>7}{'fallbacks':>11}  result",
    ]
    for t in trials:
        result = outcome(t)
        rows.append(
            f"  {t.index:>2} {t.kind:<8}{t.opened or '-':<11}"
            f"{_s(t.startup_ms):>10}{_s(t.warm_up_ms):>10}{_s(t.prep_ms):>8}{_s(t.held_ms):>8}"
            f"{t.retries:>7}{f'{t.fallbacks}/{t.set_pieces}':>11}  "
            + ("passed" if result.passed else f"FAILED: {result.why}")
        )
    by = {kind: [t for t in trials if t.kind is kind] for kind in Kind}
    opened = [t for t in trials if t.opened in ("ready", "recovered")]
    pieces = sum(t.set_pieces for t in opened)
    fell_back = sum(t.fallbacks for t in opened)
    failed = [t for t in trials if t.opened not in ("ready", "recovered")]
    cold = [t.startup_ms for t in by[Kind.COLD] if t.startup_ms]
    warm = [t.startup_ms for t in by[Kind.WARM] if t.startup_ms]
    rows += [
        "",
        f"Cold start (warm-up's start to ready): {_ms(cold)}",
        f"Warm start: {_ms(warm)}",
        "Tries while the doors held: "
        + ", ".join(f"{t.kind} {t.retries}" for t in trials if t.retries or t.held_ms)
        + ("" if any(t.retries or t.held_ms for t in trials) else "none"),
        f"Time held: {_ms([t.held_ms for t in trials if t.held_ms is not None])}",
        f"Fallback rate after warm-up, dates that opened: {fell_back} of {pieces} set pieces"
        + (f" ({fell_back / pieces:.0%})" if pieces else ""),
        f"Date-start failures (not opened by Nova itself): {len(failed)} of {len(trials)}, "
        f"{sum(t.kind is Kind.MISSING for t in failed)} of them the missing-model trial "
        "meant to fail",
        "",
        f"Passed: {sum(outcome(t).passed for t in trials)} of {len(trials)}",
    ]
    return "\n".join(rows)


def _s(ms: int | None) -> str:
    return "-" if ms is None else f"{ms / 1000:.1f} s"
