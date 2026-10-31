"""Decision 0012: scheduled transitions in lively conversation, on controlled clocks.

A transition that has waited timing.transition_grace since it first came due goes in a
brief opening; before that, it needs the lull, or the turn gap after a closing phrase.
Synthetic timing only: none of this says how real audio and speech-to-text behave.
"""

import asyncio
import logging
import math
from dataclasses import replace

import pytest
from test_conversation import asked_first, rehearsal
from test_rehearsal import Clock, Plays
from test_show_runner import (
    Show,
    approved,
    audience_question_times,
    first_question_times,
    paced_date,
)

from nova import script
from nova.cards import Seat
from nova.cues import BeatFailed, Moment, Say
from nova.decisions import InMemoryDecisionDesk
from nova.floor import LATE_FINAL, Hold, Move
from nova.rehearsal import Rehearsal, Then, render_report
from nova.show_runner import Deferral, FinalSeen, Phase, Transition, TransitionSeen
from nova.timing import FULL, REHEARSAL

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT
CHAT = "And the market was right by the harbour."


def theos_prompt_due(**timing) -> Show:
    """Maya was asked at 45 s and Nova finished at 48 s; Theo's prompt is due at 51 s."""
    show = Show(
        paced=True,
        conversational=True,
        step=0.1,
        timing=replace(FULL, second_prompt_after=6, **timing),
    )
    show.until(48)
    show.runner.line_done(48, show.says(Moment.FIRST_QUESTION)[0][1])
    return show


def utter(
    show: Show, start: float, end: float, seat: Seat, words: str, *, final=0.2, nova=False
) -> None:
    """A dater speaks from start to end, and speech-to-text's final arrives `final` s after
    the voice detector hears them stop, as it does on LiveKit (Rehearsal.transcript)."""
    show.at(start, "speech_started", seat)
    show.at(end, "speech_stopped", seat)
    show.until(end + final)
    show.runner.turn_words(show.now, seat, words, to_nova=nova)


def watched(show: Show) -> list:
    """Everything the runner tells its diagnostics, from now on."""
    seen: list = []
    show.runner.observe = seen.append
    return seen


def reasons(seen: list, beat: Transition) -> list[Deferral]:
    return [s.reason for s in seen if isinstance(s, TransitionSeen) and s.beat is beat and s.reason]


def chat(show: Show, start: float, end: float, *, talk=2.0, gap=0.5) -> list[tuple[float, float]]:
    """Both daters take turns, each starting `gap` after the other stops, with no closing
    phrase: the lively talk that held the room's questions back 150 s. The gaps."""
    gaps, t, seat = [], start, LEFT
    while t + talk <= end:
        utter(show, t, t + talk, seat, CHAT)
        gaps.append((t + talk, t + talk + gap))
        t, seat = t + talk + gap, seat.other
    return gaps


def room_question_queued() -> Show:
    """A paced conversational date at 3:50, a question from the room due at 4:00."""
    show = Show(paced=True, conversational=True, step=0.1)
    show.at(10, "queue_question", approved("What's the best meal you've ever had?"))
    return paced_date(show, 230)


# --- The opening a due transition waits for --------------------------------------------


def test_lively_talk_with_half_second_gaps_no_longer_holds_the_room_back_for_ever():
    show = room_question_queued()
    gaps = chat(show, 231, 300)
    asked = audience_question_times(show)
    assert len(asked) == 1
    assert asked[0] >= 240 + FULL.transition_grace, "the grace: Nova first waits for a lull"
    start, end = next((s, e) for s, e in gaps if s <= asked[0] < e)
    assert asked[0] - start == pytest.approx(FULL.brief_opening, abs=0.11), "in a brief opening"
    assert asked[0] < end, "before the next dater began: never over anyone"


def test_without_the_grace_the_same_talk_holds_the_room_back_as_before():
    """The failure this decision fixes: half-second gaps never make a 2.5 s lull."""
    show = Show(
        paced=True,
        conversational=True,
        step=0.1,
        timing=replace(FULL, transition_grace=math.inf),
    )
    show.at(10, "queue_question", approved("What's the best meal you've ever had?"))
    paced_date(show, 230)
    chat(show, 231, 300)
    assert audience_question_times(show) == []


