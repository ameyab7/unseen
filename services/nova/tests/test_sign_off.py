"""Lines drafted for sign-off (nova/sign_off.py): every one passes the guardrails for its
moment, checked here rather than by eye, and each sign-off document lists them as written.
None of them is said on air until it moves to nova/script.py."""

from pathlib import Path

import pytest

from nova import sign_off
from nova.cues import Moment
from nova.fixtures import EVERYDAY_PAIR, NO_PRONOUNS_PAIR, REHEARSAL_PAIR
from nova.guardrails import check
from nova.lines import names_for, rules_for

PAIRS = (REHEARSAL_PAIR, EVERYDAY_PAIR, NO_PRONOUNS_PAIR)
# The repository's docs/sign-off, if it's here: the Docker image holds services/nova only.
SIGN_OFF = next(
    (
        folder / "docs" / "sign-off"
        for folder in Path(__file__).resolve().parents
        if (folder / "docs" / "sign-off").is_dir()
    ),
    None,
)


def passes(text: str, moment: Moment, names: frozenset[str] | set[str]) -> tuple:
    rules = rules_for(moment)
    return check(
        text,
        names=names,
        max_chars=rules.max_chars,
        max_sentences=rules.max_sentences,
        questions=rules.questions,
        open_with_names=rules.open_with_names,
    )


def drafted() -> list[tuple[Moment, str, frozenset[str]]]:
    lines: list[tuple[Moment, str, frozenset[str]]] = []
    for pair in PAIRS:
        names = names_for(Moment.SILENCE, pair, None)
        lines += [(Moment.SILENCE, line, names) for line in sign_off.both(sign_off.SILENCE, pair)]
        lines += [(Moment.OUTRO, line, names) for line in sign_off.both(sign_off.OUTRO, pair)]
        lines += [
            (Moment.SILENCE, line, frozenset({pair.right.first_name, "Nova"}))
            for line in sign_off.one(sign_off.ONE_DATER_SILENCE, pair.right.first_name)
        ]
        lines += [(Moment.ROOM_TALK, line, names) for line in sign_off.ROOM_TALK]
    lightning = (*sign_off.LIGHTNING, *sign_off.LIGHTNING_LAST)
    lines += [(Moment.LIGHTNING_QUESTION, line, frozenset({"Nova"})) for line in lightning]
    lines += [(Moment.REPLY, line, frozenset({"Nova"})) for line in sign_off.CONDUCT_DETAIL]
    lines += [(Moment.REPLY, line, frozenset({"Nova"})) for line in sign_off.REPLY_FALLBACK]
    return lines


@pytest.mark.parametrize(("moment", "line", "names"), drafted())
def test_every_drafted_line_passes_the_guardrails_for_its_moment(moment, line, names):
    assert passes(line, moment, names) == ()


def test_the_pools_are_the_sizes_asked_for():
    assert (len(sign_off.LIGHTNING), len(sign_off.LIGHTNING_LAST)) == (12, 3)
    assert (len(sign_off.SILENCE), len(sign_off.OUTRO)) == (10, 4)


def test_last_question_lines_say_so_and_the_others_dont():
    assert all(line.startswith("Last quick one") for line in sign_off.LIGHTNING_LAST)
    assert not any(line.startswith("Last") for line in sign_off.LIGHTNING)


@pytest.mark.parametrize(
    ("document", "lines"),
    [
        (
            "2026-10-nova-fallbacks.md",
            (*sign_off.LIGHTNING, *sign_off.LIGHTNING_LAST, *sign_off.SILENCE, *sign_off.OUTRO),
        ),
        ("2026-10-one-dater.md", sign_off.ONE_DATER_SILENCE),
        ("2026-10-room-talk.md", sign_off.ROOM_TALK),
        ("2026-10-conduct.md", sign_off.CONDUCT_DETAIL),
        ("2026-10-reply-fallback.md", sign_off.REPLY_FALLBACK),
    ],
)
def test_each_sign_off_document_lists_its_drafts_as_written(document, lines):
    if SIGN_OFF is None:
        pytest.skip("docs/sign-off isn't in this checkout (the Docker image)")
    text = (SIGN_OFF / document).read_text(encoding="utf-8")
    missing = [line for line in lines if line not in text]
    assert missing == []
