"""Nova's log: events and timings only.

CLAUDE.md: never log transcripts, chat text, photos or ID data. This log enforces that by
type: a field can be a number, a flag, None, a code (an enum) or a tuple of codes. Free text
of any kind, including a line Nova said, raises before anything is written.
"""

import logging
from enum import Enum, StrEnum


class Event(StrEnum):
    LINE = "line"  # one line chosen: where it came from, how long it took, what it broke
    PREPARED = "prepared"  # set pieces written before the date, and whether the budget ran out
    WARMUP = "warmup"  # one tiny request at the start, so a cold model loads before prep
    SPOKEN = "spoken"  # one line played: the delay to Nova's first word (rehearsals)
    VOICE_ERROR = "voice_error"  # Nova's voice failed on a line, which never played
    DISTRESS_CHECK = "distress_check"  # the meaning layer's verdict and time, never the words
    ALERT = "alert"  # an alert for the moderator console
    QUESTION_SKIPPED = "question_skipped"  # an approved question the screen caught: its code
    COMMAND = "command"  # a rehearsal chat command (the command only, never its text)
    SPEECH = "speech"  # the voice detector heard a dater start or stop: the seat only
    YIELDED = "yielded"  # Nova stopped a line for a dater who began at the same moment


def _allowed(value: object) -> bool:
    if value is None or isinstance(value, Enum):
        return True
    if isinstance(value, bool | int | float):
        return True
    if isinstance(value, tuple):
        return all(isinstance(item, Enum) for item in value)
    return False


def _format(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, tuple):
        return ",".join(str(item.value) for item in value) or "-"
    return str(value)


class EventLog:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("nova")

    def emit(self, event: Event, **fields: object) -> None:
        if not isinstance(event, Event):
            raise ValueError(f"Unknown event {event!r}")
        for key, value in fields.items():
            if not _allowed(value):
                raise TypeError(
                    f"Log field {key!r} is a {type(value).__name__}: only codes, numbers and "
                    "flags may be logged, never text"
                )
        parts = " ".join(f"{key}={_format(value)}" for key, value in fields.items())
        self._logger.info("%s %s", event.value, parts)
