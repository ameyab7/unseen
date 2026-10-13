import pytest

from nova.cards import Seat
from nova.decisions import Choice, DecisionError, InMemoryDecisionDesk, Outcome

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT


def resolved(left: Choice | None, right: Choice | None) -> Outcome:
    desk = InMemoryDecisionDesk()
    desk.open_window()
    for seat, choice in ((LEFT, left), (RIGHT, right)):
        if choice is not None:
            desk.choose(seat, choice)
    desk.close_window()
    return desk.resolve()


def test_mutual_only_when_both_choose_reveal():
    assert resolved(Choice.REVEAL, Choice.REVEAL) is Outcome.MUTUAL


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (Choice.REVEAL, Choice.UNSEEN),
        (Choice.UNSEEN, Choice.UNSEEN),
        (Choice.REVEAL, None),
        (None, Choice.REVEAL),
        (None, None),
    ],
)
def test_anything_else_is_not_a_match_including_no_answer(left, right):
    assert resolved(left, right) is Outcome.NOT_MUTUAL


def test_choices_only_while_the_window_is_open():
    desk = InMemoryDecisionDesk()
    with pytest.raises(DecisionError, match="not open"):
        desk.choose(LEFT, Choice.REVEAL)
    desk.open_window()
    desk.close_window()
    with pytest.raises(DecisionError, match="not open"):
        desk.choose(LEFT, Choice.REVEAL)


def test_each_dater_decides_once():
    desk = InMemoryDecisionDesk()
    desk.open_window()
    desk.choose(LEFT, Choice.UNSEEN)
    with pytest.raises(DecisionError, match="already decided"):
        desk.choose(LEFT, Choice.REVEAL)


def test_no_outcome_until_the_window_closes():
    desk = InMemoryDecisionDesk()
    desk.open_window()
    desk.choose(LEFT, Choice.REVEAL)
    desk.choose(RIGHT, Choice.REVEAL)
    with pytest.raises(DecisionError, match="still open"):
        desk.resolve()
    desk.close_window()
    assert desk.resolve() is Outcome.MUTUAL
