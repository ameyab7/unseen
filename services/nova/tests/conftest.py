"""Shared test setup: no held-out or independent sentence ever reaches a test report
(decision 0005).

Some tests run the held-out sets through fake checks to test the evaluation itself, and
pytest's assertion detail prints whatever an assertion compared: a failure once printed six
held-out sentences, and on 2026-10-23 a set of failures printed more. A sentence seen in a
failure can steer the next change, and then the set no longer measures anything. So every
failing report, and any captured output, has each protected line replaced, whole or cut
short by pytest's truncation, before anything is shown. Protected: each held-out sentence
and every line that comes with one (what was said before it, what it answers, its finals);
the independent set's, if one is on this machine, read here and never shown; and those of
any independent set a test loads through the evaluation (nova.independent_set.load).
"""

import contextlib
from collections.abc import Generator, Iterable
from typing import Any

import pytest

from nova import independent_set
from nova.conduct_set import HELD_OUT_HALF
from nova.distress_set import DISTRESS_SET, Sample

REDACTED = "[held-out]"
# pytest cuts a long value to its head and tail around "...": a piece this long or longer
# can still give a sentence away.
SHORTEST = 12


def _forms(lines: Iterable[str]) -> set[str]:
    """Each line as written and as repr() writes it."""
    return {form for line in lines if line.strip() for form in (line, repr(line)[1:-1])}


def _lines(samples: Iterable[Sample]) -> set[str]:
    return {line for s in samples for line in (s.sentence, *s.finals, *s.earlier, *s.other)}


def _held_out() -> set[str]:
    lines = _lines(DISTRESS_SET)
    for conduct in HELD_OUT_HALF:
        lines.update((conduct.sentence, *conduct.other, *conduct.earlier))
    if independent_set.DEFAULT_PATH.exists():  # the real set, never shown
        with contextlib.suppress(independent_set.IndependentSetError):
            lines |= _lines(independent_set.load(independent_set.DEFAULT_PATH))
    return _forms(lines)


PROTECTED = _held_out()
# Lines of independent sets loaded while the tests run.
LOADED: set[str] = set()


def redact(text: str) -> str:
    """The text with every protected line, and every truncated piece of one, replaced:
    longest first, so a long line is never left half-replaced by a shorter one inside it."""
    lines = sorted(PROTECTED | _forms(LOADED), key=len, reverse=True)
    for line in lines:
        text = text.replace(line, REDACTED)
    if "..." in text:
        for line in lines:
            for cut in range(len(line) - 1, SHORTEST - 1, -1):
                text = text.replace(line[:cut] + "...", REDACTED + "...")
                text = text.replace("..." + line[-cut:], "..." + REDACTED)
    return text


@pytest.fixture(autouse=True)
def _protect_independent_sets(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Any independent set the evaluation loads in a test joins the protected lines for that
    test's report (made before teardown), and only that test's: another test may load a dev
    sentence as a sample on purpose, and dev failures must still say what failed."""
    real = independent_set.load

    def load(path: Any) -> tuple[Sample, ...]:
        samples = real(path)
        LOADED.update(_lines(samples))
        return samples

    monkeypatch.setattr(independent_set, "load", load)
    yield
    LOADED.clear()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Generator[None, Any]:
    outcome = yield
    report = outcome.get_result()
    if report.failed and report.longrepr is not None:
        report.longrepr = redact(str(report.longrepr))
    report.sections = [(title, redact(content)) for title, content in report.sections]
