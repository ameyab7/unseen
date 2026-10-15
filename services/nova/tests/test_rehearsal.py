"""One rehearsal date: the show runner, Nova's lines and a voice, on a fake clock, with the
delay to Nova's first word measured for every line. No LiveKit, no network."""

import asyncio
import logging

import pytest

from nova import script
from nova.cards import Seat
from nova.cues import AlertModerator, ModeratorAlert, Moment
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


class BrokenVoice(FakeSpeaker):
    """Fails on the second line, then works again."""

    async def speak(self, text: str) -> float | None:
        started = await super().speak(text)
        if len(self.texts) == 2:
            raise RuntimeError("the voice failed")
        return started


class StuckVoice(FakeSpeaker):
    """Never finishes a line."""

    async def speak(self, text: str) -> float | None:
        self.texts.append(text)
        await asyncio.Event().wait()
        return None


class TickingClock(Clock):
    """Moves on a quarter of a second every time it's read, so run() gets through a date."""

    def __call__(self) -> float:
        self.now += 0.25
        return self.now


class FakeModel:
    """Writes every line the same way. For the distress check, answers per sentence from
    labels ("no" by default, "error" to fail), taking check_takes on the clock."""

    name = "fake/model"

    def __init__(
        self,
        reply: str = "Here we go, Maya and Theo.",
        *,
        labels: dict[str, str] | None = None,
        clock: Clock | None = None,
        check_takes: float = 0.0,
        gate: asyncio.Event | None = None,
        write_error: Exception | None = None,
    ) -> None:
        self.reply = reply
        self.write_error = write_error
        self.labels = labels or {}
        self.clock, self.check_takes = clock, check_takes
        self.gate = gate  # when set, the check answers only once it's set
        self.checked: list[str] = []  # the sentences the distress check saw

    async def write(self, *, system: str, prompt: str) -> str:
        if self.write_error:
            raise self.write_error
        return self.reply

    async def label(self, *, system: str, prompt: str) -> str:
        sentence = prompt.split('"sentence": "')[1].split('"')[0]
        self.checked.append(sentence)
        if self.gate is not None:
            await self.gate.wait()
        if self.clock is not None:
            self.clock.now += self.check_takes
        answer = self.labels.get(sentence, "no")
        if answer == "error":
            raise RuntimeError("the model is down")
        return answer


