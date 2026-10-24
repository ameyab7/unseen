"""People's ratings of the benchmark's blind pairs (nova/ratings.py), on made-up dialogues
and sheets: the packet hides which version is which, and what sheets add up to never calls
a difference within its uncertainty a win. No sheet here is anyone's real rating."""

import json

import pytest

from nova.ratings import (
    BASELINE,
    CANDIDATE,
    DIMENSIONS,
    RUBRIC,
    bootstrap,
    main,
    make_packet,
    render,
    sign_test,
    summarize,
    wilson,
)


def dialogues(n: int) -> dict:
    """n sources, each run both ways; the dialogue says nothing about its variant."""
    return {
        f"scenario_{k}-1": {
            BASELINE: [["Nova", f"Line {k} one way."], ["Ana", "Ha, sure."]],
            CANDIDATE: [["Nova", f"Line {k} another way."], ["Ana", "Ha, yes."]],
        }
        for k in range(n)
    }


def test_the_rubric_is_fixed_before_rating_and_covers_every_dimension_asked_for():
    assert DIMENSIONS == (
        "naturalness",
        "listening",
        "dater_to_dater",
        "playful_energy",
        "pacing",
        "groundedness",
        "safety",
    )
    assert all(low and high for low, high in RUBRIC.values()), "both ends of each scale"


def test_the_packet_hides_the_version_scenario_and_order():
    key, text, sheet = make_packet(dialogues(30), seed=1)
    assert list(key) == [f"P{n:02d}" for n in range(1, 31)], "neutral ids, in packet order"
    assert sum(pair["A"] == CANDIDATE for pair in key.values()) == 15, "candidate as A in half"
    for word in ("baseline", "candidate", "scenario_"):
        assert word not in text.lower()
    sources = [pair["source"] for pair in key.values()]
    assert sources != sorted(sources), "not in scenario order"
    assert set(sheet["pairs"]) == set(key)
    rated = sheet["pairs"]["P01"]
    assert set(rated["A"]) == set(DIMENSIONS) and rated["prefer"] is None and rated["why"] == ""
    assert set(sheet) == {"rater", "rubric", "pairs"}, "initials and ratings, nothing personal"


def test_the_packet_is_the_same_for_the_same_seed_and_skips_a_source_run_one_way():
    first, second = make_packet(dialogues(6), seed=3), make_packet(dialogues(6), seed=3)
    assert first == second
    one_way = {**dialogues(2), "lonely-1": {BASELINE: [["Nova", "Hello."]]}}
    key, _, _ = make_packet(one_way)
    assert len(key) == 2


def sheet_for(key: dict, prefer: dict[str, str], *, rater: str, scores=None) -> dict:
    """A made-up sheet: prefer maps a pair id to "candidate", "baseline" or "same"."""
    pairs = {}
    for pid, choice in prefer.items():
        pair = key[pid]
        label = next((side for side in "AB" if pair[side] == choice), "same")
        a = scores(pair["A"]) if scores else dict.fromkeys(DIMENSIONS, 3)
        b = scores(pair["B"]) if scores else dict.fromkeys(DIMENSIONS, 3)
        pairs[pid] = {"A": a, "B": b, "prefer": label, "why": "made up for the test"}
    return {"rater": rater, "pairs": pairs}


def test_a_small_lead_within_the_uncertainty_is_no_win():
    """16 to 14 over 30 pairs: the interval holds an even split."""
    key, _, _ = make_packet(dialogues(30), seed=1)
    ids = list(key)
    prefer = {pid: CANDIDATE if k < 16 else BASELINE for k, pid in enumerate(ids)}
    got = summarize([sheet_for(key, prefer, rater="AB")], key)
    assert (got["wins"], got["ties"], got["losses"]) == (16, 0, 14)
    low, high = got["win_share_ci"]
    assert low < 0.5 < high
    assert got["verdict"] == "no clear difference: within the uncertainty"
    assert got["sign_test_p"] == pytest.approx(0.856, abs=0.01)


