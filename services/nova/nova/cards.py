"""Seats and blind cards: what Nova knows about the two daters on air."""

import re
from dataclasses import dataclass
from enum import StrEnum

# A first name and age are said on air exactly as written, in fixed and fallback lines
# (nova/script.py) that no guardrail checks, so a card holds nothing else there.
FIRST_NAME = re.compile(r"[^\W\d_]+(?:[ '-][^\W\d_]+)*")  # letters; single spaces, - or '
MAX_FIRST_NAME = 30
MIN_AGE, MAX_AGE = 18, 99


class Seat(StrEnum):
    """Where a dater sits. In stereo, left and right; Nova sits in the center."""

    LEFT = "left"
    RIGHT = "right"

    @property
    def other(self) -> Seat:
        return Seat.RIGHT if self is Seat.LEFT else Seat.LEFT


class InvalidCard(ValueError):
    """A blind card Nova can't put on air. The message names the field, never its value."""


@dataclass(frozen=True)
class Prompt:
    question: str
    answer: str

    @property
    def text(self) -> str:
        return f"{self.question} {self.answer}"


@dataclass(frozen=True)
class BlindCard:
    first_name: str
    age: int
    pronouns: str
    prompts: tuple[Prompt, Prompt]

    def __post_init__(self) -> None:
        name = self.first_name
        if len(name) > MAX_FIRST_NAME or not FIRST_NAME.fullmatch(name):
            raise InvalidCard(
                f"first_name must be letters, with single spaces, hyphens or apostrophes "
                f"between them, at most {MAX_FIRST_NAME} characters"
            )
        if type(self.age) is not int or not MIN_AGE <= self.age <= MAX_AGE:
            raise InvalidCard(f"age must be a whole number from {MIN_AGE} to {MAX_AGE}")

    @property
    def label(self) -> str:
        """How a dater's name is set everywhere: "Maya, 29"."""
        return f"{self.first_name}, {self.age}"


@dataclass(frozen=True)
class Pair:
    left: BlindCard
    right: BlindCard

    def card(self, seat: Seat) -> BlindCard:
        return self.left if seat is Seat.LEFT else self.right

    @property
    def label(self) -> str:
        return f"{self.left.label} and {self.right.label}"
