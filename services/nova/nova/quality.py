"""Whether a line does its job when heard once, beyond the guardrails (decision 0005).

The guardrails (nova/guardrails.py) are the hard gate: safety, privacy and format, and a
line that breaks one is never said. These checks are softer and about the line itself: is
a first question built on the dater's own answer and asked directly, is a lightning
question the form it was asked for, does a line about the show make nothing up, is it short
enough to follow. The line writer uses them to choose among a set piece's candidates
(best), and the evaluation reports them for every model line (form_problems), so what the
evaluation measures is what chooses.

They are deterministic word patterns, so they can't hear tone or meaning: a fault here is a
likely problem, never proof, and a line with none can still be dull. Their limits are why
they rank candidates rather than reject them, except for the two faults that make a line
untrue or break the running order (SEVERE).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from nova import prompts
from nova.cards import Pair
from nova.cues import Moment, Say
from nova.tonight import NAME, words


class Fault(StrEnum):
    # Severe: the line says something untrue about the show, or asks a dater a question
    # nobody will wait for. A candidate with one is never chosen.
    INVENTS_SHOW_FACT = "invents a show fact"
    ASKS_TOO_SOON = "asks a dater too soon"
    # Soft: the line does its job less well. Fewer is better.
    ASKS_PERMISSION = "asks permission first"
    DOUBTS = "doubts the dater"
    NOT_BUILT_ON_CARD = "not built on the dater's prompt"
    NOT_EITHER_OR = "not an either/or"
    NO_ELLIPSIS = "no ellipsis to finish"
    NOT_A_QUESTION = "not a question"
    NOT_BOTH_NAMED = "doesn't name both daters"
    TOO_MANY_QUESTIONS = "three or more questions"
    TOO_LONG = "longer than its moment"
    EMPTY_PRAISE = "empty praise"


SEVERE = frozenset({Fault.INVENTS_SHOW_FACT, Fault.ASKS_TOO_SOON})

EITHER_OR, FINISH_THE_SENTENCE, ONE_WORD = prompts.LIGHTNING_FORMS
# Words too common to show a first question used the dater's own prompt.
COMMON_WORDS = frozenset((
    "about", "your", "with", "that", "this", "from", "have", "what", "when", "where",
    "which", "their", "there", "they", "them", "then", "than", "just", "like", "into",
    "over", "some", "more", "most", "very", "ask", "always", "never", "ever", "first",
    "good", "best", "thing", "time", "long", "once", "after",
))  # fmt: skip
# Endings dropped so a word and its forms match ("juggle", "juggling"; "swim", "swims"):
# a light stem, not a dictionary, so it never needs a dependency.
ENDINGS = ("ing", "ed", "es", "er", "s", "e")

# Show facts no task gives the model, in the moments that talk about the show itself: a
# break, a studio, coming back after something. Not lightning or silence questions, where
# music or lights can come from a card.
SHOW_FICTION = re.compile(
    r"\b(?:(?:a|the|our|this|quick|short)\s+break|be right back|back after this|studio"
    r"|on stage|spotlight|before we move on)\b",
    re.IGNORECASE,
)
SHOW_MOMENTS = frozenset(
    (Moment.DOORS_INTRO, Moment.LAST_WORD, Moment.ROOM_TALK, Moment.OUTRO, Moment.NEXT_UP)
)
# A first question that asks leave to ask, or doubts the dater, instead of asking them.
PERMISSION = re.compile(
    r"\b(?:should|shall|can|could|may)\s+(?!you\b)\S+\s+(?:be\s+)?(?:ask(?:ing)?|start)\b"
    r"|\b(?:do|would)\s+you\s+(?:want|like|mind)\s+(?:us|me|\S+)\s+to\s+(?:ask|start)\b",
    re.IGNORECASE,
)
# A question to someone by name ("Sam, are you ready?") in an introduction, where nobody
# has the floor yet: the first questions come after the doors, and the next pair isn't on.
ASKS_BY_NAME = re.compile(r"(?:^|[.?]\s+)(?!Room\b|Everyone\b)[A-Z][\w'-]+,[^.?]*\?")
INTRODUCTIONS = frozenset((Moment.DOORS_INTRO, Moment.NEXT_UP))
DOUBT = re.compile(
    r"\b(?:do|did|are|can)\s+you\s+really\b|\bis\s+it\s+(?:really\s+)?true\b"
    r"|\b(?:the|how much) truth\b",
    re.IGNORECASE,
)
# Praise that says nothing about what was said (the brand book's voice is specific).
EMPTY_PRAISE = re.compile(
    r"\b(?:that's|that is|how|so)\s+(?:interesting|fascinating)\b|\bgreat (?:answer|question)\b",
    re.IGNORECASE,
)
# The word counts the prompts ask for, where they ask (nova/prompts.py).
WORDS_FOR = {Moment.FIRST_QUESTION: 20, Moment.DOORS_INTRO: 40}
# A first question or a last word with three or more questions asks too much at once.
QUESTIONS_FOR = frozenset((Moment.FIRST_QUESTION, Moment.LAST_WORD))


def _stem(word: str) -> str:
    for ending in ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            word = word[: -len(ending)]
            break
    if len(word) > 4 and word[-1] == word[-2]:  # "swimm" for "swimming"
        word = word[:-1]
    return word


def content_words(text: str) -> set[str]:
    """Words longer than three letters, not common ones, and not contractions ("what's"),
    each lightly stemmed."""
    found = re.findall(r"[a-z']+", text.lower())
    return {_stem(word) for word in found if len(word) > 3 and "'" not in word} - {
        _stem(word) for word in COMMON_WORDS
    }


def names_word(text: str, name: str) -> bool:
    """The name as a whole word: "Tomorrow" doesn't name Tom, nor "Hopefully" Hope."""
    return re.search(rf"\b{re.escape(name)}\b", text) is not None


