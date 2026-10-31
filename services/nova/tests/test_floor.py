"""The floor is not an answer-quality judge, and never uses model capacity."""

import pytest

from nova.cards import Seat
from nova.floor import Floor, Move, move, possible_completion

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hmm...", Move.HOLD),
        ("Let me think.", Move.HOLD),
        ("It was a cabin, because", Move.HOLD),
        ("I passed the lighthouse yesterday.", Move.UNKNOWN),
        ("I was thinking about that yesterday. A cabin.", Move.UNKNOWN),
        ("I'll pass.", Move.PASS),
        ("That's all.", Move.PASS),
        ("Maya is a lovely name.", Move.UNKNOWN),
        ("A cabin. Have you tried hiking, Maya?", Move.OTHER),
        ("You go first, Maya.", Move.OTHER),
        ("A lighthouse. What about you?", Move.OTHER),
        ("A lighthouse.", Move.UNKNOWN),
    ],
)
def test_explicit_moves_are_distinct_from_answer_quality(text, expected):
    assert move(text, other_name="Maya") is expected


@pytest.mark.parametrize(
    "text",
    [
        "That's about it for me.",
        "That's pretty much it.",
        "That's all I've got.",
        "that's ABOUT it",
        "A cabin by a lake. That’s about it for me.",  # speech-to-text's curly apostrophe
        "Yeah, that's pretty much it!",
        "So yeah, that's all I have.",
        "That is about it for me",
    ],
)
def test_a_closing_phrase_at_the_end_of_the_utterance_is_a_possible_completion(text):
    assert possible_completion(text)
    assert move(text, other_name="Maya") is Move.UNKNOWN, "a hint, not a move"


@pytest.mark.parametrize(
    "text",
    [
        "That's about it for me, but there's more.",
        "That's about it for me. Then we drove home.",
        "That's pretty much it for the hike, then we went home.",
        "Anyway.",
        "So yeah.",
        "That's about it for me?",
        "That's about it...",  # trailing off
        "That's all.",  # a pass, which the floor already knows
    ],
)
def test_a_closing_phrase_with_more_after_it_or_a_bare_filler_is_not(text):
    assert not possible_completion(text)


def test_a_completion_hint_is_only_a_hint_and_any_new_speech_clears_it():
    floor = Floor()
    floor.heard(10, LEFT, Move.UNKNOWN, completion=True)
    assert floor.completion_hint
    floor.started(10.5, RIGHT)
    assert not floor.completion_hint
    floor.heard(11, LEFT, Move.HOLD, completion=True)
    assert not floor.completion_hint, "never over a hesitation"
    floor.heard(12, LEFT, Move.UNKNOWN, completion=True)
    floor.clear()
    assert not floor.completion_hint


def test_a_question_not_yet_asked_is_owed_no_answer():
    """Nova has cued Maya's question but not said it: Theo talking meanwhile never holds Nova
    back from saying it, although speech not yet heard holds it, as it always does."""
    floor = Floor()
    floor.begin({LEFT})
    floor.started(11, RIGHT)
    floor.heard(12, RIGHT, Move.UNKNOWN)
    assert not floor.waiting(13)
    floor.started(13, RIGHT)
    assert floor.waiting(13.5)
    floor.heard(14, RIGHT, Move.UNKNOWN)
    floor.asked(15)
    assert floor.expected == {LEFT} and floor.waiting(34.9) and not floor.waiting(35)


def test_addressing_nova_can_coexist_with_an_answer():
    assert (
        move("A lighthouse. Nova, does that count?", other_name="Maya", to_nova=True) is Move.NOVA
    )


def question() -> Floor:
    floor = Floor()
    floor.begin({LEFT})
    floor.asked(10)
    return floor


def test_noise_does_not_spend_the_question_opportunity():
    floor = question()
    floor.started(11, LEFT)
    assert floor.expected == {LEFT}
    assert floor.waiting(17)
    assert not floor.waiting(31)


def test_a_handover_waits_for_the_other_dater_then_releases():
    floor = question()
    floor.heard(12, LEFT, Move.OTHER)
    assert floor.expected == set()
    assert floor.handover is RIGHT
    assert floor.waiting(20)
    floor.started(21, RIGHT)
    floor.heard(23, RIGHT, Move.UNKNOWN)
    assert floor.handover is None
    assert not floor.waiting(23)


def test_a_thinking_pause_is_bounded_and_new_speech_invalidates_old_work():
    floor = question()
    floor.heard(12, LEFT, Move.HOLD)
    version = floor.revision
    assert floor.waiting(25)
    floor.started(26, LEFT)
    assert floor.revision > version
    floor.heard(28, LEFT, Move.UNKNOWN)
    assert not floor.waiting(28)


def test_clarification_preserves_the_original_daters_opportunity():
    floor = question()
    floor.heard(12, LEFT, Move.NOVA)
    floor.replied(15, LEFT)
    assert floor.expected == {LEFT}
    assert floor.waiting(25)
    floor.heard(26, LEFT, Move.PASS)
    assert not floor.waiting(26)


def test_the_other_daters_laugh_never_cancels_a_clarification_nova_owes():
    floor = question()
    floor.heard(12, LEFT, Move.NOVA)
    floor.started(13, RIGHT)
    floor.heard(13.5, RIGHT, Move.UNKNOWN)  # "Ha."
    assert floor.clarifying is LEFT
    floor.replied(15, LEFT)
    assert floor.expected == {LEFT}
    assert floor.waiting(34.9) and not floor.waiting(35), "20 s from the answer, not the laugh"


def test_the_dater_who_asked_moving_on_ends_their_clarification():
    floor = question()
    floor.heard(12, LEFT, Move.NOVA)
    floor.started(13, LEFT)
    floor.heard(14, LEFT, Move.UNKNOWN)  # they answered after all
    assert floor.clarifying is None
    floor.replied(16, LEFT)  # a reply nobody needs any more never re-arms the floor
    assert floor.since == 14 and floor.expected == set()
