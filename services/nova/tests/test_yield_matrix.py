"""The yield rehearsal's arithmetic (nova/yield_matrix.py), on made-up audio levels. The
rehearsal itself runs in a real LiveKit room (nova/yield_live.py, Docker only); these check
how its trials are read, and that a run without real LiveKit audio never counts."""

from dataclasses import replace

import pytest

from nova.rehearsal import Then
from nova.yield_matrix import (
    MATRIX,
    TEST_LINE,
    Plan,
    Proof,
    Scenario,
    Trial,
    calibrate_gap,
    outcome,
    pauses,
    percentile,
    problems,
    render,
    rms,
    silent,
    sound_runs,
)


def audio(*spans: tuple[float, float], until: float | None = None) -> list[tuple[float, float]]:
    """(time, rms) every 10 ms: loud inside the spans, quiet elsewhere."""
    end = until if until is not None else max(e for _, e in spans) + 2.0
    frames = []
    for step in range(round(end * 100)):
        t = step / 100
        loud = any(s <= t < e for s, e in spans)
        frames.append((t, 0.1 if loud else 0.0))
    return frames


def test_rms_is_a_share_of_full_scale():
    assert rms([]) == 0.0
    assert rms([32767, -32767]) == pytest.approx(1.0, abs=1e-4)
    assert rms([0, 0, 0]) == 0.0


def test_nova_s_audio_splits_into_stretches_only_at_a_long_quiet():
    """A pause between Nova's sentences is shorter than SILENCE: still one stretch."""
    frames = audio((0.0, 2.0), (2.5, 4.0), (5.5, 6.0))
    runs = sound_runs(frames)
    assert [(round(s, 2), round(e, 2)) for s, e in runs] == [(0.0, 4.0), (5.5, 6.0)]


def test_pauses_count_the_sentences_that_had_ended():
    frames = audio((0.0, 1.0), (1.3, 2.0), (2.05, 3.0))
    assert pauses(frames, 0.0, 3.0, gap=0.2) == 1, "0.3 s counts, 0.05 s doesn't"
    assert pauses(frames, 0.0, 1.5, gap=0.2) == 1
    assert pauses(frames, 0.0, 0.9, gap=0.2) == 0


def test_a_long_pause_between_sentences_is_still_one_stretch():
    """The smoke run of 2026-10-23 heard pauses of up to 1.03 s; after a cut, Nova waits
    1.5 s of quiet, which still ends a stretch."""
    frames = audio((0.0, 2.0), (3.05, 4.0), (5.6, 6.0))
    runs = sound_runs(frames)
    assert [(round(s, 2), round(e, 2)) for s, e in runs] == [(0.0, 4.0), (5.6, 6.0)]


def test_a_click_before_the_first_word_is_no_sentence():
    frames = audio((0.0, 0.01), (0.5, 2.0), (2.4, 3.0))
    assert pauses(frames, 0.0, 3.0, gap=0.2) == 1, "the sentence ending only, not the click"


def test_the_sentence_gap_is_calibrated_from_whole_lines():
    """Three pauses of 0.35 s between four sentences, and 0.1 s pauses inside them."""
    line = audio((0.0, 1.0), (1.1, 2.0), (2.35, 4.0), (4.35, 6.0), (6.35, 8.0))
    assert calibrate_gap([line, line]) == 0.12
    assert calibrate_gap([audio((0.0, 3.0))]) is None, "one sentence: no gap splits it in four"


def test_percentiles_are_nearest_rank():
    values = list(range(1, 21))
    assert percentile(values, 0.5) == 10 and percentile(values, 0.95) == 19
    assert percentile([7], 0.95) == 7


EARLY = next(plan for plan in MATRIX if plan.scenario is Scenario.EARLY)


def a_yield(began: float, **fields) -> dict:
    return {
        "moment": "silence",
        "stop_ms": 200,
        "played_ms": 800,
        "sentence": 0,
        "sentences": 4,
        "then": "resumed",
        "began": began,
        "cut": began,
        **fields,
    }


