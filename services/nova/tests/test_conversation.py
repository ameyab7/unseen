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


async def test_a_dater_asking_to_hear_the_question_again_is_answered_without_naming_nova():
    clock = Clock()
    r = await asked_first(clock)
    await to_nova(r, clock, LEFT, "Sorry, can you repeat the question?")
    await r.speak_queued()
    assert len(replies(r)) == 1
    floor = r._runner.floor
    assert floor.expected == {LEFT}, "and the question is still Maya's to answer"
    r.close()


async def test_if_that_reply_fails_the_dater_hears_the_question_again_word_for_word():
    clock = Clock()
    model = FakeModel()
    r = await asked_first(clock, model=model)
    asked = [line.text for line in r.lines if line.moment is Moment.FIRST_QUESTION]
    model.write_error = RuntimeError("the model is down")
    await to_nova(r, clock, LEFT, "What was the question?")
    await r.speak_queued()
    assert replies(r) == asked[-1:]
    r.close()


async def test_a_repeat_request_for_the_other_dater_or_before_any_question_gets_no_reply():
    clock = Clock()
    r = await asked_first(clock)
    await to_nova(r, clock, LEFT, "Theo, can you say that again?")
    await r.speak_queued()
    assert replies(r) == [], "Maya asked Theo, not Nova"
    r.close()
    clock = Clock()
    r = rehearsal(clock)
    r.start()
    await r.speak_queued()
    clock.now = 10
    await to_nova(r, clock, LEFT, "Can you repeat that?")
    await r.speak_queued()
    assert replies(r) == [], "nobody has been asked anything yet"
    r.close()


def test_a_repeat_request_is_for_nova_only_from_someone_just_asked():
    show = first_question()  # Maya's first question, heard at 48 s
    runner = show.runner
    assert runner.asks_again(49, LEFT, "Sorry, can you repeat that?")
    assert not runner.asks_again(49, RIGHT, "Sorry, can you repeat that?"), "Theo wasn't asked"
    assert not runner.asks_again(49, LEFT, "Let me say that again.")
    assert not runner.asks_again(48 + FULL.floor_wait + 0.5, LEFT, "Can you repeat that?")
    legacy = Show(paced=True, conversational=False)
    legacy.until(48)
    assert not legacy.runner.asks_again(49, LEFT, "Can you repeat that?"), "conversational only"


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


# --- The silence clock and Nova's own lines (2026-11-02) ----------------------------------


def _room_question_cued(*, conversational: bool) -> tuple[Show, float, Say]:
    """A paced date that reaches the room's question with the daters talking up to it, and
    Nova not yet done saying it: the cue's time and the cue."""
    from test_show_runner import approved

    show = Show(paced=True, conversational=conversational)
    show.at(10, "queue_question", approved("What's the best meal you've ever had?"))
    talk = [(t, "speech_started", LEFT) for t in range(100, 236, 8)]
    talk += [(t + 6, "speech_stopped", LEFT) for t in range(100, 236, 8)]
    paced_date(show, 236, sorted(talk))
    show.until(260)
    asked = show.says(Moment.FROM_THE_ROOM)
    assert asked, "the room's question was never cued"
    return show, *asked[0]


def test_no_silence_prompt_while_the_room_is_still_hearing_nova_ask():
    # 2026-11-02, the full-date simulation: a room question written live (about 1-2 s) and
    # said (about 6 s) took longer than the 8 s silence clock, so a silence prompt followed
    # it before either dater could answer. The clock now waits for Nova to finish asking.
    for conversational in (True, False):
        show, cued, question = _room_question_cued(conversational=conversational)
        show.until(cued + 12)  # still writing and saying it: no line_done yet
        silences = [t for t, _ in show.says(Moment.SILENCE) if t >= cued]
        assert silences == [], (conversational, silences)
        show.runner.line_done(show.now, question)
        heard = show.now
        show.until(heard + 7)
        silences = [t for t, _ in show.says(Moment.SILENCE) if t >= cued]
        assert silences == [], (conversational, silences)


def test_the_silence_clock_counts_from_the_end_of_novas_own_line():
    # A reply finishing 5 s after the dater stopped: the 8 s of quiet count from its end.
    # Without the conversational floor, which would hold for the unanswered first questions.
    for paced in (False, True):
        show = Show(paced=paced)
        paced_date(show, 89) if paced else show.until(89)
        for start in range(90, 112, 6):  # free talk, nobody quiet for long
            show.at(start, "speech_started", LEFT)
            show.at(start + 4, "speech_stopped", LEFT)
        show.runner.line_done(117, Say(Moment.REPLY, about=LEFT))
        show.until(124.5)
        assert [t for t, _ in show.says(Moment.SILENCE) if t >= 106] == [], paced
        show.until(126)
        assert [t for t, _ in show.says(Moment.SILENCE) if t >= 106] == [125.0], paced


def test_the_gap_between_silence_prompts_counts_from_when_the_room_heard_one():
    # The cooldown used to count from the cue: a prompt said 12 s late was followed 8 s on.
    show = Show()
    show.until(99)
    first = show.says(Moment.SILENCE)
    assert [t for t, _ in first] == [98.0]
    show.runner.line_done(110, first[0][1])  # said late, after a hold or a slow line
    show.until(129.5)
    assert [t for t, _ in show.says(Moment.SILENCE)] == [98.0]
    show.until(130.5)
    assert [t for t, _ in show.says(Moment.SILENCE)] == [98.0, 130.0]
