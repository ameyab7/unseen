"""No held-out sentence reaches a failing test's report (decision 0005, tests/conftest.py).

The sentences are taken from the sets here, never written out, and every check is a plain
boolean, so not even a failure of these tests can show one."""

import contextlib
from types import SimpleNamespace

from conftest import REDACTED, SHORTEST, pytest_runtest_makereport, redact

from nova.conduct_set import HELD_OUT_HALF
from nova.distress_dev import DEV_SET
from nova.distress_set import DISTRESS_SET

SENTENCE = next(s.sentence for s in DISTRESS_SET if len(s.sentence) > 3 * SHORTEST)
OTHER = next(line for s in DISTRESS_SET for line in (*s.other, *s.earlier))  # what came before
CONDUCT = HELD_OUT_HALF[0].sentence


def test_a_held_out_sentence_and_what_comes_with_it_are_replaced_whole():
    text = f"assert {SENTENCE!r} in report, after {OTHER!r} and {CONDUCT!r}"
    out = redact(text)
    leaked = any(line in out for line in (SENTENCE, OTHER, CONDUCT))
    assert not leaked and out.count(REDACTED) == 3


def test_pieces_pytest_truncated_are_replaced_too():
    head = SENTENCE[: SHORTEST + 5] + "..."
    tail = "..." + SENTENCE[-(SHORTEST + 5) :]
    out = redact(f"[Sample(sentence='{head}   {tail}')]")
    leaked = SENTENCE[: SHORTEST + 5] in out or SENTENCE[-(SHORTEST + 5) :] in out
    assert not leaked and out.count(REDACTED) == 2


def test_dev_sentences_are_shown_as_they_are():
    """Only held-out lines are hidden: a dev failure still says what failed."""
    dev = DEV_SET[0].sentence
    assert redact(f"missed: {dev}") == f"missed: {dev}"


def test_a_failing_report_and_its_captured_output_are_redacted():
    report = SimpleNamespace(
        failed=True,
        longrepr=f"AssertionError: {SENTENCE!r} != 'x'",
        sections=[("Captured stdout call", f"heard {SENTENCE}")],
    )
    hook = pytest_runtest_makereport(item=None, call=None)
    next(hook)
    with contextlib.suppress(StopIteration):
        hook.send(SimpleNamespace(get_result=lambda: report))
    shown = report.longrepr + " ".join(content for _, content in report.sections)
    leaked = SENTENCE in shown
    assert not leaked and shown.count(REDACTED) == 2