def rehearsal(clock: Clock, speaker=None, *, model=None, reply: str = "Here we go, Maya and Theo."):
    return Rehearsal(
        REHEARSAL_PAIR,
        model=model or FakeModel(reply),
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


def alerts(r: Rehearsal) -> list[ModeratorAlert]:
    return [cue.alert for cue in r.cues if isinstance(cue, AlertModerator)]


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


async def transcripts(r: Rehearsal, clock: Clock, *said: tuple[float, str]) -> None:
    """Final transcripts from the speaking dater, each at its time. After each, any distress
    check finishes and Nova speaks."""
    for at, text in said:
        clock.now = at
        r.speech_ended()
        r.transcript(text)
        await r.heard()
        await r.speak_queued()


async def test_a_dater_who_wants_to_stop_is_heard_without_saying_nova():
    clock = Clock()
    r = await started(clock)
    await transcripts(r, clock, (40.0, "I want to stop. I'm not comfortable."))
    assert r.alerts == 1
    kind = lines(r, Moment.NEED_A_MOMENT)[0]
    assert kind.kind is Kind.REPLY, "Nova's first line after the dater spoke"
    assert script.DISTRESS_PAUSE in r.speaker.texts


async def test_the_kind_line_comes_at_once_cutting_nova_off_and_dropping_queued_lines():
    clock = Clock()
    r = await started(clock, speaker=FakeSpeaker(clock, length=12.0))
    clock.now = 40.0
    r.tick()  # the first questions, twelve seconds each, queued and not yet spoken
    r.speech_ended()
    r.transcript("I want to stop.")
    await r.speak_queued()
    assert lines(r, Moment.NEED_A_MOMENT)[0].from_sentence_end_ms == 300
    assert lines(r, Moment.FIRST_QUESTION) == [], "dropped, never said after the kind line"
    assert r.speaker.stops == 1


@pytest.mark.parametrize(
    "split",
    [("Nova,", "I want to stop."), ("I'm not", "comfortable."), ("Help, I can't", "do this.")],
)
async def test_a_sentence_split_across_transcripts_is_heard_whole(split):
    clock = Clock()
    r = await started(clock)
    await transcripts(r, clock, (40.0, split[0]), (41.5, split[1]))
    assert script.DISTRESS_PAUSE in r.speaker.texts
    assert ModeratorAlert.DATER_DISTRESSED in alerts(r)


async def test_only_the_same_daters_last_few_seconds_are_joined():
    clock = Clock()
    r = await started(clock)
    await transcripts(r, clock, (40.0, "I'm not"), (50.0, "comfortable."))
    r.command("theo")
    await transcripts(r, clock, (60.0, "I'm not"))
    r.command("maya")
    await transcripts(r, clock, (61.0, "comfortable."))
    assert alerts(r) == []
    assert script.DISTRESS_PAUSE not in r.speaker.texts


async def test_every_transcript_is_checked_once_and_what_follows_doesnt_repeat_an_alert():
    clock = Clock()
    model = FakeModel(labels={"Help.": "maybe"})
    r = await started(clock, model=model)
    await transcripts(r, clock, (40.0, "Help."), (41.0, "With the dishes, I mean."))
    assert alerts(r) == [ModeratorAlert.POSSIBLE_DISTRESS]
    await transcripts(r, clock, (42.0, "Actually, I want to stop."))
    assert alerts(r) == [ModeratorAlert.POSSIBLE_DISTRESS, ModeratorAlert.DATER_DISTRESSED]
    await transcripts(r, clock, (43.0, "Sorry."))
    assert len(alerts(r)) == 2
    assert model.checked == ["Help.", "With the dishes, I mean.", "Sorry."], (
        "everything but the unmistakable request, which needed no check"
    )


# --- The meaning layer: anything else with a warning word ----------------------------------


async def test_a_yes_pauses_the_dater_and_the_kind_line_counts_the_checks_time():
    clock = Clock()
    words = "I want to stop talking to him."
    model = FakeModel(labels={words: "yes"}, clock=clock, check_takes=0.4)
    r = await started(clock, model=model)
    await transcripts(r, clock, (40.0, words))
    assert alerts(r) == [ModeratorAlert.DATER_DISTRESSED]
    kind = lines(r, Moment.NEED_A_MOMENT)[0]
    assert kind.kind is Kind.REPLY and kind.from_sentence_end_ms == 700
    assert "Distress checks: 1 (yes 1)" in render_report(r)


async def test_a_reply_to_nova_waits_for_the_check_and_is_never_said_if_the_dater_wants_to_stop():
    clock = Clock()
    words = "Nova, I want to stop talking to him."
    r = await started(clock, model=FakeModel(labels={words: "yes"}))
    await transcripts(r, clock, (40.0, words))
    assert lines(r, Moment.REPLY) == [], "the reply written alongside the check is dropped"
    assert r.speaker.texts[-2] == script.DISTRESS_PAUSE


async def test_nova_free_to_speak_still_waits_for_the_verdict_before_a_reply():
    """Nova picks the reply up as soon as it's free, while the check is still running; it
    must not say it until the verdict, and never on a yes."""
    clock = Clock()
    words = "Nova, I want to stop talking to him."
    verdict_due = asyncio.Event()
    r = await started(clock, model=FakeModel(labels={words: "yes"}, gate=verdict_due))
    clock.now = 40.0
    r.speech_ended()
    r.transcript(words)
    speaking = asyncio.ensure_future(r.speak_queued())
    await asyncio.sleep(0.05)
    assert lines(r, Moment.REPLY) == [], "not a word before the verdict"
    verdict_due.set()
    await r.heard()
    await speaking
    await r.speak_queued()
    assert lines(r, Moment.REPLY) == []
    assert script.DISTRESS_PAUSE in r.speaker.texts


async def test_a_reply_to_nova_goes_ahead_after_a_no_or_a_maybe():
    clock = Clock()
    topic, lost = "Nova, can you help us pick a topic?", "Nova, help, I'm a bit lost."
    r = await started(clock, model=FakeModel(labels={topic: "no", lost: "maybe"}))
    await transcripts(r, clock, (40.0, topic), (45.0, lost))
    assert len(lines(r, Moment.REPLY)) == 2
    assert alerts(r) == [ModeratorAlert.POSSIBLE_DISTRESS]


async def test_if_the_check_fails_the_moderator_listens_in_and_nova_still_answers():
    clock = Clock()
    words = "Nova, help me out here."
    r = await started(clock, model=FakeModel(labels={words: "error"}))
    await transcripts(r, clock, (40.0, words))
    assert alerts(r) == [ModeratorAlert.DISTRESS_UNCHECKED]
    assert len(lines(r, Moment.REPLY)) == 1


async def test_ordinary_talk_is_checked_too_and_a_no_changes_nothing():
    clock = Clock()
    model = FakeModel()
    r = await started(clock, model=model)
    await transcripts(r, clock, (40.0, "I think tea, honestly."), (41.0, "Nova, tea or coffee?"))
    assert model.checked == ["I think tea, honestly.", "Nova, tea or coffee?"]
    assert alerts(r) == [] and len(lines(r, Moment.REPLY)) == 1


@pytest.mark.parametrize(
    ("label", "fallback"),
    [("maybe", script.TAKE_YOUR_TIME), ("error", script.TAKE_YOUR_TIME), ("no", None)],
)
async def test_after_a_maybe_or_a_failed_check_a_failed_reply_is_never_nova_staying_out_of_it(
    label, fallback
):
    clock = Clock()
    words = "Nova, he keeps asking where I live."
    model = FakeModel(labels={words: label}, write_error=RuntimeError("down"))
    r = await started(clock, model=model)
    await transcripts(r, clock, (40.0, words))
    [reply] = lines(r, Moment.REPLY)
    said = r.speaker.texts[-1]
    assert reply.source is Source.FALLBACK
    assert said == (fallback or script.REPLY_FALLBACK)


async def test_a_line_that_never_plays_fails_its_target():
    clock = Clock()
    r = await started(clock, speaker=SilentSpeaker(clock))
    doors = lines(r, Moment.DOORS_INTRO)[0]
    assert doors.from_cue_ms is None
    assert targets(r.spoken)[0].passed is False


async def test_a_voice_error_costs_one_line_never_the_rest_of_the_date():
    clock = TickingClock()
    r = rehearsal(clock, BrokenVoice(clock))
    await r.prepare()
    await asyncio.wait_for(r.run(every=0), 5)
    assert r.finished
    assert r.spoken[1].from_cue_ms is None, "the line the voice failed on never played"
    assert len(r.spoken) == len(r.speaker.texts) > 10, "every other line was spoken"
    assert script.RESULT_NOT_MUTUAL in r.speaker.texts, "including the result, at the end"
    assert targets(r.spoken)[0].passed is False


async def test_the_date_ends_and_is_reported_even_if_nova_is_stuck_mid_line():
    clock = TickingClock()
    r = rehearsal(clock, StuckVoice(clock))
    await r.prepare()
    await asyncio.wait_for(r.run(every=0, final_wait=0.05), 5)
    assert r.finished
    assert "Targets" in render_report(r)


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


async def test_when_the_moderator_ends_the_date_nothing_already_queued_is_said():
    clock = Clock()
    r = await started(clock)
    clock.now = 40.0
    r.tick()  # the first questions, queued and not yet spoken
    r.command("end")
    await r.speak_queued()
    assert lines(r, Moment.FIRST_QUESTION) == []
    assert r.speaker.texts[-1] == script.MODERATOR_END and r.speaker.stops == 1


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
