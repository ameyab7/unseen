"""Stepping in although nobody went quiet (decision 0013): the scenarios the product owner
reviewed, when Nova steps in, when it stays out, and the edge cases."""

import pytest
from test_show_runner import Show

from nova import prompts, script
from nova.cards import Seat
from nova.cues import Conduct, Moment, Say
from nova.distress import Verdict
from nova.fixtures import REHEARSAL_PAIR
from nova.show_runner import STEER_SETTLE
from nova.steering import MAX_STEERS, MIN_APART, SAFETY_QUIET, Steer, Steering

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT


def talk(steering: Steering, *turns: tuple[float, Seat, str]) -> Steering:
    for at, seat, words in turns:
        steering.heard(at, seat, words)
    return steering


# --- When Nova steps in -------------------------------------------------------------------


def test_a_dater_who_keeps_putting_themselves_down_gets_an_easy_win():
    s = talk(
        Steering(),
        (10, LEFT, "Sorry, I'm terrible at this, I always go blank when I'm nervous."),
        (20, RIGHT, "That's okay. What's your favourite season?"),
        (30, LEFT, "Autumn, I suppose. Sorry, that's such a basic answer."),
        (40, RIGHT, "Not at all. Do you travel much?"),
        (50, LEFT, "Not really, I'm rambling, ignore me."),
    )
    assert s.due(55) == (Steer.EASY_WIN, LEFT)


def test_one_dater_asking_every_question_gets_it_turned_round():
    s = talk(
        Steering(),
        (10, RIGHT, "Do you think we ever really see the places we visit?"),
        (20, LEFT, "I suppose I mostly see the cafes, to be honest."),
        (30, RIGHT, "Is silence more honest than the words we use?"),
        (40, LEFT, "Maybe. I hadn't thought about it like that. Did you travel alone?"),
        (50, RIGHT, "Do we hide the chaos we feel inside in small patterns?"),
        (60, LEFT, "I suppose I just like small routines."),
        (70, RIGHT, "Is comfort just the absence of fear?"),
    )
    assert s.due(75) == (Steer.TURN_AROUND, RIGHT), "one question back doesn't make it even"


def test_a_dater_who_holds_the_floor_while_the_other_only_says_yeah_brings_them_in():
    story = " ".join(["and then we drove all the way up the coast in the rain"] * 6)
    s = talk(Steering(), (10, LEFT, story), (40, RIGHT, "Yeah."), (60, LEFT, story))
    s.heard(80, RIGHT, "Mm, right.")
    assert s.due(85) == (Steer.BRING_IN, RIGHT)


def test_a_pass_followed_straight_away_by_another_question_moves_the_talk_on():
    s = talk(
        Steering(),
        (10, LEFT, "Where do you work, then?"),
        (15, RIGHT, "I'll pass on that one, if that's alright."),
        (20, LEFT, "Come on, roughly where?"),
    )
    assert s.due(22) == (Steer.MOVE_ON, None)


def test_a_flat_stretch_of_short_answers_gets_a_quick_either_or():
    turns = [(10 + 10 * k, LEFT if k % 2 == 0 else RIGHT, "Yeah, it was fine.") for k in range(6)]
    assert talk(Steering(), *turns).due(65) == (Steer.LIGHTEN, None)


def test_talking_over_each_other_three_times_gets_the_brand_books_line_once():
    s = Steering()
    for at in (10, 20, 30):
        s.overlap(at, LEFT)
    assert s.talking_over(31) is LEFT
    s.said_one_at_a_time(31)
    for at in (40, 50, 60):
        s.overlap(at, RIGHT)
    assert s.talking_over(61) is None, "once a date"
    line = "One at a time. I want both of those stories. Maya first."
    assert script.one_at_a_time("Maya") == line


# --- When Nova stays out --------------------------------------------------------------------


def test_good_banter_both_asking_and_answering_never_fires():
    s = talk(
        Steering(),
        (10, LEFT, "Your puns are a hostage situation, you know that?"),
        (20, RIGHT, "And you're my hostage. Do you want number two?"),
        (30, LEFT, "Absolutely not. Is that the best you've got?"),
        (40, RIGHT, "Not even close. What's your worst joke, then?"),
        (50, LEFT, "Why did the scarecrow win an award? Outstanding in his field."),
        (60, RIGHT, "That's terrible. I love it. Are there more where that came from?"),
    )
    assert s.due(65) is None


