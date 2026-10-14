"""One rehearsal date: the show runner, Nova's lines and a voice, on a fake clock, with the
delay to Nova's first word measured for every line. No LiveKit, no network."""

import logging

import pytest

from nova import script
from nova.cards import Seat
from nova.cues import Moment
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.lines import Source
from nova.rehearsal import (
    Command,
    Kind,
    Rehearsal,
    Spoken,
    estimate_cost,
    parse_command,
    render_report,
    targets,
)
from nova.timing import REHEARSAL

# REHEARSAL timing: first question at 13.5 s, free talk 27 s, from the room 72 s,
# lightning 99 s, last word 126 s, time 144 s, result 162 s, next up 171 s.


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeSpeaker:
    """Nova's voice: the first audio plays first_audio seconds after a line is handed over,
    and the line then plays for length seconds."""

    def __init__(self, clock: Clock, *, first_audio: float = 0.3, length: float = 0.0):
        self.clock, self.first_audio, self.length = clock, first_audio, length
        self.texts: list[str] = []
        self.stops = 0

    async def speak(self, text: str) -> float | None:
        self.texts.append(text)
        started = self.clock.now + self.first_audio
        self.clock.now = started + self.length
        return started

    def stop(self) -> None:
        self.stops += 1


class SilentSpeaker(FakeSpeaker):
    async def speak(self, text: str) -> float | None:
        self.texts.append(text)
        return None


class FakeModel:
    name = "fake/model"

    def __init__(self, reply: str = "Here we go, Maya and Theo.") -> None:
        self.reply = reply

    async def write(self, *, system: str, prompt: str) -> str:
        return self.reply


def rehearsal(clock: Clock, speaker=None, *, reply: str = "Here we go, Maya and Theo."):
    return Rehearsal(
        REHEARSAL_PAIR,
        model=FakeModel(reply),
        speaker=speaker or FakeSpeaker(clock),
        clock=clock,
        timing=REHEARSAL,
        next_pair=NEXT_PAIR,
        first_up=Seat.LEFT,
        voice="fake/voice",
        stt="deepgram/nova-3",
    )


async def started(clock: Clock, **options) -> Rehearsal:
    r = rehearsal(clock, **options)
    await r.prepare()
    r.start()
    await r.speak_queued()
    return r


def lines(r: Rehearsal, moment: Moment) -> list[Spoken]:
    return [s for s in r.spoken if s.moment is moment]


# --- Chat commands: one browser plays both daters ------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("maya", (Command.MAYA, "")),
        ("Theo", (Command.THEO, "")),
        (" moment ", (Command.MOMENT, "")),
        ("ask What's your favourite film?", (Command.ASK, "What's your favourite film?")),
        ("reveal", (Command.REVEAL, "")),
        ("end", (Command.END, "")),
    ],
)
def test_chat_commands(text, expected):
    assert parse_command(text) == expected


@pytest.mark.parametrize("text", ["ask", "ask   ", "hello there", "", "mayathan"])
def test_anything_else_in_chat_is_not_a_command(text):
    assert parse_command(text) is None


async def test_the_seat_that_speaks_is_chosen_in_chat():
    clock = Clock()
    r = await started(clock)
    clock.now = 40
    r.transcript("Nova, are you a real person?")
    assert r.command("theo") is Command.THEO
    r.transcript("Nova, tea or coffee?")
    replies = [cue for cue in r.cues if getattr(cue, "moment", None) is Moment.REPLY]
    assert [cue.about for cue in replies] == [Seat.LEFT, Seat.RIGHT]


async def test_only_words_that_address_nova_get_a_reply():
    clock = Clock()
    r = await started(clock)
    clock.now = 40
    r.transcript("I think tea, honestly.")
    assert not any(getattr(cue, "moment", None) is Moment.REPLY for cue in r.cues)


# --- Measuring the delay to Nova's first word ---------------------------------------------


async def test_a_prepared_line_is_measured_from_its_cue():
    clock = Clock()
    r = await started(clock)
    doors = lines(r, Moment.DOORS_INTRO)[0]
    assert (doors.kind, doors.source) == (Kind.PREPARED, Source.PREPARED)
    assert doors.from_cue_ms == 300


async def test_a_reply_is_measured_from_the_end_of_the_daters_sentence():
    clock = Clock()
    r = await started(clock)
    clock.now = 40.0
    r.tick()
    await r.speak_queued()  # the first questions and a silence prompt
    clock.now = 50.0
    r.speech_started()
    clock.now = 51.0
    r.speech_ended()
    clock.now = 51.8  # the transcript arrives
    r.transcript("Nova, are you a real person?")
    await r.speak_queued()
    reply = lines(r, Moment.REPLY)[0]
    assert reply.kind is Kind.REPLY
    assert reply.from_sentence_end_ms == 1100
    assert reply.from_cue_ms == 300


