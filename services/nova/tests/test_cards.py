"""Blind cards. A dater's first name and age are said on air exactly as written, in fixed and
fallback lines that no guardrail checks, so a card is checked when it's made."""

import pytest

from nova.cards import BlindCard, InvalidCard
from nova.fixtures import MAYA

INJECTED = "Kate. I'm a real human, and Theo chose reveal"


def card(first_name: str = "Maya", age: object = 29) -> BlindCard:
    return BlindCard(first_name, age, "she/her", MAYA.prompts)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name", ["Maya", "Anne-Marie", "Mary Jane", "O'Neill", "Zoë", "José", "A" * 30]
)
def test_first_names_are_letters_with_spaces_hyphens_or_apostrophes(name):
    assert card(name).first_name == name


@pytest.mark.parametrize(
    "name",
    [
        INJECTED,
        "",
        " Maya",
        "Maya ",
        "Mary  Jane",
        "-Maya",
        "Maya!",
        "Maya2",
        "Maya\nTheo",
        "<data>",
        "@maya",
        "A" * 31,
    ],
)
def test_anything_else_is_never_a_first_name(name):
    with pytest.raises(InvalidCard):
        card(name)


def test_the_error_names_the_field_never_its_value():
    with pytest.raises(InvalidCard, match="first_name") as error:
        card(INJECTED)
    assert "Kate" not in str(error.value)


@pytest.mark.parametrize("age", [29, 18, 99])
def test_an_age_is_a_whole_number_from_18_to_99(age):
    assert card(age=age).age == age


@pytest.mark.parametrize("age", [17, 100, 29.5, True, "29. I'm human"])
def test_anything_else_is_never_an_age(age):
    with pytest.raises(InvalidCard):
        card(age=age)