def test_a_closing_phrase_lets_a_transition_go_after_the_turn_gap_within_the_grace():
    show = theos_prompt_due()
    utter(show, 49, 52, LEFT, "A cabin by a lake. That's about it for me.")
    assert show.runner.floor.completion_hint
    show.until(53.4)
    assert first_question_times(show) == [45]
    show.until(53.6)
    assert first_question_times(show) == [45, pytest.approx(53.5)], "1.5 s, not 2.5 s"


def test_without_a_closing_phrase_a_transition_within_the_grace_waits_for_the_lull():
    show = theos_prompt_due()
    utter(show, 49, 52, LEFT, "A cabin by a lake.")
    assert not show.runner.floor.completion_hint
    show.until(54.4)
    assert first_question_times(show) == [45]
    show.until(54.6)
    assert first_question_times(show) == [45, pytest.approx(54.5)]


def test_active_speech_holds_an_overdue_transition_however_late_it_is():
    show = theos_prompt_due()
    utter(show, 49, 50, LEFT, "A cabin.")
    show.at(50.5, "speech_started", LEFT)  # and she goes on, 40 s without a break
    show.until(90)
    assert first_question_times(show) == [45], "overdue since 66 s, but Maya is speaking"
    assert show.runner.next_opening(90) is None
    show.at(90, "speech_stopped", LEFT)
    assert show.runner.next_opening(90) == pytest.approx(90.4)
    show.until(90.2)
    show.runner.turn_words(90.2, LEFT, "We went every summer.")
    show.until(90.3)
    assert first_question_times(show) == [45]
    show.until(90.5)
    assert first_question_times(show) == [45, pytest.approx(90.4)]


def test_new_speech_from_either_dater_ends_a_completion_hint():
    show = theos_prompt_due()
    utter(show, 49, 52, LEFT, "A cabin by a lake. That's about it for me.")
    assert show.runner.floor.completion_hint
    show.at(52.6, "speech_started", RIGHT)  # Theo: "Mm."
    assert not show.runner.floor.completion_hint
    show.at(52.9, "speech_stopped", RIGHT)
    show.until(53.1)
    show.runner.turn_words(53.1, RIGHT, "Mm.")
    show.until(55.3)
    assert first_question_times(show) == [45], "the lull after Theo, not the turn gap"
    show.until(55.5)
    assert first_question_times(show) == [45, pytest.approx(55.4)]


@pytest.mark.parametrize("theo_stops_first", [False, True])
def test_a_late_final_never_revives_a_hint_newer_speech_has_ended(theo_stops_first):
    show = theos_prompt_due()
    show.at(49, "speech_started", LEFT)
    show.at(52, "speech_stopped", LEFT)
    show.at(52.3, "speech_started", RIGHT)  # Theo is in before Maya's final arrives
    if theo_stops_first:
        show.at(52.6, "speech_stopped", RIGHT)
    show.until(52.8)
    show.runner.turn_words(52.8, LEFT, "A cabin by a lake. That's about it for me.")
    assert not show.runner.floor.completion_hint


def test_a_thinking_pause_holds_even_an_overdue_transition():
    """Overdue changes only the quiet a transition needs, never a dater's thinking time."""
    show = theos_prompt_due()
    utter(show, 67, 68, LEFT, "Hmm, let me think.")  # overdue since 66 s
    show.until(77)
    assert first_question_times(show) == [45], "nine seconds of quiet, and Maya is thinking"
    utter(show, 77, 80, LEFT, "A cabin by a lake.")
    show.until(80.5)
    assert first_question_times(show) == [45, pytest.approx(80.4)]


