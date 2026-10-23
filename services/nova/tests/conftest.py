"""Shared test setup: no held-out sentence ever reaches a test report (decision 0005).

Some tests run the held-out sets through fake checks to test the evaluation itself, and
pytest's assertion detail prints whatever an assertion compared: a failure once printed six
held-out sentences, and on 2026-10-23 a set of failures printed more. A sentence seen in a
failure can steer the next change, and then the set no longer measures anything. So every
failing report, and any captured output, has each held-out sentence and every line that
comes with one (what was said before it, what it answers, its finals) replaced, whole or
cut short by pytest's truncation, before anything is shown.
"""

from collections.abc import Generator
from typing import Any

import pytest

from nova.conduct_set import HELD_OUT_HALF
from nova.distress_set import DISTRESS_SET

REDACTED = "[held-out]"
# pytest cuts a long value to its head and tail around "...": a piece this long or longer
# can still give a sentence away.
SHORTEST = 12


def _held_out() -> list[str]:
    """Every held-out line, as written and as repr() writes it, longest first so a long
    line is never left half-replaced by a shorter one inside it."""
    lines: set[str] = set()
    for sample in DISTRESS_SET:
        lines.update((sample.sentence, *sample.finals, *sample.earlier, *sample.other))
    for conduct in HELD_OUT_HALF:
        lines.update((conduct.sentence, *conduct.other, *conduct.earlier))
    forms = {form for line in lines if line.strip() for form in (line, repr(line)[1:-1])}
    return sorted(forms, key=len, reverse=True)


HELD_OUT = _held_out()


def redact(text: str) -> str:
    """The text with every held-out line, and every truncated piece of one, replaced."""
    for line in HELD_OUT:
        text = text.replace(line, REDACTED)
    if "..." in text:
        for line in HELD_OUT:
            for cut in range(len(line) - 1, SHORTEST - 1, -1):
                text = text.replace(line[:cut] + "...", REDACTED + "...")
                text = text.replace("..." + line[-cut:], "..." + REDACTED)
    return text


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Generator[None, Any]:
    outcome = yield
    report = outcome.get_result()
    if report.failed and report.longrepr is not None:
        report.longrepr = redact(str(report.longrepr))
    report.sections = [(title, redact(content)) for title, content in report.sections]