def test_a_cut_is_read_from_the_dater_s_onset_both_as_heard_and_at_the_agent():
    """Nova from 10.0 s, the dater from 10.3 s, the detector at 10.5 s, the voice stopped
    200 ms later at the agent, the last of Nova's audio received at 10.9 s."""
    trial = Trial(
        0,
        Scenario.EARLY,
        9.0,
        nova=audio((10.0, 10.9), (14.0, 25.0)),
        onsets=[10.3],
        ends=[12.5],
        agent={"yields": [a_yield(10.5)]},
    )
    got = outcome(trial, EARLY, line_s=11.0, gap=0.2)
    assert got.detector_ms == [200] and got.agent_ms == [400]
    assert got.heard_ms == [600], "to the end of the last loud frame received"
    assert got.cut_error_ms == [100], "the dater heard 900 ms, the agent says 800 ms played"
    assert (got.missed, got.false_yields, got.duplicate) == (0, 0, False)
    assert got.then == [Then.RESUMED]
    assert (got.sentence_checked, got.sentence_mismatch) == (1, 0)


def test_a_cut_is_counted_from_when_the_agent_started_the_line():
    """As in the smoke run of 2026-10-23: a click, then sentences with a pause longer than
    SILENCE before the cut. What was heard counts from the line's first sound after the
    voice started it, so the cut lands in the third sentence, as the agent says."""
    nova = audio((10.0, 10.01), (10.5, 12.0), (12.4, 14.0), (15.1, 17.0), (21.0, 22.0))
    trial = Trial(
        0,
        Scenario.LATE,
        9.0,
        nova=nova,
        onsets=[16.3],
        ends=[18.0],
        agent={
            "yields": [a_yield(16.6, cut=16.9, sentence=2, played_ms=7100)],
            "speaks": [[9.8, 9.9, 16.9, 177], [20.8, 20.9, 22.0, 42]],
        },
    )
    late = next(plan for plan in MATRIX if plan.scenario is Scenario.LATE)
    got = outcome(trial, late, line_s=11.0, gap=0.2)
    assert got.cut_error_ms == [-100], "heard from 10.0 s to 17.0 s, the agent says 7.1 s"
    assert (got.sentence_checked, got.sentence_mismatch) == (1, 0)


def test_nova_talking_on_over_a_dater_is_a_missed_yield():
    trial = Trial(0, Scenario.MIDDLE, 0.0, nova=audio((1.0, 12.0)), onsets=[6.0], ends=[8.0])
    plan = next(p for p in MATRIX if p.scenario is Scenario.MIDDLE)
    assert outcome(trial, plan, line_s=11.0, gap=None).missed == 1


def test_a_yield_with_nobody_speaking_is_false_and_a_second_whole_line_a_duplicate():
    """The echo trials: Nova stopping for its own voice."""
    trial = Trial(
        0,
        Scenario.ECHO,
        0.0,
        nova=audio((1.0, 5.0), (6.5, 17.5), (19.0, 30.0)),
        agent={"yields": [a_yield(4.8, then="restarted", sentence=None)]},
    )
    plan = next(p for p in MATRIX if p.scenario is Scenario.ECHO)
    got = outcome(trial, plan, line_s=11.0, gap=0.2)
    assert got.false_yields == 1 and got.duplicate


def test_a_restart_within_its_bound_is_no_duplicate():
    trial = Trial(
        0,
        Scenario.EARLY,
        0.0,
        nova=audio((1.0, 1.5), (4.0, 15.0)),
        onsets=[1.2],
        agent={"yields": [a_yield(1.3, then="restarted", sentence=None)]},
    )
    got = outcome(trial, EARLY, line_s=11.0, gap=0.2)
    assert not got.duplicate and got.sentence_checked == 0


def test_going_on_between_the_dater_s_sentences_is_counted():
    plan = next(p for p in MATRIX if p.scenario is Scenario.PAUSES)
    trial = Trial(
        0,
        Scenario.PAUSES,
        0.0,
        nova=audio((1.0, 5.0), (7.0, 8.0), (12.0, 20.0)),
        onsets=[4.5],
        ends=[10.5],
        agent={"yields": [a_yield(4.7), a_yield(7.3)]},
    )
    assert outcome(trial, plan, line_s=11.0, gap=None).went_on_mid_speech == 1