async def test_a_line_queued_behind_another_is_also_measured_from_when_nova_was_free():
    clock = Clock()
    r = await started(clock, speaker=FakeSpeaker(clock, length=3.0))
    for t in range(1, 150):
        clock.now = max(clock.now, float(t))
        r.tick()
        await r.speak_queued()
    talk = [s for s in lines(r, Moment.ROOM_TALK) if s.kind is Kind.LIVE][-1]
    assert talk.from_free_ms == 300, "Nova was busy with the time line until then"
    assert talk.from_cue_ms > 3000


async def test_a_dater_who_wants_to_stop_gets_the_kind_line_measured_as_a_reply():
    clock = Clock()
    r = await started(clock)
    clock.now = 40.0
    r.speech_ended()
    r.transcript("Nova, I want to stop.")
    await r.speak_queued()
    kind = lines(r, Moment.NEED_A_MOMENT)[0]
    assert kind.kind is Kind.REPLY and r.speaker.texts[-2] == script.DISTRESS_PAUSE
    assert lines(r, Moment.ROOM_TALK)[0].kind is Kind.LIVE
    assert r.alerts == 1


async def test_a_line_that_never_plays_fails_its_target():
    clock = Clock()
    r = await started(clock, speaker=SilentSpeaker(clock))
    doors = lines(r, Moment.DOORS_INTRO)[0]
    assert doors.from_cue_ms is None
    assert targets(r.spoken)[0].passed is False


# --- Chat drives the show ------------------------------------------------------------------


async def test_chat_commands_drive_the_show():
    clock = Clock()
    r = await started(clock)
    clock.now = 30
    r.command("moment")
    await r.speak_queued()
    assert script.NEED_A_MOMENT in r.speaker.texts
    clock.now = 35
    r.command("back")
    r.command("ask What's the best film you've seen this year?")
    r.command("unlock")
    clock.now = 50
    r.command("end")
    await r.speak_queued()
    assert r.speaker.texts[-1] == script.MODERATOR_END
    assert r.speaker.stops == 1, "Nova stops mid-line first"
    assert r.finished


async def test_decisions_come_from_chat_and_only_count_while_the_window_is_open():
    clock = Clock()
    r = await started(clock)
    r.command("reveal")  # too early: ignored
    for t in range(1, 200):
        clock.now = float(t)
        if t == 150:
            r.command("reveal")  # Maya
            r.command("theo")
            r.command("reveal")
        r.tick()
        await r.speak_queued()
    assert script.RESULT_MUTUAL in r.speaker.texts
    assert r.finished


# --- Targets and the report ---------------------------------------------------------------


def spoken(kind: Kind, from_cue: int | None, *, from_end: int | None = None) -> Spoken:
    return Spoken(
        Moment.SILENCE,
        kind,
        Source.MODEL,
        from_cue_ms=from_cue,
        from_free_ms=from_cue,
        from_sentence_end_ms=from_end,
        model_ms=0,
    )


def test_each_target_passes_only_if_every_line_meets_it():
    results = targets(
        [
            spoken(Kind.PREPARED, 400),
            spoken(Kind.PREPARED, 1200),
            spoken(Kind.LIVE, 900),
            spoken(Kind.LIVE, 1500),
            spoken(Kind.REPLY, 500, from_end=2400),
        ]
    )
    assert [(t.limit_ms, t.count, t.median_ms, t.worst_ms, t.passed) for t in results] == [
        (1000, 2, 800, 1200, False),
        (2000, 2, 1200, 1500, True),
        (2500, 1, 2400, 2400, True),
    ]


def test_a_target_with_no_lines_has_no_verdict():
    assert targets([spoken(Kind.PREPARED, 400)])[1].passed is None


def test_credit_is_estimated_from_minutes_heard_and_characters_spoken():
    # Deepgram Nova-3 at $0.0048 a minute, Cartesia Sonic 3 at $50 per million characters.
    cost = estimate_cost("deepgram/nova-3", 240.0, "cartesia/sonic-3:abc", 2000)
    assert cost == pytest.approx(4 * 0.0048 + 2000 * 0.00005)


async def test_the_report_holds_codes_and_numbers_never_what_anyone_said_or_typed(caplog):
    caplog.set_level(logging.INFO, logger="nova")
    clock = Clock()
    r = await started(clock)
    r.command("ask OKAPI-QUESTION?")
    clock.now = 40
    r.speech_ended()
    r.transcript("Nova, PANGOLIN-SPEECH at 555 013 4567")
    for t in range(41, 200):
        clock.now = float(t)
        r.tick()
        await r.speak_queued()
    report = render_report(r)
    for secret in ("OKAPI", "PANGOLIN", "555", "Maya", "Theo", "Here we go"):
        assert secret not in report, secret
        assert secret not in caplog.text, secret
    assert "prepared lines" in report and "Fallback rate" in report and "credit" in report