def test_retries_and_speech_never_restart_a_transitions_pending_age():
    class FlakyWindow(InMemoryDecisionDesk):
        """open_window() fails the first time, as the database might."""

        failed = False

        def open_window(self) -> None:
            if not self.failed:
                self.failed = True
                raise RuntimeError("the database is down")
            super().open_window()

    show = Show(paced=True, conversational=True, step=0.1)
    show.runner._desk = FlakyWindow()
    paced_date(show, 475)
    utter(show, 478, 482, RIGHT, CHAT)  # holds "That's time", due at 8:00
    assert show.runner._transition == ("time", None)
    due = show.runner._transition_since
    show.until(486)
    assert BeatFailed("time") in show.cues(BeatFailed)
    [(_, time)] = show.says(Moment.TIME)
    assert time.due == due, "the same transition, tried again: still due since 8:00"


def test_a_room_hold_keeps_the_due_time_and_leaves_the_hold_out_of_the_age():
    """A "need a moment" stops the show clock: nobody's conversation held the transition
    back meanwhile, so 40 s of it never make it overdue. Its due time stays 51 s."""
    show = theos_prompt_due()
    show.at(49, "speech_started", LEFT)
    show.at(60, "speech_stopped", LEFT)
    show.at(60, "need_a_moment", LEFT)
    show.at(100, "back", LEFT)
    runner = show.runner
    assert runner._transition_since == 51 and runner._transition_held == pytest.approx(40)
    age_from = runner._age_from(runner._beats[0])
    assert runner.quiet_needed(100, age_from, 2.5) == 2.5, "9 s pending, not 49"
    assert runner.quiet_needed(106.1, age_from, 2.5) == FULL.brief_opening


# --- Pending age and protection are separate ---------------------------------------------


def test_a_clarification_holds_an_overdue_transition_until_it_is_answered():
    show = theos_prompt_due()
    seen = watched(show)
    utter(show, 60, 62, LEFT, "Nova, do you mean ever, or this year?", nova=True)
    show.until(70)
    assert first_question_times(show) == [45], "overdue since 66 s, but Nova owes Maya an answer"
    show.runner.line_done(70, Say(Moment.REPLY, about=LEFT, return_floor=True))
    show.until(74.9)
    assert first_question_times(show) == [45], "a silence doesn't end her turn: an answer does"
    utter(show, 75, 78, LEFT, "This year, then. A cabin.")
    show.until(78.5)
    assert first_question_times(show) == [45, pytest.approx(78.4)], "then a brief opening"
    held = reasons(seen, Transition.SECOND_PROMPT)
    assert Deferral.CLARIFICATION in held and Deferral.THINKING in held
    assert {s.due for s in seen if isinstance(s, TransitionSeen)} == {51}, "never restarted"


def test_a_handover_holds_an_overdue_transition_until_it_is_taken():
    show = theos_prompt_due()
    seen = watched(show)
    utter(show, 60, 62, LEFT, "A cabin. What about you, Theo?")
    show.until(71.9)
    assert first_question_times(show) == [45], "Theo has the floor, though he's quiet"
    utter(show, 72, 75, RIGHT, "A tent, somewhere cold.")
    show.until(75.5)
    assert first_question_times(show) == [45, pytest.approx(75.4)]
    assert Deferral.HANDOVER in reasons(seen, Transition.SECOND_PROMPT)


def test_a_thinking_hold_nobody_ends_expires_on_its_own_bound_not_on_a_silence():
    show = theos_prompt_due()
    utter(show, 67, 68, LEFT, "Hmm, let me think.")
    show.until(88.1)
    assert first_question_times(show) == [45], "twenty seconds of quiet are hers to think"
    show.until(88.5)
    [_, theos] = first_question_times(show)
    assert theos == pytest.approx(68.2 + FULL.floor_wait, abs=0.11), "the floor's own bound"


def test_the_next_opening_ends_the_grace_early_only_when_it_ends_first():
    show = theos_prompt_due()
    utter(show, 49, 55, LEFT, CHAT)
    assert show.runner.next_opening(55.2) == pytest.approx(57.5), "the lull, before 66 s"
    show.at(56, "speech_started", RIGHT)
    show.at(65.5, "speech_stopped", RIGHT)
    assert show.runner.next_opening(65.5) == pytest.approx(66), "the grace ends at 66 s"