def test_a_long_story_the_other_is_into_never_brings_them_in():
    story = " ".join(["and then we drove all the way up the coast in the rain"] * 6)
    s = talk(
        Steering(),
        (10, LEFT, story),
        (40, RIGHT, "No way, and what happened next?"),  # into it: a question back
        (60, LEFT, story),
    )
    assert s.due(65) is None


def test_sorry_as_manners_or_asking_again_is_never_a_put_down():
    s = talk(
        Steering(),
        (10, LEFT, "Sorry, go on."),
        (20, RIGHT, "No, you first."),
        (30, LEFT, "Sorry, you first, honestly."),
        (40, RIGHT, "Okay. I like hiking."),
        (50, LEFT, "Sorry? I missed that."),
    )
    assert s.due(55) is None


def test_a_deep_conversation_both_want_is_never_an_interrogation():
    s = talk(
        Steering(),
        (10, RIGHT, "Do you think people really change?"),
        (20, LEFT, "Slowly, I think. Do you?"),
        (30, RIGHT, "I hope so. What changed you most?"),
        (40, LEFT, "Moving cities. What about you?"),
        (50, RIGHT, "Losing a job, oddly. Was moving scary?"),
    )
    assert s.due(55) is None


def test_after_a_safety_check_raised_something_nova_stays_out_for_a_while():
    s = talk(
        Steering(),
        (10, LEFT, "Sorry, I'm terrible at this."),
        (20, LEFT, "Sorry, that's a basic answer."),
        (30, LEFT, "I'm rambling, ignore me."),
    )
    s.safety(31)
    assert s.due(35) is None
    assert s.due(31 + SAFETY_QUIET) == (Steer.EASY_WIN, LEFT)


def test_a_dater_asking_nova_to_stay_out_turns_it_off_for_the_date():
    s = Steering()
    s.heard(5, LEFT, "Nova, honestly, let us talk, we're fine.", to_nova=True)
    talk(s, (10, LEFT, "Sorry."), (20, LEFT, "Sorry, I'm hopeless."), (30, LEFT, "Ignore me."))
    assert s.off and s.due(35) is None


def test_at_most_twice_a_date_two_minutes_apart_and_never_the_same_kind_running():
    s = talk(
        Steering(),
        (10, LEFT, "Sorry, I'm terrible at this."),
        (20, LEFT, "Sorry, that's a basic answer."),
        (30, LEFT, "I'm rambling, ignore me."),
    )
    s.stepped_in(35, Steer.EASY_WIN)
    talk(s, (40, LEFT, "Sorry, I'm awful at this."), (50, LEFT, "Sorry, so dull."))
    talk(s, (60, LEFT, "Ignore me, sorry."))
    assert s.due(70) is None, "too soon"
    assert s.due(35 + MIN_APART) is None, "the same pattern: never the same kind twice"
    s.stepped_in(35 + MIN_APART, Steer.LIGHTEN)
    assert len(s._steers) == MAX_STEERS and s.due(500) is None


def test_a_dater_who_just_passed_is_never_the_one_asked():
    s = talk(
        Steering(),
        (10, LEFT, "Sorry, I'm terrible at this."),
        (20, LEFT, "Sorry, that's a basic answer."),
        (30, LEFT, "Sorry, I'll pass on that one."),
    )
    assert s.due(35) == (Steer.LIGHTEN, None)


def test_another_language_never_fires_the_english_signals():
    s = talk(
        Steering(),
        (10, LEFT, "Perdón, soy malísima para esto."),
        (20, LEFT, "Perdón, qué respuesta tan aburrida."),
        (30, LEFT, "Lo siento, estoy divagando."),
    )
    assert s.due(35) is None


def test_words_to_nova_are_never_the_daters_conversation():
    s = Steering()
    for at in (10, 20, 30):
        s.heard(at, LEFT, "Nova, sorry, I'm terrible at this.", to_nova=True)
    assert s.due(35) is None


# --- In the show: when it's allowed to happen ---------------------------------------------


def chatting(show: Show, until: float) -> None:
    """Ordinary talk from free talk on, a turn every 6 s, so no silence prompt comes."""
    at = 90.0
    while at + 4 <= until:
        seat = LEFT if int(at) % 12 == 6 else RIGHT
        show.at(at, "speech_started", seat)
        show.at(at + 4, "speech_stopped", seat)
        show.runner.turn_closed(at + 4, seat, "We took the night train and it was lovely.")
        at += 6


