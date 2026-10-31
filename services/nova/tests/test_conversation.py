"""Conversational runner and listener regressions, on controlled clocks."""

import asyncio
from dataclasses import replace

from test_rehearsal import Clock, FakeModel, FakeSpeaker, Plays
from test_show_runner import Show, first_question_times, paced_date

from nova import script
from nova.cards import Seat
from nova.cues import Moment, Say, Segment
from nova.fixtures import REHEARSAL_PAIR
from nova.rehearsal import Rehearsal, render_report
from nova.timing import FULL

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT


def first_question() -> Show:
    show = Show(paced=True, conversational=True, timing=replace(FULL, second_prompt_after=6))
    show.until(48)
    show.runner.line_done(48, show.says(Moment.FIRST_QUESTION)[0][1])
    return show


def say(show: Show, start: float, end: float, seat: Seat, words: str, *, nova=False) -> None:
    show.at(start, "speech_started", seat)
    show.until(end)
    show.runner.turn_words(end, seat, words, to_nova=nova)
    show.at(end, "speech_stopped", seat)


def test_thinking_is_not_a_finished_answer():
    show = first_question()
    say(show, 49, 50, LEFT, "Let me think.")
    show.until(60)
    assert first_question_times(show) == [45]
    say(show, 61, 65, LEFT, "A cabin by a lake.")
    show.until(65.5)
    assert first_question_times(show) == [45]
    # Theo's prompt has been due since 51 s: 15 s on, a brief opening is enough (0012).
    show.until(66)
    assert first_question_times(show) == [45, 66]


def test_the_other_dater_can_pick_up_the_thread_before_the_next_prompt():
    show = first_question()
    say(show, 49, 50, LEFT, "A cabin. Have you tried hiking, Theo?")
    show.until(55)
    assert first_question_times(show) == [45]
    say(show, 56, 60, RIGHT, "Yes, there was this trail last summer, and")
    show.until(65)
    assert first_question_times(show) == [45]
    say(show, 66, 70, RIGHT, "We finally got to the top.")
    show.until(73)
    # Due since 51 s, so a brief opening after Theo is enough (decision 0012).
    assert first_question_times(show) == [45, 70.5]


def test_silence_is_not_treated_as_a_pass_at_six_seconds():
    show = first_question()
    show.until(60)
    assert first_question_times(show) == [45]
    show.until(68)
    assert first_question_times(show) == [45, 68]


def test_explicit_pass_releases_the_opportunity_after_a_quiet_gap():
    show = first_question()
    say(show, 49, 50, LEFT, "I'll pass.")
    show.until(52.5)
    assert first_question_times(show) == [45, 52.5]


def test_clarification_returns_the_opportunity_to_the_person_asked():
    show = first_question()
    say(show, 49, 50, LEFT, "Nova, do you mean a holiday?", nova=True)
    show.runner.line_done(54, Say(Moment.REPLY, about=LEFT, return_floor=True))
    show.until(65)
    assert first_question_times(show) == [45]
    say(show, 66, 70, LEFT, "A cabin by a lake.")
    show.until(73)
    # Due since 51 s, so a brief opening after Maya is enough (decision 0012).
    assert first_question_times(show) == [45, 70.5]


def test_a_real_speaker_is_never_talked_over_after_thirty_seconds():
    show = first_question()
    show.at(49, "speech_started", LEFT)
    show.until(115)
    assert first_question_times(show) == [45]
    show.runner.turn_words(116, LEFT, "That's all.")
    show.at(116, "speech_stopped", LEFT)
    show.until(119)
    # Overdue after a minute of her speech: a brief opening, never during it (0012).
    assert first_question_times(show) == [45, 116.5]


def test_safety_holds_and_the_full_decision_window_are_preserved():
    show = Show(paced=True, conversational=True)
    paced_date(show, 100)
    show.at(101, "need_a_moment", LEFT)
    show.until(125)
    show.at(126, "back", LEFT)
    paced_date(show, 800)
    assert show.times(Say, moment=Moment.RESULT)[0] - show.times(Say, moment=Moment.TIME)[0] == 60
    assert len(show.says(Moment.LAST_WORD)) == 2


