"""The startup rehearsal's trials and report (nova/startup_matrix.py, decision 0011): what
each kind of session must show, and the measures the report gives. The LiveKit side
(nova/startup_live.py) runs only on demand, in Docker."""

from nova.startup_matrix import BOUND_MS, PLAN, Kind, Trial, outcome, render


def ready(index: int, kind: Kind, ms: int) -> Trial:
    return Trial(
        index, kind, opened="ready", startup_ms=ms, warm_up_ms=ms - 11_000, prep_ms=11_000,
        fallbacks=0, set_pieces=10, first_line="prepared",
    )  # fmt: skip


def recovered() -> Trial:
    return Trial(
        6, Kind.OUTAGE, opened="recovered", startup_ms=78_000, held_ms=62_000, retries=5,
        reprepared=1, fallbacks=0, set_pieces=10, alerts=["nova_not_ready", "nova_ready"],
    )  # fmt: skip


def missing() -> Trial:
    return Trial(
        7, Kind.MISSING, opened="ended", startup_ms=1_200, held_ms=900, fallbacks=10,
        set_pieces=10, alerts=["nova_not_ready", "nova_still_not_ready"],
    )  # fmt: skip


def test_the_plan_has_cold_and_warm_sessions_and_both_ways_the_hold_ends():
    assert PLAN.count(Kind.COLD) >= 3 and PLAN.count(Kind.WARM) >= 3
    assert Kind.OUTAGE in PLAN and Kind.MISSING in PLAN
    first_warm = PLAN.index(Kind.WARM)
    assert Kind.COLD in PLAN[:first_warm], "a warm session follows a load"


def test_cold_and_warm_must_be_ready_in_time_with_nobody_alerted():
    assert outcome(ready(0, Kind.COLD, 30_000)).passed
    assert not outcome(ready(0, Kind.COLD, BOUND_MS + 1)).passed
    late = ready(1, Kind.WARM, 2_000)
    late.alerts = ["nova_not_ready"]
    assert not outcome(late).passed


def test_an_outage_must_be_held_tried_again_and_opened_by_nova():
    assert outcome(recovered()).passed
    on_fallbacks = recovered()
    on_fallbacks.fallbacks = 10
    assert not outcome(on_fallbacks).passed, "never opened on fallbacks alone"
    by_moderator = recovered()
    by_moderator.opened = "moderator"
    assert not outcome(by_moderator).passed


def test_a_missing_model_must_be_reported_at_once_and_never_tried_again():
    assert outcome(missing()).passed
    retried = missing()
    retried.retries = 2
    assert not outcome(retried).passed
    opened = missing()
    opened.opened = "moderator"
    assert not outcome(opened).passed


def test_the_report_gives_every_measure_asked_for():
    trials = [
        ready(0, Kind.COLD, 30_000),
        ready(1, Kind.WARM, 1_500),
        ready(2, Kind.COLD, 29_000),
        ready(3, Kind.WARM, 1_200),
        recovered(),
        missing(),
    ]
    report = render(trials, started="2026-10-24 12:00", model="gemma4:26b")
    assert "Cold start (warm-up's start to ready): median 29.5 s, worst 30.0 s" in report
    assert "Warm start: median 1.4 s, worst 1.5 s" in report
    assert "Tries while the doors held: outage 5, missing 0" in report
    assert "Time held: median 31.4 s, worst 62.0 s" in report
    assert "Fallback rate after warm-up, dates that opened: 0 of 50 set pieces (0%)" in report
    assert "Date-start failures (not opened by Nova itself): 1 of 6, 1 of them" in report
    assert "Passed: 6 of 6" in report
