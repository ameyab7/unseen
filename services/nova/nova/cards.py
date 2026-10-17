"""Seats and blind cards: what Nova knows about the two daters on air."""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

# A first name and age are said on air exactly as written, in fixed and fallback lines
# (nova/script.py) that no guardrail checks, so a card holds nothing else there.
FIRST_NAME = re.compile(r"[^\W\d_]+(?:[ '-][^\W\d_]+)*")  # letters; single spaces, - or '
MAX_FIRST_NAME = 30
# The host's names: on audio the room couldn't tell host from dater, and the guardrails
# treat "Nova" as the host. A name is reserved whole ("Ai"), and "Nova" as a first part too
# ("Nova Lee"); "Ai Lin" is fine.
RESERVED_NAMES = frozenset({"nova", "ai"})
# A part of a name in capitals is spelled out by the voice ("MAYA"); two letters are often
# meant to be (AJ, TJ).
MAX_CAPITALS = 2
MIN_AGE, MAX_AGE = 18, 99
PROMPTS = 2  # a card has exactly two
MAX_QUESTION = 60  # the app's fixed prompt questions
MAX_ANSWER = 120  # the prototype's example answer is 45
PRONOUNS = re.compile(r"[^\W\d_]+(?:[ /-]+[^\W\d_]+)*")  # letters, slashes, spaces, hyphens
MAX_PRONOUNS = 20


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
    """pronouns: as the dater wrote them, or empty if they gave none (the prototype makes
    them optional); Nova then uses their first name, never a guessed pronoun.
    approved_for_air: a person at casting approved the card's text, so a fallback may quote
    it (decision 0005, proposed; off until signed off)."""

    first_name: str
    age: int
    pronouns: str
    prompts: tuple[Prompt, Prompt]
    approved_for_air: bool = False

    def __post_init__(self) -> None:
        # Composed, as the model reads it: a decomposed "Zoë" would fail the guardrails.
        object.__setattr__(self, "first_name", unicodedata.normalize("NFC", self.first_name))
        name = self.first_name
        if len(name) > MAX_FIRST_NAME or not FIRST_NAME.fullmatch(name):
            raise InvalidCard(
                f"first_name must be letters, with single spaces, hyphens or apostrophes "
                f"between them, at most {MAX_FIRST_NAME} characters"
            )
        if unicodedata.normalize("NFKC", name) != name:
            raise InvalidCard("first_name must be written in ordinary letters, not lookalikes")
        if name[0].islower():  # names in scripts without capitals are fine
            raise InvalidCard("first_name must start with a capital letter")
        if any(part.isupper() and len(part) > MAX_CAPITALS for part in re.split(r"[ '-]", name)):
            raise InvalidCard("first_name must not be written in capitals")
        first_part = re.split(r"[ '-]", name)[0].lower()
        if name.lower() in RESERVED_NAMES or first_part == "nova":
            raise InvalidCard("first_name can't be the host's name (Nova or AI)")
        if type(self.age) is not int or not MIN_AGE <= self.age <= MAX_AGE:
            raise InvalidCard(f"age must be a whole number from {MIN_AGE} to {MAX_AGE}")
        if self.pronouns and (
            len(self.pronouns) > MAX_PRONOUNS or not PRONOUNS.fullmatch(self.pronouns)
        ):
            raise InvalidCard(
                f"pronouns must be empty, or letters with slashes, spaces or hyphens, at most "
                f"{MAX_PRONOUNS} characters"
            )
        if len(self.prompts) != PROMPTS:
            raise InvalidCard(f"prompts must hold exactly {PROMPTS} prompts")
        for prompt in self.prompts:  # as kept and sent to the model: spaces count
            question, answer = prompt.question, prompt.answer
            if not question.strip() or len(question) > MAX_QUESTION:
                raise InvalidCard(f"a prompt's question must be 1 to {MAX_QUESTION} characters")
            if not answer.strip() or len(answer) > MAX_ANSWER:
                raise InvalidCard(f"a prompt's answer must be 1 to {MAX_ANSWER} characters")

    @property
    def label(self) -> str:
        """How a dater's name is set everywhere: "Maya, 29"."""
        return f"{self.first_name}, {self.age}"


@dataclass(frozen=True)
class Pair:
    left: BlindCard
    right: BlindCard

    def __post_init__(self) -> None:
        # On audio, "Alex and Alex are deciding, privately" means nothing, and nor does
        # "Zoë and Zoe": names that sound the same, whatever their case or accents.
        if _sounds(self.left.first_name) == _sounds(self.right.first_name):
            raise InvalidCard(
                "the two daters on a date need different first names: cast another pair, or "
                "ask one of them for a name to use on air"
            )

    def card(self, seat: Seat) -> BlindCard:
        return self.left if seat is Seat.LEFT else self.right

    @property
    def label(self) -> str:
        return f"{self.left.label} and {self.right.label}"


def _sounds(name: str) -> str:
    """A name as it sounds on audio, near enough: no accents, no case."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
