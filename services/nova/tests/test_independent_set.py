"""The independent held-out set's loader and separation check (nova/independent_set.py).

Never the real set: every file here is written to a temporary folder, with placeholder
words no dater would say."""

import json
from pathlib import Path

import pytest

from nova import conduct_set
from nova.distress_dev import DEV_SET
from nova.distress_set import DISTRESS_SET
from nova.independent_set import (
    DEFAULT_PATH,
    IndependentSetError,
    TooClose,
    fingerprint,
    known_lines,
    load,
    separation,
)

FRESH = [
    {"said": "placeholder zebra marmalade question one", "expected": "pause"},
    {"said": ["placeholder zebra", "marmalade two"], "expected": "nothing"},
    {
        "said": "placeholder zebra reply three",
        "other": "placeholder zebra prompt three",
        "expected": "alert",
    },
    {
        "said": "placeholder zebra four",
        "earlier": "placeholder zebra before four",
        "expected": "nothing",
    },
]


def written(tmp_path: Path, samples: list, **header: str) -> Path:
    path = tmp_path / "distress.json"
    data = {"author": "someone else", "written": "2026-11-02", "samples": samples, **header}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_each_expected_outcome_becomes_a_sample_the_evaluation_scores(tmp_path):
    pause, split, alert, after = load(written(tmp_path, FRESH))
    assert (pause.distress, pause.boundary) == (True, False)
    assert (split.distress, split.split) == (False, ("placeholder zebra", "marmalade two"))
    assert (alert.distress, alert.boundary, alert.other) == (
        True,
        True,
        ("placeholder zebra prompt three",),
    )
    assert after.earlier == ("placeholder zebra before four",) and not after.distress


@pytest.mark.parametrize(
    ("samples", "problem"),
    [
        ([{"said": "placeholder zebra secret", "expected": "maybe"}], "sample 1: expected"),
        ([{"said": "", "expected": "pause"}], "sample 1: said"),
        ([{"said": ["a", "b", "c"], "expected": "pause"}], "sample 1: said"),
        ([*FRESH, {"said": "placeholder zebra secret", "other": 5, "expected": "pause"}], "5:"),
        ([], "no samples"),
    ],
)
def test_a_bad_file_names_the_position_and_field_never_the_words(tmp_path, samples, problem):
    with pytest.raises(IndependentSetError) as caught:
        load(written(tmp_path, samples))
    assert problem in str(caught.value) and "secret" not in str(caught.value)


def test_a_file_without_its_author_is_refused(tmp_path):
    with pytest.raises(IndependentSetError, match="author"):
        load(written(tmp_path, FRESH, author=""))


def test_fresh_samples_are_separate(tmp_path):
    assert separation(load(written(tmp_path, FRESH))) == []


@pytest.mark.parametrize(
    "known",
    [DEV_SET[3].sentence, conduct_set.DEV_HALF[2].sentence],
    ids=["a dev sentence", "a conduct sentence"],
)
def test_a_sample_that_paraphrases_a_known_line_is_named_by_position(tmp_path, known):
    samples = [*FRESH[:1], {"said": known, "expected": "nothing"}]
    close = separation(load(written(tmp_path, samples)))
    assert close == [TooClose(2, 1.0)]


def test_a_line_a_sample_answers_counts_too(tmp_path):
    answered = next(line for s in DEV_SET for line in s.other)
    samples = [{"said": "placeholder zebra five", "other": answered, "expected": "alert"}]
    assert [c.position for c in separation(load(written(tmp_path, samples)))] == [1]


def test_known_lines_hold_everything_the_check_was_built_tuned_or_measured_on():
    known = set(known_lines())
    assert all(s.sentence in known for s in (*DEV_SET, *DISTRESS_SET))
    assert all(s.sentence in known for s in (*conduct_set.DEV_HALF, *conduct_set.HELD_OUT_HALF))
    assert "is there a back door out of this" in known, "the rules' own examples"


def test_the_fingerprint_changes_with_the_file(tmp_path):
    path = written(tmp_path, FRESH)
    before = fingerprint(path)
    written(tmp_path, FRESH[:2])
    assert len(before) == 12 and fingerprint(path) != before


def test_the_real_set_lives_where_git_and_docker_leave_it_alone():
    """Its words never reach the repository or an image: its folder ignores everything but
    its own .gitignore and README, and the image never copies a set."""
    ignored = (DEFAULT_PATH.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ignored[-3:] == ["*", "!.gitignore", "!README.md"]
    dockerignore = (Path(__file__).parents[1] / ".dockerignore").read_text(encoding="utf-8")
    assert "independent/*.json" in dockerignore.splitlines()
    assert DEFAULT_PATH.parent.name == "independent" and DEFAULT_PATH.suffix == ".json"
