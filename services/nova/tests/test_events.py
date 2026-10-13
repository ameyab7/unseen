"""Nova's log records events and timings only. CLAUDE.md: never transcripts, chat text,
photos or ID data. The event log enforces it: free text can't be passed in at all."""

import logging

import pytest

from nova.cues import Moment
from nova.events import Event, EventLog
from nova.guardrails import Violation


def test_logs_an_event_with_codes_numbers_and_flags(caplog):
    caplog.set_level(logging.INFO, logger="nova")
    EventLog().emit(
        Event.LINE,
        moment=Moment.SILENCE,
        ms=118,
        timed_out=False,
        violations=(Violation.EXCLAMATION, Violation.TOO_LONG),
        error=None,
    )
    assert caplog.messages == [
        "line moment=silence ms=118 timed_out=False violations=exclamation,too_long error=-"
    ]


@pytest.mark.parametrize(
    "value",
    ["Maya works at Northwind Bank", b"bytes", ["a list"], {"a": "dict"}, ("text in a tuple",)],
)
def test_free_text_is_refused(value):
    with pytest.raises(TypeError, match="only codes, numbers and flags"):
        EventLog().emit(Event.LINE, moment=Moment.SILENCE, detail=value)


def test_unknown_events_are_refused():
    with pytest.raises(ValueError, match="Unknown event"):
        EventLog().emit("anything")  # type: ignore[arg-type]
