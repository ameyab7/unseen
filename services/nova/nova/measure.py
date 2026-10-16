"""Elapsed time in milliseconds, the one unit every log event and report uses."""

import time


def ms(seconds: float) -> int:
    return round(seconds * 1000)


def ms_since(started: float, *, now: float | None = None) -> int:
    """Milliseconds from started to now (the monotonic clock unless now is given)."""
    return ms((time.monotonic() if now is None else now) - started)