def test_free_talk_absorbs_earlier_delay_without_losing_the_activity_order():
    show = Show(paced=True, conversational=True)
    show.until(80)
    show.runner.line_done(80, show.says(Moment.FIRST_QUESTION)[0][1])
    say(show, 81, 84, LEFT, "A cabin.")
    paced_date(show, 600)
    segments = [segment for _, segment in show.segments()]
    assert segments.index(Segment.FREE_TALK) < segments.index(Segment.FROM_THE_ROOM)
    assert show.runner.conversation_delay > 0
    assert show.runner._recovered_delay > 0


def test_a_last_word_keeps_twenty_seconds_after_a_late_prompt():
    show = Show(paced=True, conversational=True)
    paced_date(show, 419)
    show.until(440)
    first = show.says(Moment.LAST_WORD)[0][1]
    show.runner.line_done(440, first)
    say(show, 441, 442, LEFT, "I had a lovely time.")
    show.until(459.5)
    assert len(show.says(Moment.LAST_WORD)) == 1
    show.until(460)
    assert len(show.says(Moment.LAST_WORD)) == 2


def test_a_new_speech_start_at_a_due_transition_is_processed_before_the_clock():
    show = first_question()
    say(show, 49, 50, LEFT, "A cabin by a lake.")
    show.until(52)
    show.now = 52.5
    show._record(show.runner.speech_started(52.5, RIGHT))
    assert first_question_times(show) == [45]


def rehearsal(clock: Clock, model: FakeModel | None = None, **options) -> Rehearsal:
    return Rehearsal(
        REHEARSAL_PAIR,
        model=model or FakeModel(),
        speaker=options.pop("speaker", None) or FakeSpeaker(clock),
        clock=clock,
        voice="fake/voice",
        stt="fake/stt",
        timing=FULL,
        conversational=True,
        first_up=LEFT,
        meaning_check=False,
        **options,
    )


async def asked_first(clock: Clock, **options) -> Rehearsal:
    """Nova has asked Maya her first question."""
    r = rehearsal(clock, **options)
    r.start()
    await r.speak_queued()
    clock.now = 45
    r.tick()
    await r.speak_queued()
    return r


async def to_nova(r: Rehearsal, clock: Clock, seat: Seat, words: str) -> None:
    """A dater says something to Nova, and the utterance closes with its check done."""
    r.speech_started(seat)
    clock.now += 1
    r.transcript(words, seat)
    r.speech_ended(seat)
    clock.now += 0.6
    r.tick()
    await r.heard()


async def test_listener_updates_the_floor_without_an_extra_model_request():
    clock = Clock()
    model = FakeModel()
    r = rehearsal(clock, model)
    r.start()
    clock.now = 45
    r.tick()
    await r.speak_queued()
    r.speech_started(LEFT)
    r.transcript("Let me think.", LEFT)
    r.speech_ended(LEFT)
    clock.now += 0.6
    r.tick()
    await r.heard()
    assert r._runner.floor.waiting(clock.now)
    assert model.checked == ["Let me think."]
    r.close()


def replies(r: Rehearsal) -> list[str]:
    return [line.text for line in r.lines if line.moment is Moment.REPLY]


async def test_a_laugh_from_the_other_dater_never_costs_the_asker_their_answer():
    clock = Clock()
    r = await asked_first(clock)
    await to_nova(r, clock, LEFT, "Nova, do you mean a holiday?")
    r.speech_started(RIGHT)
    clock.now += 0.3
    r.transcript("Ha.", RIGHT)
    r.speech_ended(RIGHT)
    clock.now += 2  # past the turn gap after Theo's laugh
    await r.speak_queued()
    assert len(replies(r)) == 1
    floor = r._runner.floor
    assert floor.expected == {LEFT} and floor.clarifying is None
    assert floor.since == clock.now, "Maya's opportunity runs from the answer, not the laugh"
    r.close()


