"""The mute rehearsal's arithmetic (nova/mute_matrix.py), on made-up trials: what passes and
fails each scenario, and when a run counts as real. No LiveKit."""

import pytest

from nova.mute_matrix import MATRIX, Proof, Scenario, Trial, outcome, problems, render
from nova.yield_matrix import FRAME_SECONDS


def sound(start: float, end: float) -> list[tuple[float, float]]:
    """Nova heard from start to end, then quiet frames until end + 5 s."""
    frames, t = [], start
    while t < end + 5.0:
        frames.append((round(t, 3), 0.1 if t < end else 0.0))
        t += FRAME_SECONDS
    return frames


def test_a_mute_mid_line_that_stops_holds_and_speaks_again_passes():
    t = Trial(0, Scenario.MID_LINE, nova=sound(0.0, 4.6) + sound(10.2, 12.0)[:100])
    t.sent = [(4.0, "moderator", "mute", 5), (9.0, "moderator", "unmute", 6)]
    t.acks = [(4.1, "applied", True, 5), (9.1, "applied", False, 6)]
    t.marks = {"play": 0.0, "unmute": 9.0}
    got = outcome(t)
    assert got.passed and got.stop_ms == pytest.approx(600, abs=15) and got.ack_ms == 100


def test_a_line_that_plays_on_after_the_mute_fails():
    t = Trial(0, Scenario.MID_LINE, nova=sound(0.0, 11.0))
    t.sent = [(4.0, "moderator", "mute", 5), (12.0, "moderator", "unmute", 6)]
    t.acks = [(4.1, "applied", True, 5)]
    t.marks = {"play": 0.0, "unmute": 12.0}
    assert not outcome(t).passed


def test_an_impostor_who_got_an_answer_or_stopped_nova_fails():
    good = Trial(0, Scenario.IMPOSTOR, nova=sound(0.0, 11.0), marks={"impostor": 4.0})
    good.agent = {"muted": False}
    assert outcome(good).passed
    answered = Trial(1, Scenario.IMPOSTOR, nova=sound(0.0, 11.0), marks={"impostor": 4.0})
    answered.impostor_acks, answered.agent = 1, {"muted": False}
    assert not outcome(answered).passed
    stopped = Trial(2, Scenario.IMPOSTOR, nova=sound(0.0, 4.5), marks={"impostor": 4.0})
    stopped.agent = {"muted": True}
    assert not outcome(stopped).passed


def test_stale_commands_must_be_answered_stale_and_change_nothing():
    t = Trial(0, Scenario.STALE, nova=sound(0.0, 11.0), marks={"stale": 4.0})
    t.acks, t.agent = [(4.1, "stale", False, 1), (4.2, "stale", False, 7)], {"muted": False}
    assert outcome(t).passed
    t.acks = [(4.1, "applied", True, 1)]
    assert not outcome(t).passed


def test_a_duplicate_is_one_mute():
    t = Trial(0, Scenario.DUPLICATE, nova=sound(0.0, 4.5))
    t.acks = [(4.1, "applied", True, 5), (4.3, "stale", True, 5), (8.0, "applied", False, 9)]
    t.agent = {"mutes_delta": 1}
    assert outcome(t).passed
    t.agent = {"mutes_delta": 2}
    assert not outcome(t).passed


def test_a_lost_connection_must_leave_nova_muted_and_tell_the_returning_moderator():
    t = Trial(0, Scenario.LOST, nova=sound(10.0, 12.0), marks={"play": 2.0, "unmute": 9.0})
    t.rejoined_state = (8.0, True)
    assert outcome(t).passed
    t.rejoined_state = (8.0, False)
    assert not outcome(t).passed, "the console must be told the mute stands"
    t.rejoined_state, t.nova = (8.0, True), sound(3.0, 5.0)
    assert not outcome(t).passed, "Nova spoke while muted"


def test_safety_while_muted_needs_the_alert_and_silence():
    t = Trial(0, Scenario.SAFETY, marks={"clip": 1.0, "unmute": 8.0})
    t.agent = {"alerts": ["dater_distressed"]}
    assert outcome(t).passed
    t.agent = {"alerts": []}
    assert not outcome(t).passed


def test_after_a_cut_a_muted_nova_never_resumes():
    t = Trial(0, Scenario.AFTER_CUT, nova=sound(0.0, 5.0), marks={"unmute": 12.0})
    t.clip_ends, t.agent = [7.0], {"yields_then": ["stopped"]}
    assert outcome(t).passed
    t.nova = sound(0.0, 5.0) + sound(8.0, 10.0)
    assert not outcome(t).passed


def test_a_run_counts_only_with_real_audio_and_the_moderators_role_in_the_room():
    real = Proof("RM_x", "agent", "TR_y", 5000, 60.0, {"role": "moderator"}, 30, 0, 24)
    assert problems(real) == []
    for broken, why in (
        (Proof("", "agent", "TR_y", 5000, 60.0, {"role": "moderator"}, 30), "room"),
        (Proof("RM_x", "agent", "TR_y", 5000, 60.0, {}, 30), "attribute"),
        (Proof("RM_x", "agent", "TR_y", 5000, 60.0, {"role": "moderator"}, 0), "state"),
        (Proof("RM_x", "agent", "TR_y", 5000, 60.0, {"role": "moderator"}, 30, 2), "voice"),
    ):
        assert any(why in p for p in problems(broken)), why


def test_the_report_has_every_scenario_and_no_words():
    trials = [Trial(k, plan.scenario) for k, plan in enumerate(MATRIX)]
    for t in trials:
        t.marks = {"play": 0.0, "impostor": 1.0, "stale": 1.0, "clip": 1.0}
    report = render(Proof(), trials, started="2026-10-23 12:00")
    assert "NOT VALID" in report
    for plan in MATRIX:
        assert plan.scenario in report


def test_the_stop_is_the_end_of_what_nova_was_saying_never_the_next_line():
    """A first scoring read the last sound within 5 s of the mute, which was the line Nova
    said after the unmute (the mute rehearsal of 2026-10-23)."""
    t = Trial(0, Scenario.MID_LINE, nova=sound(0.0, 4.6)[:700] + sound(8.0, 12.0))
    t.sent = [(4.0, "moderator", "mute", 5), (7.5, "moderator", "unmute", 6)]
    t.acks = [(4.1, "applied", True, 5), (7.6, "applied", False, 6)]
    t.marks = {"play": 0.0, "unmute": 7.5}
    assert outcome(t).stop_ms == pytest.approx(600, abs=15)


def test_a_stale_mute_has_no_stop_to_measure():
    t = Trial(0, Scenario.STALE, nova=sound(0.0, 11.0), marks={"stale": 4.0})
    t.sent = [(4.0, "moderator", "mute", 1)]
    t.acks, t.agent = [(4.1, "stale", False, 1)], {"muted": False}
    assert outcome(t).stop_ms is None