def test_the_stop_request_is_timed_to_its_alert():
    plan = next(p for p in MATRIX if p.scenario is Scenario.STOP)
    trial = Trial(
        0,
        Scenario.STOP,
        0.0,
        nova=audio((1.0, 6.5), (9.0, 15.0)),
        onsets=[6.0],
        ends=[7.0],
        agent={"yields": [a_yield(6.2)], "alerts": [[8.1, "dater_distressed"]]},
    )
    assert outcome(trial, plan, line_s=11.0, gap=None).alert_ms == [2100]


def test_the_matrix_covers_every_case_asked_for():
    """Early, middle and late in a line; twice in one line; pauses between a dater's
    sentences; a TV; a stale cut; delayed audio; no interruption; Nova's own echo."""
    covered = {plan.scenario for plan in MATRIX}
    assert covered == set(Scenario)
    assert MATRIX[0].scenario is Scenario.NONE, "the whole line is measured first"
    twice = next(p for p in MATRIX if p.scenario is Scenario.TWICE)
    assert twice.again is not None
    assert next(p for p in MATRIX if p.scenario is Scenario.DELAYED).delay > 0
    echo = {p.scenario: p for p in MATRIX if p.echo}
    assert not echo[Scenario.ECHO].aec and echo[Scenario.ECHO_AEC].aec
    assert 40 <= sum(p.trials for p in MATRIX) <= 60


REAL = Proof(
    room="nova-yield-x",
    room_sid="RM_abc",
    agent_identity="agent-AJ_x",
    agent_kind="agent",
    agent_track_sid="TR_def",
    agent_track_source="SOURCE_MICROPHONE",
    harness_identity="yield-dater",
    agent_linked="yield-dater",
    frames=60_000,
    sound_s=300.0,
    agent_heard=40,
    timed_words=True,
)


def test_a_run_with_real_livekit_audio_both_ways_counts():
    assert problems(REAL) == []


def test_a_run_where_nova_s_voice_failed_never_counts():
    """The first full run: LiveKit's voice service refused Nova's lines (429) from the sixth
    trial on, and the report read "0 missed" for trials in which Nova never spoke."""
    failed = replace(REAL, trials=51, silent_trials=45, voice_errors=45)
    assert problems(failed) == [
        "Nova's line never reached the dater in 45 of 51 trials (45 voice errors): run it again"
    ]
    assert silent(Trial(0, Scenario.MIDDLE, 0.0, nova=audio((1.0, 1.5))))
    assert not silent(Trial(0, Scenario.MIDDLE, 0.0, nova=audio((1.0, 12.0))))


def test_a_text_only_or_faked_run_never_counts_as_a_livekit_rehearsal():
    """A run whose voice and listener were mocks has no room or track LiveKit gave, no audio
    received over WebRTC, and no voice detector hearing the dater's track."""
    found = problems(Proof(harness_identity="yield-dater"))
    assert found == [
        "no LiveKit room id",
        "Nova wasn't an agent participant in the room",
        "no audio track from Nova",
        "too little of Nova's audio received over WebRTC",
        "the agent's voice detector never heard the dater's track",
        "the agent didn't listen to the dater's track",
    ]
    report = render(Proof(), [], started="2026-10-22 12:00")
    assert report.splitlines()[0].startswith("Nova yield rehearsal, 2026-10-22 12:00: NOT VALID")


def test_the_report_is_numbers_never_the_words():
    trial = Trial(
        0,
        Scenario.NONE,
        0.0,
        nova=audio((1.0, 3.0), (3.35, 6.0), (6.35, 9.0), (9.35, 12.0)),
        agent={"yields": []},
    )
    report = render(REAL, [trial], started="2026-10-22 12:00")
    assert report.splitlines()[0].endswith("real LiveKit audio both ways")
    for words in (TEST_LINE[:30], "story about", "I want to stop now"):
        assert words not in report


def test_a_plan_says_what_it_does():
    plan = Plan(Scenario.EARLY, 1, "x", "cut_in", 0.0, 0.3)
    assert plan.expect_yield and plan.delay == 0.0 and not plan.echo