def opening(text: str) -> list[str]:
    """The first three words, without masked names: every line to both daters opens with
    their names, as the rules ask, so the names say nothing about repetition."""
    return words(text.replace(NAME, " "))[:3]


def faults(say: Say, text: str, *, pair: Pair, form: str | None = None) -> tuple[Fault, ...]:
    """What a model line misses of its moment's job, in Fault order."""
    found: set[Fault] = set()
    stripped = text.rstrip()
    if say.moment in SHOW_MOMENTS and SHOW_FICTION.search(text):
        found.add(Fault.INVENTS_SHOW_FACT)
    if say.moment in INTRODUCTIONS and ASKS_BY_NAME.search(text):
        found.add(Fault.ASKS_TOO_SOON)
    if say.moment is Moment.FIRST_QUESTION:
        if PERMISSION.search(text):
            found.add(Fault.ASKS_PERMISSION)
        if DOUBT.search(text):
            found.add(Fault.DOUBTS)
    if EMPTY_PRAISE.search(text):
        found.add(Fault.EMPTY_PRAISE)
    limit = WORDS_FOR.get(say.moment)
    if limit is not None and len(text.split()) > limit:
        found.add(Fault.TOO_LONG)
    if say.moment in QUESTIONS_FOR and text.count("?") >= 3:
        found.add(Fault.TOO_MANY_QUESTIONS)
    match say.moment:
        case Moment.LIGHTNING_QUESTION if form == EITHER_OR and " or " not in text:
            found.add(Fault.NOT_EITHER_OR)
        case Moment.LIGHTNING_QUESTION if form == FINISH_THE_SENTENCE and not stripped.endswith(
            ("...", "…")
        ):
            found.add(Fault.NO_ELLIPSIS)
        case Moment.LIGHTNING_QUESTION if form == ONE_WORD and not stripped.endswith("?"):
            found.add(Fault.NOT_A_QUESTION)
        case Moment.FIRST_QUESTION if say.about is not None:
            # The dater's own answer: the app's fixed question words don't count.
            answer = pair.card(say.about).prompts[0].answer
            if not content_words(answer) & content_words(text):
                found.add(Fault.NOT_BUILT_ON_CARD)
        case Moment.OUTRO | Moment.DOORS_INTRO:
            if not all(names_word(text, card.first_name) for card in (pair.left, pair.right)):
                found.add(Fault.NOT_BOTH_NAMED)
    return tuple(fault for fault in Fault if fault in found)


def form_problems(say: Say, text: str, *, pair: Pair, form: str | None = None) -> list[str]:
    """The faults as the evaluation reports them."""
    return [str(fault) for fault in faults(say, text, pair=pair, form=form)]


# --- Choosing among a set piece's candidates ---------------------------------------------

# A candidate that opens with the same three words as a line said tonight for its moment
# counts this much closer to tonight's lines: enough to break a near tie towards a fresh
# opening, never enough to override a clearly closer line (closeness runs from 0 to 1).
SAME_OPENING = 0.3


@dataclass(frozen=True)
class Scored:
    """One candidate as the line writer sees it. ok: it passed the guardrails. closeness:
    to the most similar line said tonight for its moment (Tonight.closeness)."""

    ok: bool
    faults: tuple[Fault, ...] = ()
    closeness: float = 0.0
    same_opening: bool = False

    @property
    def usable(self) -> bool:
        return self.ok and not SEVERE & set(self.faults)

    @property
    def repetition(self) -> float:
        return self.closeness + (SAME_OPENING if self.same_opening else 0.0)


def best(scored: Sequence[Scored]) -> int | None:
    """The candidate to keep, in tiers, so novelty can never rescue a worse line: only one
    that passed the guardrails and has no severe fault; then the fewest soft faults; then
    the least repetition; then the first written. None: none qualifies, so the approved
    fallback is said."""
    usable = [k for k, s in enumerate(scored) if s.usable]
    return min(
        usable,
        key=lambda k: (len(scored[k].faults), round(scored[k].repetition, 6), k),
        default=None,
    )


def least_similar(scored: Sequence[Scored]) -> int | None:
    """The rule before decision 0005's quality ranking: of the candidates that passed the
    guardrails, the least like tonight's lines. Kept for the evaluation to compare."""
    passing = [k for k, s in enumerate(scored) if s.ok]
    return min(passing, key=lambda k: (scored[k].closeness, k), default=None)
