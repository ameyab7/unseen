"""Seats and blind cards: what Nova knows about the two daters on air."""

from dataclasses import dataclass
from enum import StrEnum


class Seat(StrEnum):
    """Where a dater sits. In stereo, left and right; Nova sits in the center."""

    LEFT = "left"
    RIGHT = "right"

    @property
    def other(self) -> Seat:
        return Seat.RIGHT if self is Seat.LEFT else Seat.LEFT


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