async def test_a_reply_the_asker_has_moved_on_from_is_dropped_and_both_are_still_checked():
    clock = Clock()
    model = FakeModel()
    r = await asked_first(clock, model=model)
    await to_nova(r, clock, LEFT, "Nova, do you mean a holiday?")
    r.speech_started(LEFT)
    clock.now += 2
    r.transcript("Actually never mind, the cabin by the lake is my answer.", LEFT)
    r.speech_ended(LEFT)
    clock.now += 2
    r.tick()
    await r.heard()
    await r.speak_queued()
    assert replies(r) == []
    assert model.checked == [
        "Nova, do you mean a holiday?",
        "Actually never mind, the cabin by the lake is my answer.",
    ]
    r.close()


async def test_the_kind_line_is_said_whatever_either_dater_says_after_it():
    clock = Clock()
    words = "Nova, honestly this is a lot right now."
    r = await asked_first(clock, model=FakeModel(labels={words: "error"}))
    alerts = r.alerts
    await to_nova(r, clock, LEFT, words)  # the check fails: unchecked, so the kind line
    r.speech_started(RIGHT)
    clock.now += 0.3
    r.speech_ended(RIGHT)
    r.speech_started(LEFT)
    clock.now += 2
    r.transcript("Sorry, I keep going on about my week at work.", LEFT)
    r.speech_ended(LEFT)
    await r.speak_queued()
    assert replies(r) == [script.TAKE_YOUR_TIME]
    assert r.alerts > alerts, "and the moderator heard the check failed"
    r.close()


async def test_a_reply_the_other_dater_talks_over_is_dropped_never_said_over_them():
    clock = Clock()
    r = await asked_first(clock)
    await to_nova(r, clock, LEFT, "Nova, do you mean a holiday?")
    r.speech_started(RIGHT)
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0.06)
    assert not task.done() and replies(r) == []
    clock.now += FULL.talk_over_grace + 0.1
    await asyncio.wait_for(task, 1)
    assert replies(r) == []
    assert r._runner.floor.expected == {LEFT}, "Maya keeps her opportunity"
    r.speech_ended(RIGHT)
    r.close()


class HoldsTheFirstLine(Plays):
    """Plays its first line until stopped or finished, like a real voice; the rest at once."""

    async def speak(self, text: str) -> float | None:
        if not self.texts:
            self.held.add(text)
        return await super().speak(text)


async def test_the_other_daters_mm_never_hides_a_dater_still_speaking_over_nova():
    clock = Clock()
    voice = HoldsTheFirstLine(clock)
    r = rehearsal(clock, speaker=voice, yield_hold=0.01)
    r._queue_cues([Say(Moment.FIRST_QUESTION, about=LEFT)])
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0.01)
    clock.now += 2  # past the yield window: Theo must keep going for the hold
    r.speech_started(RIGHT)
    r.speech_started(LEFT)  # Maya's "mm" over Theo
    r.speech_ended(LEFT)
    await asyncio.sleep(0.05)
    assert voice.stops == 1, "Theo kept going, so Nova stopped for him"
    r.speech_ended(RIGHT)
    clock.now += 3
    voice.finish()
    await asyncio.wait_for(task, 1)
    r.close()


async def test_with_two_inputs_a_typed_command_is_the_typists_whoever_spoke_last():
    clock = Clock()
    r = rehearsal(clock, input_streams=2)
    r.start()
    await r.speak_queued()
    r.speech_started(RIGHT)
    clock.now += 1
    r.transcript("That sounds lovely.", RIGHT)
    r.speech_ended(RIGHT)
    r.command("moment", LEFT)
    assert r._runner._away is LEFT
    r.command("back", LEFT)
    r.speech_started(LEFT)
    r.speech_ended(LEFT)
    r.command("report", RIGHT)
    assert r._runner._reporter is RIGHT
    r.close()


