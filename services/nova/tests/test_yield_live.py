"""The yield rehearsal's LiveKit side (nova/yield_live.py), the parts that run without a
room. It imports LiveKit, so like tests/test_agent.py it runs in Docker only (CLAUDE.md); the
rehearsal itself is run on demand, never in the tests."""

from array import array

from nova import yield_live
from nova.yield_live import Ear, Timed, _trim
from nova.yield_matrix import AGENT_NAME, ECHO_DELAY, FRAME_SECONDS


class Voice:
    played, played_text, timed = 1.5, "Here is a", True

    def __init__(self) -> None:
        self.stops = 0

    async def speak(self, text: str) -> float | None:
        return 10.0

    def stop(self) -> None:
        self.stops += 1


async def test_the_voice_is_timed_in_numbers_and_says_what_played_as_before():
    timed = Timed(Voice())
    assert await timed.speak("Here is a line.") == 10.0
    called, started, finished, length = timed.calls[0]
    assert started == 10.0 and called <= finished and length == len("Here is a line.")
    timed.stop()
    assert len(timed.stops) == 1 and timed.voice.stops == 1
    assert (timed.played, timed.played_text) == (1.5, "Here is a")


def test_a_clip_starts_at_its_first_sound():
    assert list(_trim(array("h", [0, 3, 0, 9000, -9000, 0, 2]))) == [9000, -9000]
    assert list(_trim(array("h", [0, 0]))) == [0, 0]


def test_nova_s_echo_comes_back_echo_delay_late():
    ear = Ear()
    for k in range(round(ECHO_DELAY / FRAME_SECONDS) + 1):
        ear._echo.append(array("h", [k]))
    assert ear.echo() == array("h", [0])
    assert ear.echo() is None, "no more than the delay's worth held"


def test_nova_s_side_joins_only_rooms_it_is_dispatched_to_by_name():
    """An agent with no name joins every new room in the project, a Playground's included;
    with one, only a room whose token dispatches it by that name."""
    assert yield_live.server._agent_name == AGENT_NAME == "nova-yield-rehearsal"