def test_a_clear_preference_is_reported_as_one():
    key, _, _ = make_packet(dialogues(30), seed=1)
    prefer = {pid: CANDIDATE if k < 25 else _otherwise(k) for k, pid in enumerate(key)}
    got = summarize([sheet_for(key, prefer, rater="AB")], key)
    assert got["verdict"] == "the candidate preferred" and got["win_share_ci"][0] > 0.5


def _otherwise(k: int) -> str:
    return "same" if k % 2 else BASELINE


def test_each_pair_counts_once_however_many_raters():
    """Two raters who split on a pair make it a tie; agreeing raters count it once."""
    key, _, _ = make_packet(dialogues(4), seed=1)
    ids = list(key)
    first = sheet_for(key, dict.fromkeys(ids, CANDIDATE), rater="AB")
    second = sheet_for(key, {ids[0]: BASELINE, **{pid: CANDIDATE for pid in ids[1:]}}, rater="CD")
    got = summarize([first, second], key)
    assert (got["wins"], got["ties"], got["losses"], got["rated"]) == (3, 1, 0, 4)
    (agreement,) = got["agreement"]
    assert agreement["raters"] == ["AB", "CD"] and agreement["agree"] == 0.75
    assert got["per_rater"]["CD"] == {CANDIDATE: 3, "same": 0, BASELINE: 1}


def test_scores_unblind_by_side_and_report_the_candidate_minus_the_baseline():
    key, _, _ = make_packet(dialogues(20), seed=1)

    def scores(variant: str) -> dict:
        return {d: (4 if variant == CANDIDATE and d == "groundedness" else 3) for d in DIMENSIONS}

    got = summarize([sheet_for(key, dict.fromkeys(key, "same"), rater="AB", scores=scores)], key)
    assert got["dimensions"]["groundedness"]["mean"] == 1.0
    assert got["dimensions"]["groundedness"]["ci"] == (1.0, 1.0)
    assert got["dimensions"]["naturalness"]["mean"] == 0.0
    assert "groundedness    +1.00 (+1.00 to +1.00) (outside an even result)" in render(got)


def test_scores_out_of_range_or_missing_are_left_out():
    key, _, _ = make_packet(dialogues(2), seed=1)
    pid = next(iter(key))
    sheet = {"rater": "AB", "pairs": {pid: {"A": {"pacing": 9}, "B": {"pacing": True}}}}
    got = summarize([sheet], key)
    assert got["dimensions"]["pacing"]["pairs"] == 0 and got["rated"] == 0


def test_with_no_ratings_the_result_is_pending_never_estimated():
    key, _, sheet = make_packet(dialogues(30), seed=1)
    text = render(summarize([sheet], key))
    assert text.startswith("Human ratings: pending (0 of 30 pairs rated)")


def test_a_single_rater_is_reported_as_a_limit():
    key, _, _ = make_packet(dialogues(4), seed=1)
    got = summarize([sheet_for(key, dict.fromkeys(key, CANDIDATE), rater="AB")], key)
    assert "one rater: no check that another person would agree" in render(got)


def test_the_statistics():
    assert wilson(0, 0) == (0.0, 1.0)
    low, high = wilson(15, 30)
    assert low < 0.5 < high
    assert sign_test(0, 0) == 1.0 and sign_test(10, 0) == pytest.approx(2 / 1024)
    assert bootstrap([2.0, 2.0, 2.0]) == (2.0, 2.0)


def test_the_command_writes_the_rater_folder_apart_from_the_key(tmp_path):
    source = tmp_path / "dialogues.json"
    source.write_text(json.dumps(dialogues(4)), encoding="utf-8")
    assert main(["packet", "--dialogues", str(source), "--out", str(tmp_path / "out")]) == 0
    raters = tmp_path / "out" / "for-raters"
    assert sorted(p.name for p in raters.iterdir()) == ["packet.md", "sheet.json"]
    assert (tmp_path / "out" / "key.json").exists()
    assert "candidate" not in (raters / "packet.md").read_text(encoding="utf-8").lower()