async def test_the_report_has_the_running_order_last_words_window_and_both_airtimes():
    clock = Clock()
    r = rehearsal(clock, speaker=FakeSpeaker(clock, length=2.0))
    r.start()
    remarked = False
    while not r.finished and clock.now < 900:
        clock.now += 1
        if not remarked and clock.now >= 120:  # one remark in free talk; otherwise silent
            remarked = True
            r.speech_started(LEFT)
            clock.now += 3
            r.transcript("We drove to the coast in the rain.", LEFT)
            r.speech_ended(LEFT)
        r.tick()
        await r.speak_queued()
    await r.heard()
    segments = [segment for segment, _ in r.segments]
    assert segments == [
        Segment.DOORS,
        Segment.FIRST_QUESTION,
        Segment.FREE_TALK,
        Segment.FROM_THE_ROOM,
        Segment.FREE_TALK,  # the lightning round, never unlocked
        Segment.LAST_WORD,
        Segment.TIME,
        Segment.RESULT,  # no next pair in this rehearsal, so no Next up
    ]
    assert r.window_seconds is not None and abs(r.window_seconds - FULL.decision_window) <= 1
    assert [seat for seat, _ in r.last_words] == [LEFT, RIGHT]
    assert all(given >= FULL.minimum_last_word - 1 for _, given in r.last_words)
    report = render_report(r)
    assert "Segments, seconds from the start: doors 0.0, first_question 45" in report
    assert "Last words, from Nova finishing the prompt to the next turn: left" in report
    assert "Decision window: 60" in report or "Decision window: 59" in report
    assert "of the time anyone spoke (the daters 3 s)" in report
    assert "Interruptions: a dater started speaking while Nova spoke" in report
    assert "p95" in report and "p99" in report and "0 timed out" in report
    r.close()


async def test_a_real_participant_seat_overrides_the_simulation_seat():
    clock = Clock()
    r = rehearsal(clock)
    r.command("maya")
    r.speech_started(RIGHT)
    r.transcript("A cabin by a lake.", RIGHT)
    assert r._runner.speaking == {RIGHT}
    r.speech_started(LEFT)
    r.speech_ended(RIGHT)
    assert r._runner.speaking == {LEFT}
    r.speech_ended(LEFT)
    r.close()


async def test_a_question_already_queued_never_talks_over_a_long_new_contribution():
    clock = Clock()
    r = rehearsal(clock)
    r._queue_cues([Say(Moment.FIRST_QUESTION, about=LEFT)])
    r.speech_started(RIGHT)
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0)
    clock.now = 40  # beyond both the old queue grace and the old speech cap
    await asyncio.sleep(0.06)
    assert not task.done() and not r.speaker.texts
    r.transcript("That's all.", RIGHT)
    r.speech_ended(RIGHT)
    clock.now += 3
    await asyncio.wait_for(task, 0.2)
    assert len(r.speaker.texts) == 1 and r.talked_over == 0
    r.close()


def test_two_input_streams_are_both_counted_in_the_speech_credit_estimate():
    clock = Clock()
    r = Rehearsal(
        REHEARSAL_PAIR,
        model=FakeModel(),
        speaker=FakeSpeaker(clock),
        clock=clock,
        voice="fake/voice",
        stt="fake/stt",
        input_streams=2,
    )
    r._listening_since = 0
    clock.now = 30
    assert r.stt_seconds == 60
    r.close()


def test_an_already_closed_voice_session_cannot_prevent_date_cleanup():
    class ClosedSpeaker(FakeSpeaker):
        def stop(self):
            raise RuntimeError("AgentSession isn't running")

    clock = Clock()
    r = Rehearsal(
        REHEARSAL_PAIR,
        model=FakeModel(),
        speaker=ClosedSpeaker(clock),
        clock=clock,
        voice="fake/voice",
        stt="fake/stt",
        conversational=True,
    )
    r._context.heard(LEFT, "A cabin by a lake.")
    r.close()
    r.close()
    assert r.closed and r._context.closed and len(r._context) == 0
