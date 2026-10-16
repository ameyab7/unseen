"""Nova's memory of tonight: every line it has used, across dates, with names masked."""

from nova.cues import Moment
from nova.tonight import NAME, Tonight, mask, similarity, words


def test_names_are_masked_including_possessives():
    assert mask("Thank you, Maya and Theo. Maya's turn.", ["Maya", "Theo"]) == (
        f"Thank you, {NAME} and {NAME}. {NAME}'s turn."
    )


def test_lines_are_kept_per_moment_with_earlier_daters_names_masked():
    tonight = Tonight()
    tonight.remember(Moment.OUTRO, "Thank you, Maya and Theo.", names=["Maya", "Theo"])
    tonight.remember(Moment.SILENCE, "Maya, Theo, tea or coffee?", names=["Maya", "Theo"])
    assert tonight.said(Moment.OUTRO) == [f"Thank you, {NAME} and {NAME}."]
    assert tonight.said(Moment.LAST_WORD) == []


def test_the_same_line_with_other_names_is_as_close_as_it_gets():
    tonight = Tonight()
    tonight.remember(Moment.OUTRO, "Thank you, Maya and Theo.", names=["Maya", "Theo"])
    names = ["Sam", "Priya"]
    assert tonight.closeness(Moment.OUTRO, "Thank you, Sam and Priya.", names=names) == 1.0
    fresh = "Sam, Priya, what a conversation that was. Thanks to you both."
    assert tonight.closeness(Moment.OUTRO, fresh, names=names) < 0.7
    assert tonight.closeness(Moment.SILENCE, fresh, names=names) == 0.0, "nothing said yet"


def test_only_the_most_recent_lines_are_shown_to_the_model():
    tonight = Tonight()
    for n in range(30):
        tonight.remember(Moment.SILENCE, f"Question number {n}?", names=[])
    said = tonight.said(Moment.SILENCE)
    assert len(said) == 24 and said[-1] == "Question number 29?"


def test_similarity_is_by_words_ignoring_case_and_punctuation():
    assert words("Thank you, Maya!") == ["thank", "you", "maya"]
    assert similarity("Thank you, room.", "thank you room") == 1.0
    assert similarity("Mountains or sea?", "Early bird or night owl?") < 0.5


def test_a_planned_set_piece_is_shown_at_once_and_kept_once_it_is_spoken():
    tonight = Tonight()
    spoken = tonight.plan(Moment.OUTRO, "Thank you, Maya and Theo.", names=["Maya", "Theo"])
    tonight.plan(Moment.LAST_WORD, "Theo, the last word is yours.", names=["Maya", "Theo"])
    assert tonight.said(Moment.OUTRO) == [f"Thank you, {NAME} and {NAME}."], "seen while planned"
    tonight.heard(spoken)
    tonight.drop([spoken])
    assert tonight.said(Moment.OUTRO) == [f"Thank you, {NAME} and {NAME}."]


def test_a_planned_set_piece_the_date_never_spoke_is_dropped_when_it_finishes():
    tonight = Tonight()
    tonight.remember(Moment.OUTRO, "Thank you, Sam and Priya.", names=["Sam", "Priya"])
    unspoken = tonight.plan(Moment.OUTRO, "Thank you, Maya and Theo.", names=["Maya", "Theo"])
    tonight.drop([unspoken])
    assert tonight.said(Moment.OUTRO) == [f"Thank you, {NAME} and {NAME}."], "the heard one stays"
    assert tonight.closeness(Moment.OUTRO, "Thank you, Ben and Tom.", names=["Ben", "Tom"]) == 1.0