# --- Playback: the same opening, checked again before the voice -------------------------


class HoldsTheNextLine(Plays):
    """Once armed, plays the next line until stopped or finished, like a real voice."""

    armed = False

    async def speak(self, text: str) -> float | None:
        if self.armed:
            self.armed = False
            self.held.add(text)
        return await super().speak(text)


async def theos_prompt_goes(clock: Clock, **options) -> Rehearsal:
    """Maya was asked at 45 s and talked from 50 s to 90 s: Theo's prompt, due at 67.5 s, is
    overdue, and goes at 90.4 s, in a brief opening. Queued, not yet spoken."""
    r = await asked_first(clock, **options)
    clock.now = 50
    r.speech_started(LEFT)
    for now in (67.5, 80.0, 90.0):
        clock.now = now
        r.tick()
    r.transcript("A cabin by a lake, every summer.", LEFT)
    r.speech_ended(LEFT)
    clock.now = 90.25
    assert r._until_next_tick(0.25) == pytest.approx(0.15), "the run loop wakes for it"
    clock.now = 90.4
    r.tick()
    return r


def theos_prompts(r: Rehearsal) -> list[Say]:
    return [
        cue
        for cue in r.cues
        if isinstance(cue, Say) and cue.moment is Moment.FIRST_QUESTION and cue.about is RIGHT
    ]


async def test_speech_starting_before_the_voice_defers_the_line_to_the_next_opening():
    clock = Clock()
    r = await theos_prompt_goes(clock)
    assert [say.due for say in theos_prompts(r)] == [67.5]
    said = len(r.speaker.texts)
    r.speech_started(RIGHT)  # between the opening and the voice
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0.06)
    assert len(r.speaker.texts) == said, "never said while Theo speaks"
    clock.now = 93
    r.transcript("I love that.", RIGHT)
    r.speech_ended(RIGHT)
    clock.now = 93.2
    await asyncio.sleep(0.06)
    assert len(r.speaker.texts) == said, "0.2 s isn't the opening"
    clock.now = 93.45
    await asyncio.wait_for(task, 1)
    assert len(r.speaker.texts) == said + 1 and r.talked_over == 0, "said once, in the next"
    assert r._runner._turns_unfinished == 0
    r.close()


async def test_a_yield_keeps_the_pending_age_and_says_the_line_once():
    clock = Clock()
    voice = HoldsTheNextLine(clock)
    r = await theos_prompt_goes(clock, speaker=voice)
    said = len(voice.texts)
    voice.armed = True
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0.01)
    r.speech_started(LEFT)  # Maya cuts in as Nova starts: it stops at once
    await asyncio.sleep(0.01)
    assert voice.stops == 1
    clock.now = 92
    r.transcript("Sorry, go on.", LEFT)
    r.speech_ended(LEFT)
    clock.now = 92.2
    await asyncio.sleep(0.06)
    assert len(voice.texts) == said + 1, "0.2 s isn't the opening"
    clock.now = 92.45
    await asyncio.sleep(0.06)
    assert len(voice.texts) == said + 2, "back in a brief opening, not the 1.5 s turn gap"
    voice.finish()
    await asyncio.wait_for(task, 1)
    assert [y.then for y in r.yields] in ([Then.RESTARTED], [Then.RESUMED])
    assert len(theos_prompts(r)) == 1, "never queued twice"
    played = [line for line in r.lines if line.moment is Moment.FIRST_QUESTION]
    assert len(played) == 2, "Maya's and Theo's, each once"
    assert r._runner._turns_unfinished == 0 and r._runner.floor.posed
    r.close()