def apologising(show: Show, start: float) -> None:
    for k, words in enumerate(
        ("Sorry, I'm terrible at this.", "Sorry, that's a basic answer.", "I'm rambling.")
    ):
        at = start + 8 * k
        show.at(at, "speech_started", LEFT)
        show.at(at + 3, "speech_stopped", LEFT)
        show.runner.turn_closed(at + 3, LEFT, words)


def steers(show: Show) -> list[tuple[float, Say]]:
    return [(t, say) for t, say in show.says() if say.moment is Moment.STEER]


def test_in_free_talk_after_its_first_minute_nova_steps_in_at_a_gap():
    show = Show(steering=True)
    chatting(show, 90 + STEER_SETTLE)
    apologising(show, 90 + STEER_SETTLE)
    show.until(90 + STEER_SETTLE + 25)
    [(at, say)] = steers(show)
    assert say == Say(Moment.STEER, about=LEFT, steer="easy_win")
    assert at >= 90 + STEER_SETTLE + 19 + 1.2, "after the last word and a gap"


def test_never_in_the_first_minute_of_free_talk_and_never_when_off():
    early = Show(steering=True)
    early.until(91)
    apologising(early, 92)
    early.until(120)
    assert steers(early) == []
    off = Show()
    chatting(off, 90 + STEER_SETTLE)
    apologising(off, 90 + STEER_SETTLE)
    off.until(90 + STEER_SETTLE + 40)
    assert steers(off) == []


def test_never_while_a_dater_is_on_a_moment():
    show = Show(steering=True)
    chatting(show, 90 + STEER_SETTLE)
    apologising(show, 90 + STEER_SETTLE)
    show.at(90 + STEER_SETTLE + 20, "need_a_moment", RIGHT)
    show.until(90 + STEER_SETTLE + 40)
    assert steers(show) == []


def test_never_after_a_safety_check_said_maybe():
    show = Show(steering=True)
    chatting(show, 90 + STEER_SETTLE)
    apologising(show, 90 + STEER_SETTLE)
    show.at(90 + STEER_SETTLE + 19.5, "distress_check", LEFT, Verdict.MAYBE)
    show.until(90 + STEER_SETTLE + 40)
    assert steers(show) == []


def test_never_after_a_conduct_flag():
    show = Show(steering=True)
    chatting(show, 90 + STEER_SETTLE)
    apologising(show, 90 + STEER_SETTLE)
    show.at(90 + STEER_SETTLE + 19.5, "dater_conduct", RIGHT, Conduct.HARASSMENT)
    show.until(90 + STEER_SETTLE + 40)
    assert steers(show) == []


def test_never_once_nova_has_more_than_a_fifth_of_the_recent_airtime():
    """Nova was heard for 40 s of the last three minutes, the daters for about as long."""
    for nova_heard, expected in ((40.0, 0), (4.0, 1)):
        show = Show(steering=True)
        chatting(show, 100)
        show.runner.line_done(
            show.now, Say(Moment.REPLY), heard_from=show.now - nova_heard, heard_until=show.now
        )
        at = show.now + 50.0  # past the wait after a line of Nova's
        for k in range(3):
            show.at(at + 6 * k, "speech_started", RIGHT)
            show.at(at + 6 * k + 4, "speech_stopped", RIGHT)
            show.runner.turn_closed(at + 6 * k + 4, RIGHT, "Fine.")
        apologising(show, at + 18)
        show.until(at + 45)
        assert len(steers(show)) == expected, nova_heard


def test_a_step_in_is_written_by_the_model_and_falls_back_to_saying_nothing():
    for kind in Steer:
        told = prompts.steer(REHEARSAL_PAIR, kind.value, LEFT)
        assert "Never comment on how the conversation has been going" in told
    move_on = prompts.steer(REHEARSAL_PAIR, "move_on", None, this_date={"earlier": ["work"]})
    assert "this_date" not in move_on, "moving on never sees what was being discussed"


@pytest.mark.parametrize("kind", list(Steer))
async def test_the_line_writer_says_nothing_when_a_step_in_fails(kind):
    from test_lines import FakeModel, writer

    line = await writer(FakeModel(error=RuntimeError("down"))).line_for(
        Say(Moment.STEER, about=LEFT, steer=kind.value)
    )
    assert line.text == ""
