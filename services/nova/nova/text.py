"""Small text helpers shared by the guardrails, the distress and conduct checks, the on-air
listener and tonight's memory, so each rule is written once."""

import re
from collections.abc import Collection, Iterable, Iterator

# Curly quotes from models and speech-to-text, made straight.
STRAIGHT_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})
# A word: letters first, then letters, digits, apostrophes or hyphens.
WORD = re.compile(r"[^\W\d_][\w'-]*")


def any_of(*patterns: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    """One pattern that matches any of these."""
    return re.compile("|".join(f"(?:{p})" for p in patterns), flags)


def straight_quotes(text: str) -> str:
    return text.translate(STRAIGHT_QUOTES)


def plain(text: str) -> str:
    """Straight quotes, and runs of whitespace as single spaces."""
    return " ".join(straight_quotes(text).split())


def mask_names(text: str, names: Iterable[str], placeholder: str) -> str:
    """Each name as a whole word, longest first, replaced with the placeholder."""
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", placeholder, text)
    return text


def starts_sentence(text: str, index: int) -> bool:
    """Whether the word at index starts a sentence (or the text)."""
    before = text[:index].rstrip(" \"'(")
    return not before or before[-1] in ".?!:…"


def capitalised_mid_sentence(text: str, skip: Collection[str] = ()) -> Iterator[str]:
    """Capitalised words in the middle of a sentence, up to any apostrophe: likely names,
    places, workplaces or schools. A word capitalised only because it starts a sentence is
    not, and nor is any word in skip."""
    for match in WORD.finditer(text):
        base = match.group(0).split("'")[0]
        if base[:1].isupper() and base not in skip and not starts_sentence(text, match.start()):
            yield base