async def test_a_line_stopped_while_it_waits_for_its_opening_is_never_said():
    clock = Clock()
    r = await theos_prompt_goes(clock)
    r.speech_started(RIGHT)
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0.06)
    r._queue_cues(r._runner.need_a_moment(clock.now, LEFT))  # Maya asks for a moment
    r.speech_ended(RIGHT)
    clock.now = 100
    await asyncio.wait_for(task, 1)
    assert not r._runner.floor.posed and not r._runner.floor.expected, "nobody owes it"
    assert r.deliveries[-1].heard is None
    r._queue_cues(r._runner.back(clock.now, LEFT))
    clock.now = 110
    r.tick()
    await r.speak_queued()
    played = [line for line in r.lines if line.moment is Moment.FIRST_QUESTION]
    assert len(played) == 1, "only Maya's: Theo's was dropped by the pause"
    r.close()


def test_a_date_that_ends_drops_its_pending_transition():
    show = theos_prompt_due()
    show.at(49, "speech_started", LEFT)
    show.until(60)
    show.at(60, "moderator_end")
    assert show.runner._transition is None
    show.at(70, "speech_stopped", LEFT)
    show.until(100)
    assert first_question_times(show) == [45]


# --- Everything else keeps its timing ----------------------------------------------------


async def test_a_line_that_is_not_a_held_back_transition_still_waits_the_turn_gap():
    clock = Clock()
    r = rehearsal(clock)
    r._queue_cues([Say(Moment.FIRST_QUESTION, about=LEFT)])
    r.speech_started(RIGHT)
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0)
    clock.now = 40
    r.transcript("That's all.", RIGHT)
    r.speech_ended(RIGHT)
    clock.now = 40.45
    await asyncio.sleep(0.06)
    assert not r.speaker.texts, "no brief opening without a transition's due"
    clock.now = 41.55
    await asyncio.wait_for(task, 0.5)
    assert len(r.speaker.texts) == 1
    r.close()


def test_only_conversational_transitions_carry_a_due_and_shorten_their_gap():
    legacy = Show(paced=True)
    paced_date(legacy, 600)
    assert all(say.due is None for _, say in legacy.says())
    show = theos_prompt_due()
    show.runner.floor.completion_hint = True
    assert show.runner.quiet_needed(1000, None, 2.5) == 2.5, "not a transition: unchanged"
    assert REHEARSAL.transition_grace == 15 and REHEARSAL.brief_opening == 0.4, "not scaled"


# --- A question's answer window opens when the room has heard it --------------------------


def mayas_question_queued() -> tuple[Show, Say]:
    """Nova has queued Maya's first question at 45 s, and hasn't said it yet."""
    show = Show(paced=True, conversational=True, step=0.1)
    show.until(45)
    [(_, question)] = show.says(Moment.FIRST_QUESTION)
    return show, question


def test_speech_while_a_question_waits_to_be_said_never_answers_it():
    show, question = mayas_question_queued()
    utter(show, 45.5, 46.5, LEFT, "Sorry, I was saying, the market.")
    show.until(50)
    show.runner.line_done(50, question, heard_from=47, heard_until=50)
    assert show.runner.floor.expected == {LEFT}
    assert show.runner.floor.holds(50.1) is Hold.ANSWER


def test_speech_from_before_the_question_with_a_final_after_it_never_answers_it():
    show, question = mayas_question_queued()
    show.at(45.5, "speech_started", LEFT)
    show.at(46.5, "speech_stopped", LEFT)
    show.until(50)
    show.runner.line_done(50, question, heard_from=47, heard_until=50)
    show.until(50.3)
    show.runner.turn_words(50.3, LEFT, "Sorry, I was saying, the market.")  # a late final
    assert show.runner.floor.expected == {LEFT}, "it arrived after the question, began before"


def test_an_answer_after_the_question_was_said_answers_it():
    show, question = mayas_question_queued()
    show.until(50)
    show.runner.line_done(50, question, heard_from=47, heard_until=50)
    utter(show, 51, 54, LEFT, "A cabin by a lake.")
    assert show.runner.floor.expected == set()


