"""One rehearsal date: the show runner, Nova's lines and a voice, on a fake clock, with the
delay to Nova's first word measured for every line. No LiveKit, no network."""

import asyncio
import logging
import time

import pytest

from nova import script
from nova.cards import Seat
from nova.config import Settings
from nova.cues import AlertModerator, ModeratorAlert, Moment, Say
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.lines import LineWriter, Source
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

# Nova acts on what a dater said once their utterance closes: the grace after they stopped
# and the last transcript arrived (nova/on_air.py).
GRACE = Settings.utterance_grace


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
        self.prompts: list[str] = []  # what the line writer asked for

    async def write(self, *, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.write_error:
            raise self.write_error
        return self.reply

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        if '"sentence": "' not in prompt:
            return "ok"  # the warm-up's tiny request
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


def close(r: Rehearsal, clock: Clock) -> None:
    """The dater stays quiet for the grace, so Nova acts on what they said."""
    clock.now += GRACE
    r.tick()


async def past_the_first_questions(r: Rehearsal, clock: Clock, at: float = 40.0) -> None:
    """Both first questions and the silence prompt after them: the second dater's question
    comes once the first had their answer window (the rehearsal is paced)."""
    clock.now = at
    r.tick()
    await r.speak_queued()
    clock.now = at + REHEARSAL.answer_window + 1.0
    r.tick()
    await r.speak_queued()


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
    close(r, clock)
    replies = [cue for cue in r.cues if getattr(cue, "moment", None) is Moment.REPLY]
    assert [cue.about for cue in replies] == [Seat.LEFT, Seat.RIGHT]


async def test_only_words_that_address_nova_get_a_reply():
    clock = Clock()
    r = await started(clock)
    clock.now = 40
    r.transcript("I think tea, honestly.")
    close(r, clock)
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
    await past_the_first_questions(r, clock)
    clock.now = 50.0
    r.speech_started()
    clock.now = 51.0
    r.speech_ended()
    clock.now = 51.8  # the transcript arrives
    r.transcript("Nova, are you a real person?")
    clock.now = 52.3
    r.tick()
    assert not any(getattr(cue, "moment", None) is Moment.REPLY for cue in r.cues), "grace"
    clock.now = 52.4  # the grace after the transcript: Nova acts
    r.tick()
    await r.speak_queued()
    reply = lines(r, Moment.REPLY)[0]
    assert reply.kind is Kind.REPLY
    assert reply.from_sentence_end_ms == 1700, "the transcript's wait and the grace count"
    assert reply.grace_ms == 1400
    assert reply.from_cue_ms == 300


async def test_a_question_split_across_transcripts_gets_one_reply_to_the_whole_of_it():
    clock = Clock()
    r = await started(clock)
    await past_the_first_questions(r, clock)
    clock.now = 50.0
    r.speech_started()
    clock.now = 50.5
    r.transcript("Nova,")  # speech-to-text finalizes at the pause after "Nova,"
    clock.now = 52.0
    r.speech_ended()
    clock.now = 52.3
    r.transcript("what's your favourite film?")
    clock.now = 52.9
    r.tick()
    await r.heard()
    await r.speak_queued()
    replies = [cue for cue in r.cues if getattr(cue, "moment", None) is Moment.REPLY]
    assert len(replies) == 1
    assert any("Nova, what's your favourite film?" in prompt for prompt in r._model.prompts)
    reply = lines(r, Moment.REPLY)[0]
    assert (reply.grace_ms, reply.from_sentence_end_ms) == (900, 1200)


async def test_speech_ends_for_the_dater_who_started_speaking():
    """One browser plays both daters: if the chat picks the other seat mid-sentence, the
    voice detector's end of speech still belongs to the one who was speaking."""
    clock = Clock()
    r = await started(clock)
    clock.now = 40.0
    r.speech_started()
    r.transcript("Nova, tea or coffee?")
    r.command("theo")
    r.speech_ended()
    close(r, clock)
    replies = [cue for cue in r.cues if getattr(cue, "moment", None) is Moment.REPLY]
    assert [cue.about for cue in replies] == [Seat.LEFT]


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
    close(r, clock)
    await r.speak_queued()
    kind = lines(r, Moment.NEED_A_MOMENT)[0]
    assert kind.kind is Kind.REPLY and r.speaker.texts[-2] == script.DISTRESS_PAUSE
    assert lines(r, Moment.ROOM_TALK)[0].kind is Kind.LIVE
    assert r.alerts == 1


async def transcripts(r: Rehearsal, clock: Clock, *said: tuple[float, str]) -> None:
    """Final transcripts from the speaking dater, each at its time and each a whole
    utterance: the dater has stopped when it arrives and stays quiet for the grace. After
    each, any distress check finishes and Nova speaks."""
    for at, text in said:
        clock.now = at
        r.speech_ended()
        r.transcript(text)
        close(r, clock)
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
    close(r, clock)
    await r.speak_queued()
    kind = lines(r, Moment.NEED_A_MOMENT)[0]
    assert (kind.from_sentence_end_ms, kind.grace_ms) == (900, 600)
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
    assert kind.kind is Kind.REPLY and kind.from_sentence_end_ms == 1300, "grace and check"
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
    close(r, clock)
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
    assert r.speaker.texts[-1] == script.TAKE_YOUR_TIME, "after a maybe, the kind line"


@pytest.mark.parametrize(
    ("label", "said"),
    [
        ("maybe", script.TAKE_YOUR_TIME),
        ("error", script.TAKE_YOUR_TIME),
        ("no", "Here we go, Maya and Theo."),
    ],
)
async def test_after_a_maybe_or_a_failed_check_nova_never_quips_whatever_the_model_wrote(
    label, said
):
    """A model reply that worked used to play after a maybe; now only no lets it through."""
    clock = Clock()
    words = "Nova, he keeps asking where I live."
    r = await started(clock, model=FakeModel(labels={words: label}))
    await transcripts(r, clock, (40.0, words))
    [reply] = lines(r, Moment.REPLY)
    assert r.speaker.texts[-1] == said
    assert (reply.source is Source.FALLBACK) is (label != "no")


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


async def test_set_pieces_the_date_never_spoke_leave_tonights_memory_when_it_ends(monkeypatch):
    finished: list[LineWriter] = []
    monkeypatch.setattr(LineWriter, "finish", lambda writer: finished.append(writer))
    clock = TickingClock()
    r = rehearsal(clock, StuckVoice(clock))
    await r.prepare()
    await asyncio.wait_for(r.run(every=0, final_wait=0.05), 5)
    assert len(finished) == 1


class HangingModel(FakeModel):
    async def write(self, *, system: str, prompt: str) -> str:
        await asyncio.Event().wait()
        return ""


async def test_the_report_says_when_the_prep_budget_ran_out():
    clock = Clock()
    r = Rehearsal(
        REHEARSAL_PAIR,
        model=HangingModel(),
        speaker=FakeSpeaker(clock),
        clock=clock,
        timing=REHEARSAL,
        voice="fake/voice",
        stt="deepgram/nova-3",
        prep_budget=0.05,
    )
    await r.prepare()
    assert r.prep_budget_exhausted
    assert "The prep budget (0.05 s) ran out" in render_report(r)
    assert "ran out" not in render_report(rehearsal(clock)), "only when it did"


async def test_the_model_is_warmed_up_with_one_tiny_request_whatever_it_answers(caplog):
    caplog.set_level(logging.INFO, logger="nova")
    r = rehearsal(Clock())
    await r.warm_up()
    assert r.warm_up_ms is not None and "warmup ms=" in caplog.text
    assert "Model warmed up in" in render_report(r)


class DownModel(FakeModel):
    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        raise RuntimeError("connection refused")


async def test_a_model_that_cant_answer_is_never_reported_as_warm(caplog):
    """Ollama not running, a model not pulled, a bad key: the report and the log say so."""
    caplog.set_level(logging.INFO, logger="nova")
    r = rehearsal(Clock(), model=DownModel())
    await r.warm_up()
    assert "warmup ms=" in caplog.text and "error=error" in caplog.text
    assert "attempts=1" in caplog.text, "a failure it can't tell is never tried again"
    assert "Model warm-up failed (error)" in render_report(r)
    assert ", 1 attempt." in render_report(r)


class Hanging(FakeModel):
    """Answers nothing until cancelled, counting the requests it was sent."""

    def __init__(self, name: str = "fake/model") -> None:
        super().__init__()
        self.name, self.asked = name, 0

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        self.asked += 1
        await asyncio.Event().wait()
        return "ok"


async def test_the_same_model_is_warmed_up_once_and_another_at_the_same_time():
    """Twice, one after the other, a hung model held the date for two warm-up timeouts."""
    model = Hanging()
    r = Rehearsal(
        REHEARSAL_PAIR,
        model=model,
        classifier=Hanging(),
        speaker=FakeSpeaker(Clock()),
        voice="fake/voice",
        stt="deepgram/nova-3",
        clock=Clock(),
    )
    started = time.monotonic()
    await r.warm_up(deadline=0.2)
    assert model.asked == 1 and time.monotonic() - started < 0.35
    classifier = Hanging("fake/classifier")
    r = Rehearsal(
        REHEARSAL_PAIR,
        model=Hanging(),
        classifier=classifier,
        speaker=FakeSpeaker(Clock()),
        voice="fake/voice",
        stt="deepgram/nova-3",
        clock=Clock(),
    )
    started = time.monotonic()
    await r.warm_up(deadline=0.2)
    assert classifier.asked == 1 and time.monotonic() - started < 0.35, "at the same time"
    assert "Distress check's model warm-up failed (timeout)" in render_report(r)


async def test_an_approved_question_the_screen_catches_is_never_spoken_or_declined(caplog):
    """Nova says nothing about it; the console gets a code and the next question is read."""
    caplog.set_level(logging.INFO, logger="nova")
    clock = Clock()
    r = rehearsal(clock)
    r.command("ask Maya, what does Theo look like?")
    r.command("ask Mountains or sea?")
    await r.prepare()
    r.start()
    for t in range(1, 80):  # from the room starts at 72 s in a rehearsal
        clock.now = float(t)
        r.tick()
        await r.speak_queued()
    assert len(lines(r, Moment.FROM_THE_ROOM)) == 1
    assert not any("look like" in text or "another night" in text for text in r.speaker.texts)
    assert "question_skipped reason=looks" in caplog.text


async def test_the_first_question_waits_for_the_dater_to_finish_their_sentence():
    """The voice detector's events reach the show runner through the on-air listener."""
    clock = Clock()
    r = await started(clock)  # REHEARSAL: the first question is due at 13.5 s

    def first_questions() -> list:
        return [c for c in r.cues if getattr(c, "moment", None) is Moment.FIRST_QUESTION]

    clock.now = 13.0
    r.speech_started()
    clock.now = 14.0
    r.tick()
    assert first_questions() == [], "Maya is mid-sentence"
    clock.now = 15.0
    r.speech_ended()
    assert first_questions() == [], "a pause between her sentences, maybe"
    clock.now = 16.5  # the turn gap: 1.5 s of quiet
    r.tick()
    assert len(first_questions()) == 1


async def test_only_the_line_that_answers_the_dater_is_measured_as_a_reply():
    """A beat that comes due while the check runs arrives in the same batch as the kind
    line; it used to take the reply's place, leaving the kind line measured as prepared."""
    words = "He keeps looking at me in a way I really hate."
    clock = Clock()
    r = await started(clock, model=FakeModel(labels={words: "yes"}, clock=clock, check_takes=1.0))
    clock.now = 19.5  # Theo's first question is due at 20.25 s
    r.speech_ended()
    r.transcript(words)
    close(r, clock)  # the check runs from 20.1 s to 21.1 s
    await r.heard()
    await r.speak_queued()
    kind = lines(r, Moment.NEED_A_MOMENT)[0]
    assert kind.kind is Kind.REPLY, "the kind line answers the dater"


async def test_characters_count_only_lines_that_played():
    clock = Clock()
    r = await started(clock, speaker=SilentSpeaker(clock))
    assert r.spoken and r.characters_spoken == 0, "nothing played, nothing billed"


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
    assert r.speaker.stops == 2, "Nova stops mid-line first, for the moment and the end"
    assert r.finished


def held_until_bear_with_us(r: Rehearsal, clock: Clock) -> None:
    """Maya asks for a moment at 30 s; rehearsal timing covers every 3 s and asks the room
    to bear with us at 39 s. Nothing is spoken yet: Nova was busy the whole time."""
    clock.now = 30.0
    r.command("moment")
    for t in (33.0, 36.0, 39.0):
        clock.now = t
        r.tick()
    assert [c for c in r.cues if getattr(c, "moment", None) is Moment.BEAR_WITH_US]


@pytest.mark.parametrize("command", ["resume", "back"])
async def test_talk_that_covered_a_hold_is_never_said_once_the_hold_is_over(command):
    """Before, the lines queued to cover a hold were still said after the moderator
    resumed: "we're taking a short pause" and "Bear with us a little longer" for twenty
    seconds of a date that had moved on (the scenario run)."""
    clock = Clock()
    r = await started(clock)
    held_until_bear_with_us(r, clock)
    clock.now = 40.0
    r.command(command)
    await r.speak_queued()
    assert lines(r, Moment.BEAR_WITH_US) == [] and lines(r, Moment.ROOM_TALK) == []
    assert script.NEED_A_MOMENT in r.speaker.texts


async def test_the_second_dater_is_asked_only_once_the_first_had_time_to_answer():
    """Nova runs late: Maya's question plays until 19.9 s, and Theo's is due by the clock at
    20.25 s. It waits for Maya's answer window instead."""
    clock = Clock()
    r = await started(clock, speaker=FakeSpeaker(clock, length=6.0))

    def asked() -> list[Seat | None]:
        return [c.about for c in r.cues if getattr(c, "moment", None) is Moment.FIRST_QUESTION]

    clock.now = 13.6
    r.tick()
    await r.speak_queued()
    clock.now = 20.3
    r.tick()
    assert asked() == [Seat.LEFT]
    clock.now = 26.0
    r.tick()
    assert asked() == [Seat.LEFT, Seat.RIGHT]


async def test_talk_that_covers_a_hold_is_said_while_the_hold_lasts():
    clock = Clock()
    r = await started(clock)
    held_until_bear_with_us(r, clock)
    await r.speak_queued()
    assert len(lines(r, Moment.ROOM_TALK)) == 3 and len(lines(r, Moment.BEAR_WITH_US)) == 1


async def test_talk_to_the_room_still_being_written_when_the_hold_ends_is_never_said():
    clock = Clock()
    gate = asyncio.Event()
    model = GatedWriter(gate)
    r = await started(clock, model=model)
    model.gating = True
    clock.now = 30.0
    r.command("moment")
    speaking = asyncio.ensure_future(r.speak_queued())
    await asyncio.sleep(0.05)  # the moment's line is said; the room talk waits on the model
    r.command("back")
    gate.set()
    await asyncio.wait_for(speaking, 1.0)
    await r.speak_queued()
    assert lines(r, Moment.ROOM_TALK) == []
    assert r.speaker.texts[-2:] == [script.NEED_A_MOMENT, script.WELCOME_BACK]


class PlaysUntilStopped(FakeSpeaker):
    """Once armed, plays one line until Nova is told to stop."""

    def __init__(self, clock: Clock, line: str) -> None:
        super().__init__(clock)
        self.line, self.armed = line, False
        self._stopped = asyncio.Event()

    async def speak(self, text: str) -> float | None:
        if not (self.armed and text == self.line):
            return await super().speak(text)
        self.texts.append(text)
        self._stopped.clear()
        started = self.clock.now
        await self._stopped.wait()
        return started

    def stop(self) -> None:
        super().stop()
        self._stopped.set()


async def test_a_dater_back_while_nova_talks_to_the_room_is_welcomed_at_once():
    """Before, the line covering the hold played to its end first, so "And we're back."
    came about 5 s after the dater was (the scenario run)."""
    clock = Clock()
    talk = "Thanks for waiting, room."
    speaker = PlaysUntilStopped(clock, talk)
    r = await started(clock, speaker=speaker, reply=talk)
    speaker.armed = True
    clock.now = 30.0
    r.command("moment")
    speaking = asyncio.ensure_future(r.speak_queued())
    await asyncio.sleep(0.05)  # the moment's line is said; Nova is mid-way through the talk
    assert speaker.texts[-2:] == [script.NEED_A_MOMENT, talk]
    r.command("back")
    await asyncio.wait_for(speaking, 1.0)
    await r.speak_queued()
    assert speaker.texts[-2:] == [talk, script.WELCOME_BACK]
    assert speaker.stops == 2, "for the moment, then for the end of the hold"


async def test_a_dater_who_starts_as_nova_does_goes_first_and_nova_says_it_again():
    """After a silence, Maya and Nova took the floor at the same moment and Nova talked over
    her (the LiveKit run with listening daters): now Nova stops and lets her go first."""
    clock = Clock()
    speaker = PlaysUntilStopped(clock, script.LIGHTNING_UNLOCKED)
    r = await started(clock, speaker=speaker)
    speaker.armed = True
    clock.now = 30.0
    r.command("unlock")
    speaking = asyncio.ensure_future(r.speak_queued())
    await asyncio.sleep(0.05)  # Nova has just started the line
    clock.now = 30.6
    r.speech_started()  # and so has Maya
    assert speaker.stops == 1, "Nova stops at once"
    speaker.armed = False
    await asyncio.sleep(0.05)
    assert speaker.texts.count(script.LIGHTNING_UNLOCKED) == 1, "not over her"
    clock.now = 34.0
    r.speech_ended()  # a pause between her sentences
    await asyncio.sleep(0.1)
    clock.now = 34.5
    r.speech_started()
    clock.now = 37.0
    r.speech_ended()
    await asyncio.sleep(0.1)
    clock.now = 38.0
    await asyncio.sleep(0.1)
    assert speaker.texts.count(script.LIGHTNING_UNLOCKED) == 1, "not in her pause, nor yet"
    clock.now = 38.5  # 1.5 s of quiet
    await asyncio.wait_for(speaking, 1.0)
    assert speaker.texts.count(script.LIGHTNING_UNLOCKED) == 2, "said again once she's done"
    assert speaker.stops == 1, "a dater who starts again isn't a second collision"
    assert len(lines(r, Moment.LIGHTNING_UNLOCKED)) == 1


async def test_a_dater_who_starts_well_into_novas_line_is_not_a_collision():
    clock = Clock()
    speaker = PlaysUntilStopped(clock, script.LIGHTNING_UNLOCKED)
    r = await started(clock, speaker=speaker)
    speaker.armed = True
    clock.now = 30.0
    r.command("unlock")
    speaking = asyncio.ensure_future(r.speak_queued())
    await asyncio.sleep(0.05)
    clock.now = 32.0  # two seconds into Nova's line: the dater talks over Nova, not the reverse
    r.speech_started()
    assert speaker.stops == 0
    speaking.cancel()
    await asyncio.gather(speaking, return_exceptions=True)


async def test_a_dater_who_needs_a_moment_cuts_nova_off_mid_line():
    """Before, Nova finished an eight-second question to both daters first (the scenario
    run). Now it stops at once, as for a report or a distress pause."""
    clock = Clock()
    r = await started(clock, speaker=FakeSpeaker(clock, length=12.0))
    clock.now = 40.0
    r.tick()  # the first questions, twelve seconds each, queued and not yet spoken
    r.command("moment")
    await r.speak_queued()
    assert r.speaker.stops == 1
    assert lines(r, Moment.FIRST_QUESTION) == [], "dropped, as for the kind line"
    assert lines(r, Moment.NEED_A_MOMENT)


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


# --- Before the date starts and after it ends ----------------------------------------------
# nova/agent.py hears daters from the moment the session starts, while set pieces are still
# being written, so nothing a dater or the chat does may raise before start().

COMMANDS = ("moment", "back", "unlock", "ask Mountains or sea?", "reveal", "keep", "resume")


async def finished(clock: Clock) -> Rehearsal:
    r = await started(clock)
    for t in range(1, 240):
        clock.now = float(t)
        r.tick()
        await r.speak_queued()
    assert r.finished
    return r


async def test_a_dater_who_wants_to_stop_before_the_start_reaches_the_moderator():
    clock = Clock()
    r = rehearsal(clock)
    r.speech_started()
    r.speech_ended()
    r.transcript("I want to stop.")
    close(r, clock)  # nova/agent.py's listen() does this while set pieces are prepared
    await r.heard()
    await r.speak_queued()
    assert alerts(r) == [ModeratorAlert.DATER_DISTRESSED]
    assert r.speaker.texts == [], "nothing on air before the date"


async def test_a_yes_from_the_check_before_the_start_only_alerts():
    words = "I want to stop talking to him."
    clock = Clock()
    r = rehearsal(clock, model=FakeModel(labels={words: "yes"}))
    r.transcript(words)
    close(r, clock)
    await r.heard()
    await r.speak_queued()
    assert alerts(r) == [ModeratorAlert.DATER_DISTRESSED] and r.speaker.texts == []


async def test_commands_before_the_start_never_raise_and_are_logged(caplog):
    caplog.set_level(logging.INFO, logger="nova")
    r = rehearsal(Clock())
    for text in (*COMMANDS, "leave", "end", "theo"):
        assert r.command(text) is not None
    assert caplog.text.count("command command=") == len(COMMANDS) + 3, "one line each"
    assert r.seat is Seat.RIGHT, "choosing who speaks needs no date"
    assert alerts(r) == []
    assert r.finished, "a leave before the date ends it, and the end is never lost"
    r.start()
    assert r.speaker.texts == [] and all(not isinstance(c, Say) for c in r.cues)


async def test_after_the_date_ends_daters_and_commands_never_raise():
    clock = Clock()
    r = await finished(clock)
    r.speech_started()
    r.transcript("I want to stop.")
    r.speech_ended()
    close(r, clock)
    await r.heard()
    for text in (*COMMANDS, "leave", "end"):
        r.command(text)
    assert alerts(r)[-1] is ModeratorAlert.DATER_DISTRESSED


async def test_a_report_on_air_pauses_the_date_after_alerting_the_moderator():
    clock = Clock()
    r = await started(clock)
    clock.now = 40.0
    r.tick()
    await r.speak_queued()
    said = len(r.speaker.texts)
    assert r.command("report") is Command.REPORT
    await r.speak_queued()
    assert alerts(r) == [ModeratorAlert.DATER_REPORTED]
    assert r.speaker.texts[said] == script.NEED_A_MOMENT and r.speaker.stops == 1


@pytest.mark.parametrize("when", ["before the start", "after the end"])
async def test_a_report_off_air_alerts_the_moderator_and_nothing_else(when):
    clock = Clock()
    r = rehearsal(clock) if when == "before the start" else await finished(clock)
    said = len(r.speaker.texts)
    r.command("report")
    await r.speak_queued()
    assert alerts(r)[-1] is ModeratorAlert.DATER_REPORTED
    assert r.speaker.texts[said:] == []


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
    assert "Utterances: 1 (0 by the instant layer)" in report
    assert "(grace 0.6 s after the last transcript, at most 1.5 s after the dater stopped)" in (
        report
    )
    reply = next(row for row in report.splitlines() if row.lstrip().startswith("reply"))
    assert "1000 ms" in reply, "the reply's grace: this date ticks once a second"
    for secret in ("OKAPI", "PANGOLIN", "555", "Maya", "Theo", "Here we go"):
        assert secret not in report, secret
        assert secret not in caplog.text, secret
    assert "prepared lines" in report and "Fallback rate" in report and "credit" in report


# --- Hearing the right dater, to the end of the date ----------------------------------------


async def test_a_final_that_comes_after_the_dater_stopped_is_still_theirs():
    """Speech-to-text's final lands a moment after the voice detector hears the words end.
    If the chat picks the other seat in between, the final is still the one who spoke."""
    clock = Clock()
    r = await started(clock)
    clock.now = 40.0
    r.speech_started()
    clock.now = 41.0
    r.speech_ended()
    r.command("theo")
    clock.now = 41.3
    r.transcript("I want to stop.")
    close(r, clock)
    distressed = [cue for cue in r.cues if isinstance(cue, AlertModerator)]
    assert distressed == [AlertModerator(ModeratorAlert.DATER_DISTRESSED, Seat.LEFT)]


async def test_a_final_long_after_anyone_stopped_belongs_to_the_seat_the_chat_chose():
    clock = Clock()
    r = await started(clock)
    clock.now = 40.0
    r.speech_started()
    r.speech_ended()
    r.command("theo")
    clock.now = 45.0
    r.transcript("I want to stop.")
    close(r, clock)
    alerted = [cue.seat for cue in r.cues if isinstance(cue, AlertModerator)]
    assert alerted == [Seat.RIGHT]


async def test_what_a_dater_says_as_the_date_ends_still_reaches_the_moderator():
    """The date's last lines play, then whatever a dater was still saying is acted on and
    its check gets time to finish, before the rehearsal stops listening."""
    clock = TickingClock()
    model = FakeModel(labels={"is this almost over": "yes"})
    r = rehearsal(clock, StuckVoice(clock), model=model)
    await r.prepare()
    running = asyncio.create_task(r.run(every=0, final_wait=0.5))
    while not r.finished:
        await asyncio.sleep(0)
    r.speech_ended()
    r.transcript("is this almost over")
    await asyncio.wait_for(running, 5)
    assert model.checked[-1] == "is this almost over"
    assert ModeratorAlert.DATER_DISTRESSED in alerts(r)


async def test_speech_recognition_is_billed_from_when_the_session_started_listening():
    """The agent listens from the start of the session, through the warm-up and the prep
    budget, so the credit estimate counts that too; the date's own minutes don't."""
    clock = Clock()
    r = rehearsal(clock)
    listening = asyncio.create_task(r.listen())
    await asyncio.sleep(0)
    try:
        clock.now = 60.0
        await r.prepare()
        r.start()
        clock.now = 240.0
        assert (r.stt_seconds, r.date_seconds) == (240.0, 180.0)
        assert "speech recognition 4.0 min" in render_report(r)
    finally:
        listening.cancel()


# --- Stop means stop -----------------------------------------------------------------------


class GatedWriter(FakeModel):
    """Once gating, writes a line only when the gate opens, which may be never: set pieces
    are prepared before, so only live lines wait."""

    def __init__(self, gate: asyncio.Event, **options) -> None:
        super().__init__(**options)
        self.write_gate = gate
        self.gating = False

    async def write(self, *, system: str, prompt: str) -> str:
        if self.gating:
            await self.write_gate.wait()
        return await super().write(system=system, prompt=prompt)


class StuckOn(FakeSpeaker):
    """Never finishes saying one line."""

    def __init__(self, clock: Clock, line: str) -> None:
        super().__init__(clock)
        self.line = line

    async def speak(self, text: str) -> float | None:
        if text == self.line:
            self.texts.append(text)
            await asyncio.Event().wait()
        return await super().speak(text)


async def nova_waiting_on(
    r: Rehearsal, clock: Clock, words: str, *, at: float = 40.0
) -> asyncio.Future:
    """The dater says words to Nova at a time, and Nova picks the reply up and waits on it."""
    clock.now = at
    r.speech_ended()
    r.transcript(words)
    close(r, clock)
    speaking = asyncio.ensure_future(r.speak_queued())
    await asyncio.sleep(0.05)
    return speaking


@pytest.mark.parametrize(
    ("command", "moment"),
    [("end", Moment.MODERATOR_END), ("leave", Moment.DATER_LEFT), ("report", Moment.NEED_A_MOMENT)],
)
async def test_a_reply_waiting_for_its_check_is_never_said_after_a_stop(command, moment):
    """The line Nova has already picked up is stopped too, and the stop's own line comes
    at once, without waiting for the check."""
    clock = Clock()
    check_due = asyncio.Event()
    r = await started(clock, model=FakeModel(gate=check_due))
    speaking = await nova_waiting_on(r, clock, "Nova, tea or coffee?")
    r.command(command)
    await asyncio.wait_for(speaking, 1.0)
    await r.speak_queued()
    assert lines(r, Moment.REPLY) == []
    assert lines(r, moment), "said while the check still runs"
    check_due.set()
    await r.heard()
    await r.speak_queued()
    assert lines(r, Moment.REPLY) == [], "not after the check either"


async def test_the_kind_line_comes_at_once_while_a_reply_waits_for_its_check():
    clock = Clock()
    check_due = asyncio.Event()
    r = await started(clock, model=FakeModel(gate=check_due))
    speaking = await nova_waiting_on(r, clock, "Nova, tea or coffee?")
    await transcripts_now(r, clock, "I want to stop.")
    await asyncio.wait_for(speaking, 1.0)
    await r.speak_queued()
    assert script.DISTRESS_PAUSE in r.speaker.texts
    assert lines(r, Moment.REPLY) == []
    check_due.set()
    await r.heard()


async def transcripts_now(r: Rehearsal, clock: Clock, text: str) -> None:
    """The dater says text and stops, while Nova may be busy: nobody waits for Nova."""
    clock.now += 1.0
    r.speech_ended()
    r.transcript(text)
    close(r, clock)


async def test_a_live_line_still_being_written_is_never_said_after_a_stop():
    clock = Clock()
    written = asyncio.Event()
    model = GatedWriter(written)
    r = await started(clock, model=model)
    model.gating = True
    speaking = await nova_waiting_on(r, clock, "Nova, tea or coffee?")
    r.command("end")
    await asyncio.sleep(0.05)
    assert speaking.done(), "Nova let go of the line at once"
    await speaking
    await r.speak_queued()
    written.set()
    await asyncio.sleep(0)
    assert lines(r, Moment.REPLY) == []
    assert r.speaker.texts[-1] == script.MODERATOR_END


async def test_a_second_unkind_remark_said_to_nova_is_never_answered_before_the_pause():
    """The deterministic case: the check's own conduct label stops the show right after the
    verdict lets the reply through."""
    clock = Clock()
    unkind = '{"label": "no", "conduct": "unkind"}'
    first, second = "You're boring, honestly.", "Nova, isn't Theo boring?"
    check_due = asyncio.Event()
    check_due.set()
    model = FakeModel(labels={first: unkind, second: unkind}, gate=check_due)
    r = await started(clock, model=model)
    await transcripts(r, clock, (40.0, first))
    assert script.KEEP_IT_KIND in r.speaker.texts
    check_due.clear()
    speaking = await nova_waiting_on(r, clock, second, at=45.0)  # Nova picks the reply up
    check_due.set()
    await r.heard()
    await speaking
    await r.speak_queued()
    assert lines(r, Moment.REPLY) == []
    assert script.SHOW_PAUSE in r.speaker.texts


async def test_a_line_cut_before_its_first_word_is_not_counted_as_never_played():
    """Target 1 fails for a line that never played: not for one Nova was told to stop."""
    clock = Clock()

    class CutSpeaker(FakeSpeaker):
        async def speak(self, text: str) -> float | None:
            self.texts.append(text)
            if len(self.texts) == 1:
                r.command("end")  # stopped before the line's first audio
                return None
            return await super().speak(text)

    r = rehearsal(clock, CutSpeaker(clock))
    await r.prepare()
    r.start()
    await r.speak_queued()
    assert all(spoken.from_cue_ms is not None for spoken in r.spoken)
    assert r.speaker.texts[-1] == script.MODERATOR_END


@pytest.mark.parametrize("label", ["maybe", "slow"])
async def test_the_speaking_task_stops_when_cancelled_whatever_it_waits_on(label):
    """Cancelled while Nova says "Take all the time you need." after a maybe, or while a live
    line is still written: the task ends, and nothing it was waiting on keeps it alive."""
    clock = Clock()
    words = "Nova, he keeps asking where I live."
    model = FakeModel(labels={words: "maybe"}) if label == "maybe" else GatedWriter(asyncio.Event())
    r = await started(clock, model=model, speaker=StuckOn(clock, script.TAKE_YOUR_TIME))
    if isinstance(model, GatedWriter):
        model.gating = True
    clock.now = 40.0
    r.speech_ended()
    r.transcript(words)
    close(r, clock)
    await r.heard()
    speaking = asyncio.ensure_future(r._speak_forever())
    await asyncio.sleep(0.05)
    speaking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(speaking, 1.0)
    assert speaking.cancelled()


# --- Nova never starts over a dater ---------------------------------------------------------


class ReactingRoom(FakeSpeaker):
    """A dater starts talking while Nova says the result, and stops a moment later."""

    def __init__(self, clock: Clock) -> None:
        super().__init__(clock)
        self.events: list[str] = []
        self.rehearsal: Rehearsal | None = None

    async def speak(self, text: str) -> float | None:
        self.events.append(text)
        started = await super().speak(text)
        if text == script.RESULT_NOT_MUTUAL and self.rehearsal is not None:
            r = self.rehearsal
            r.speech_started()

            def stop() -> None:
                self.events.append("dater stopped")
                r.speech_ended()

            asyncio.get_running_loop().call_later(0.05, stop)
        return started


async def test_lines_queued_together_wait_for_a_dater_who_starts_talking_between_them():
    """The result, the outro and the invitation are queued at once while nobody speaks; the
    outro still waits for a dater who reacts to the result, and is measured from when they
    finished."""
    clock = Clock()
    room = ReactingRoom(clock)
    r = await started(clock, speaker=room)
    room.rehearsal = r
    for t in range(41, 175):
        clock.now = max(clock.now, float(t))
        r.tick()
        await r.speak_queued()
    result = room.events.index(script.RESULT_NOT_MUTUAL)
    assert room.events[result + 1] == "dater stopped", "the outro waited for them"
    outro = lines(r, Moment.OUTRO)[0]
    assert outro.from_cue_ms is not None and outro.from_cue_ms < 1000


async def test_a_line_waiting_for_a_dater_is_dropped_if_nova_is_stopped():
    clock = Clock()
    r = await started(clock)
    clock.now = 40.0
    r.tick()  # the first question, queued
    r.speech_started()  # the dater is speaking: the line waits
    speaking = asyncio.ensure_future(r.speak_queued())
    await asyncio.sleep(0.05)
    r.command("end")
    await asyncio.wait_for(speaking, 1.0)
    await r.speak_queued()
    assert lines(r, Moment.FIRST_QUESTION) == []
    assert r.speaker.texts[-1] == script.MODERATOR_END


async def test_a_first_unkind_remark_said_to_nova_gets_only_lets_keep_it_kind():
    """The product owner's choice: Nova doesn't answer a remark it is about to call unkind."""
    clock = Clock()
    words = "Nova, isn't Theo the most boring man alive?"
    unkind = '{"label": "no", "conduct": "unkind"}'
    r = await started(clock, model=FakeModel(labels={words: unkind}))
    await transcripts(r, clock, (40.0, words))
    assert lines(r, Moment.REPLY) == []
    assert r.speaker.texts[-1] == script.KEEP_IT_KIND


async def test_the_log_never_says_which_dater_chose_what(caplog):
    """A not-mutual result protects both daters only if nobody can read who chose reveal."""
    caplog.set_level(logging.INFO, logger="nova")
    clock = Clock()
    r = await started(clock)
    for command in ("maya", "reveal", "theo", "unseen"):
        r.command(command)
    assert "reveal" not in caplog.text and "unseen" not in caplog.text
    assert "command command=- seat=-" in caplog.text


async def test_the_cues_the_rehearsal_keeps_hold_nothing_a_dater_or_listener_said():
    clock = Clock()
    r = await started(clock)
    r.command("ask OKAPI-QUESTION?")
    await transcripts(r, clock, (40.0, "Nova, PANGOLIN-SPEECH, tea or coffee?"))
    for t in range(41, 120):
        clock.now = float(t)
        r.tick()
        await r.speak_queued()
    kept = repr(r.cues)
    assert "OKAPI" not in kept and "PANGOLIN" not in kept


# --- Nova not ready: the doors hold for the moderator (decision 0005, 2026-10-22) ------------


class WontWarm(FakeModel):
    """Its warm-up fails, and it fails the same way for every line."""

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        if '"sentence": "' not in prompt:
            raise RuntimeError("the model is down")
        return await super().label(system=system, prompt=prompt, schema=schema)

    async def write(self, *, system: str, prompt: str) -> str:
        raise RuntimeError("the model is down")


async def not_ready(clock: Clock) -> Rehearsal:
    r = rehearsal(clock, model=WontWarm())
    await r.warm_up(deadline=1.0)
    await r.prepare()
    return r


async def test_a_ready_nova_opens_the_date_at_once_with_no_alert():
    clock = Clock()
    r = rehearsal(clock)
    await r.warm_up(deadline=1.0)
    await r.prepare()
    assert r.ready and await asyncio.wait_for(r.hold_until_ready(), 1.0)
    assert ModeratorAlert.NOVA_NOT_READY not in alerts(r) and r.held_for is None


async def test_nova_not_ready_holds_the_doors_until_the_moderator_opens_the_date():
    """Two rehearsals on 2026-10-18 opened with every set piece a fallback after a failed
    warm-up, and only the report said so. Now the moderator is told and decides."""
    clock = Clock()
    r = await not_ready(clock)
    assert not r.ready
    held = asyncio.ensure_future(r.hold_until_ready())
    await asyncio.sleep(0.02)
    assert ModeratorAlert.NOVA_NOT_READY in alerts(r) and not held.done(), "never on its own"
    clock.now = 12.0
    r.command("start")
    assert await asyncio.wait_for(held, 1.0) is True and r.held_for == 12.0
    r.start()
    assert "the doors held 12.0 s for the moderator, who opened the date" in render_report(r)


async def test_the_moderator_can_end_a_date_nova_isnt_ready_for():
    clock = Clock()
    r = await not_ready(clock)
    held = asyncio.ensure_future(r.hold_until_ready())
    await asyncio.sleep(0.02)
    r.command("end")
    assert await asyncio.wait_for(held, 1.0) is False and r.finished
    assert "ended it" in render_report(r)


async def test_every_set_piece_a_fallback_is_not_ready_even_when_the_warm_up_was_fine():
    class WarmButBroken(WontWarm):
        async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
            return "ok" if '"sentence": "' not in prompt else "no"

    r = rehearsal(Clock(), model=WarmButBroken())
    await r.warm_up(deadline=1.0)
    await r.prepare()
    assert all(w.error is None for _, w in r.warmed) and not r.ready