def test_speech_over_the_end_of_the_question_answers_it_and_a_mm_in_its_middle_does_not():
    show, question = mayas_question_queued()
    utter(show, 48, 48.4, LEFT, "Mm.")  # its final comes while Nova is still asking
    show.at(49.5, "speech_started", LEFT)  # over the question's last half-second
    show.until(50)
    show.runner.line_done(50, question, heard_from=47, heard_until=50)
    assert show.runner.floor.expected == {LEFT}, "the mm ended before the question did"
    show.at(53, "speech_stopped", LEFT)
    show.until(53.2)
    show.runner.turn_words(53.2, LEFT, "A cabin by a lake.")
    assert show.runner.floor.expected == set()


def test_an_answer_whose_final_came_before_nova_finished_counts_once_it_is_said():
    show, question = mayas_question_queued()
    show.at(49.5, "speech_started", LEFT)
    show.until(49.9)
    show.runner.turn_words(49.9, LEFT, "A cabin")  # a final while she goes on
    assert show.runner.floor.expected == {LEFT}, "not yet: the question isn't over"
    show.runner.line_done(50, question, heard_from=47, heard_until=50)
    assert show.runner.floor.expected == set()


def test_a_question_never_said_owes_nobody_an_answer():
    show, question = mayas_question_queued()
    show.until(46)
    show.runner.line_done(46, question, said=False)  # a pause dropped it, or it never played
    floor = show.runner.floor
    assert not floor.posed and not floor.expected
    utter(show, 47, 49, LEFT, "A cabin by a lake.")
    assert floor.holds(49.3) is None, "nothing waits for an answer to it"


async def test_an_interruption_before_the_question_is_said_is_not_its_answer():
    clock = Clock()
    voice = HoldsTheNextLine(clock)
    r = await theos_prompt_goes(clock, speaker=voice)
    voice.armed = True
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0.01)
    r.speech_started(RIGHT)  # Theo, as Nova starts asking him: it stops at once
    await asyncio.sleep(0.01)
    clock.now = 92
    r.transcript("Oh, sorry, go ahead.", RIGHT)
    r.speech_ended(RIGHT)
    clock.now = 92.45
    await asyncio.sleep(0.06)  # said again, in the brief opening
    clock.now = 95
    voice.finish()
    await asyncio.wait_for(task, 1)
    floor = r._runner.floor
    assert floor.posed and floor.expected == {RIGHT}, "his 'go ahead' came before the question"
    delivery = r.deliveries[-1]
    assert delivery.heard == (pytest.approx(92.45), pytest.approx(95)) and delivery.yields == 1
    r.speech_started(RIGHT)
    clock.now = 98
    r.transcript("A tent, somewhere cold.", RIGHT)
    assert floor.expected == set(), "the answer after it"
    r.speech_ended(RIGHT)
    r.close()


async def test_a_question_cut_late_with_the_rest_dropped_takes_the_speech_over_it_as_answer():
    """The voice doesn't say which words played, and 2.6 s of the question had: the rest is
    never said (Then.DROPPED). The room heard it up to the cut, and Theo answered over it."""
    clock = Clock()
    voice = HoldsTheNextLine(clock)
    r = await theos_prompt_goes(clock, speaker=voice, yield_hold=0.01)
    voice.armed = True
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0.01)
    clock.now = 93
    r.speech_started(RIGHT)
    await asyncio.sleep(0.05)
    assert voice.stops == 1
    clock.now = 96
    r.transcript("A tent, somewhere cold.", RIGHT)
    r.speech_ended(RIGHT)
    clock.now = 96.5
    await asyncio.wait_for(task, 1)
    assert r.yields[-1].then is Then.DROPPED
    assert r.deliveries[-1].heard == (pytest.approx(90.4), pytest.approx(93))
    assert r._runner.floor.posed and r._runner.floor.expected == set()
    r.close()


async def test_a_question_superseded_after_a_cut_owes_nobody_an_answer():
    clock = Clock()
    voice = HoldsTheNextLine(clock)
    r = await theos_prompt_goes(clock, speaker=voice)
    voice.armed = True
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0.01)
    r.speech_started(LEFT)  # Maya cuts in at once
    await asyncio.sleep(0.01)
    r._queue_cues([Say(Moment.LIGHTNING_UNLOCKED, text=script.LIGHTNING_UNLOCKED)])  # newer
    clock.now = 92
    r.transcript("Wait, before that.", LEFT)
    r.speech_ended(LEFT)
    clock.now = 92.5
    await asyncio.wait_for(task, 1)
    assert r.yields[-1].then is Then.SUPERSEDED and r.deliveries[-1].heard is None
    assert not r._runner.floor.posed and not r._runner.floor.expected
    r.close()


async def test_before_the_voice_a_thinking_hold_still_holds_an_overdue_line():
    clock = Clock()
    r = await theos_prompt_goes(clock)
    said = len(r.speaker.texts)
    r.speech_started(LEFT)
    task = asyncio.create_task(r.speak_queued())
    await asyncio.sleep(0.01)
    clock.now = 91.5
    r.transcript("Hmm, let me think.", LEFT)
    r.speech_ended(LEFT)
    clock.now = 96
    await asyncio.sleep(0.06)
    assert len(r.speaker.texts) == said, "overdue and quiet 4.5 s, but Maya is thinking"
    r.speech_started(LEFT)
    clock.now = 98
    r.transcript("Actually, a cabin.", LEFT)
    r.speech_ended(LEFT)
    clock.now = 98.45
    await asyncio.wait_for(task, 1)
    assert len(r.speaker.texts) == said + 1
    r.close()


# --- Transcript timing -------------------------------------------------------------------


def test_a_closing_phrase_whose_final_came_just_before_the_stop_counts_at_the_stop():
    show = theos_prompt_due()
    show.at(49, "speech_started", LEFT)
    show.until(51.8)
    show.runner.turn_words(51.8, LEFT, "A cabin by a lake. That's about it for me.")
    assert not show.runner.floor.completion_hint, "not while she is still speaking"
    show.at(52.3, "speech_stopped", LEFT)  # after the voice detector's stretch of silence
    assert show.runner.floor.completion_hint
    show.until(53.7)
    assert first_question_times(show) == [45]
    show.until(53.9)
    assert first_question_times(show) == [45, pytest.approx(53.8)], "the turn gap"


def test_a_final_long_before_the_stop_leaves_the_rest_unheard_and_gives_no_hint():
    show = theos_prompt_due()
    show.at(49, "speech_started", LEFT)
    show.until(50.5)
    show.runner.turn_words(50.5, LEFT, "A cabin by a lake. That's about it for me.")
    show.at(52.3, "speech_stopped", LEFT)  # 1.8 s later: she said more after it
    floor = show.runner.floor
    assert not floor.completion_hint and floor.holds(52.4) is Hold.UNHEARD
    show.until(54.7)
    assert first_question_times(show) == [45], "the lull: no hint"
    show.until(54.9)
    assert first_question_times(show) == [45, pytest.approx(54.8)]


def test_newer_speech_before_the_stop_keeps_a_closing_phrase_from_becoming_a_hint():
    show = theos_prompt_due()
    show.at(49, "speech_started", LEFT)
    show.until(51.8)
    show.runner.turn_words(51.8, LEFT, "A cabin by a lake. That's about it for me.")
    show.at(52.0, "speech_started", RIGHT)  # Theo comes in before she's done
    show.at(52.3, "speech_stopped", LEFT)
    assert not show.runner.floor.completion_hint


@pytest.mark.parametrize(
    ("words", "goes"), [("We went every summer.", True), ("Hmm, let me think.", False)]
)
def test_an_outstanding_final_holds_even_a_brief_opening_until_it_says_what_it_was(words, goes):
    show = theos_prompt_due()
    seen = watched(show)
    utter(show, 50, 52, LEFT, "A cabin.")
    show.at(53, "speech_started", LEFT)
    show.at(70, "speech_stopped", LEFT)  # overdue since 66 s; its final not in yet
    show.until(70.8)
    assert first_question_times(show) == [45], "what she just said isn't known yet"
    assert reasons(seen, Transition.SECOND_PROMPT)[-1] is Deferral.UNHEARD
    show.runner.turn_words(70.9, LEFT, words)  # 0.9 s after the stop
    show.until(71.0)
    assert (len(first_question_times(show)) == 2) is goes


def test_speech_no_final_ever_says_holds_an_opening_only_until_late_final():
    """A cough: the voice detector heard it and speech-to-text said nothing. Before, it held
    the floor up to 20 s from when it began; now for LATE_FINAL after it ends."""
    show = theos_prompt_due()
    show.at(49, "speech_started", LEFT)
    show.until(66.8)
    show.runner.turn_words(66.8, LEFT, "A cabin by the lake, every summer.")
    show.at(67, "speech_stopped", LEFT)
    show.at(67.2, "speech_started", RIGHT)  # Theo coughs, inside the brief opening
    show.at(67.5, "speech_stopped", RIGHT)
    show.until(69.4)
    assert first_question_times(show) == [45], "it might have been 'hmm': wait for its final"
    show.until(69.6)
    assert first_question_times(show) == [45, pytest.approx(67.5 + LATE_FINAL)]


def test_lively_talk_whose_finals_lag_every_gap_holds_a_transition_until_the_budget():
    """Each final arrives 0.1 s into the next dater's turn: no opening ever has the latest
    speech known, so the transition is held back on purpose until the overrun budget (60 s
    of waiting) is spent. Then it goes in a brief opening, with a final still to come."""
    show = theos_prompt_due()
    seen = watched(show)
    events, t, seat = [], 52.0, LEFT
    while t < 125:
        events += [(t, "speech_started", seat), (t + 2, "speech_stopped", seat)]
        events.append((t + 2.6, "final", seat))
        t, seat = t + 2.5, seat.other
    for at, what, who in sorted(events, key=lambda event: event[0]):
        if what == "final":
            show.until(at)
            show.runner.turn_words(at, who, CHAT)
        else:
            show.at(at, what, who)
        if len(first_question_times(show)) == 2:
            break
    [_, theos] = first_question_times(show)
    assert theos >= 51 + FULL.conversation_overrun
    assert Deferral.UNHEARD in reasons(seen, Transition.SECOND_PROMPT)


# --- Rehearsal diagnostics: codes and numbers, never words --------------------------------


def test_the_runner_tells_its_diagnostics_a_transitions_life_and_each_final():
    show = theos_prompt_due()
    seen = watched(show)
    utter(show, 49, 70, LEFT, "A cabin by a lake, every summer.")
    show.until(70.5)
    transitions = [s for s in seen if isinstance(s, TransitionSeen)]
    assert [s.phase for s in transitions][0] is Phase.DUE
    went = transitions[-1]
    assert went.phase is Phase.WENT and went.due == 51 and went.quiet == FULL.brief_opening
    assert went.age == pytest.approx(19.4)
    assert reasons(seen, Transition.SECOND_PROMPT) == [Deferral.SPEAKING, Deferral.QUIET]
    [final] = [s for s in seen if isinstance(s, FinalSeen)]
    assert (final.began, final.stopped, final.move) == (49, 70, Move.UNKNOWN)
    assert final.latest and final.answered and not final.hint


async def test_the_rehearsal_logs_and_reports_transitions_finals_and_deliveries(caplog):
    caplog.set_level(logging.INFO, logger="nova")
    clock = Clock()
    r = await theos_prompt_goes(clock)
    await r.speak_queued()
    log = caplog.text
    assert "transition phase=due beat=second_prompt" in log
    assert "transition phase=went beat=second_prompt" in log and "quiet_ms=400" in log
    assert "final seat=left" in log and "answered=True" in log
    assert "delivered moment=first_question about=right said=True" in log
    report = render_report(r)
    assert "  second_prompt: due 67.5, went 90.4 after 22.9 s, quiet 0.4 s; held back by " in report
    assert "Finals: 1" in report
    for words in ("cabin", "summer"):
        assert words not in log and words not in report
    r.close()
