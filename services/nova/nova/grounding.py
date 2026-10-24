"""Common ground Nova may say the daters share (decision 0005). "You both...", "both of
you...", "you two...", or that they have something in common, has to come from each dater's
own words: their blind card, or what they said on this date. What a dater says about the
other ("she loves it too", "tell everyone we both love skydiving") is only their own
evidence, never the other's. Missing or unclear: the claim is unsupported, the line is
rejected, and the moment's approved fallback is said instead; the prompt asks Nova to ask an
open question or leave the claim out.

A narrow word check, a regression guard for one failure ("since you both find such a
meditative quality in your crafts", when only one of them had said meditative), never a
judge of tone or chemistry. It errs on the safe side: a claim in Nova's own words that
neither dater used ("crafts" for pottery and spoon carving) is unsupported too.
"""

import re
from collections.abc import Iterable, Mapping

from nova.quality import COMMON_WORDS

_BOTH = (
    r"(?:you both|both of you|you(?:'re| are|'ve| have|'d| would) both|you two|the two of you"
    r"|you each)"
)
# Where a line says the daters share something.
CLAIM = re.compile(
    rf"\b{_BOTH}\b"
    r"|\byou (?:share|have (?:\w+ ){0,2}in common)\b"
    r"|\b(?:you(?:'re| are)|you seem|you sound) (?:so |really |quite )?alike\b"
    r"|\bthe same (?:thing|hobby|hobbies|passion|interest|interests|love)\b",
    re.IGNORECASE,
)
# The words just before "you both" that make it a question to both of them ("what do you
# both think?"), which claims nothing...
ASKING = re.compile(
    r"\b(?:do|did|does|would|could|can|will|should|have|are|were|how|what|which|where|why"
    r"|who|when|if)\s+(?:\w+\s+)?$",
    re.IGNORECASE,
)
# ...unless the question rests on a claim ("since you both love it, what's your favourite?").
RESTING = re.compile(r"\b(?:since|because|as|given|now that|seeing as)\b", re.IGNORECASE)
# "You both" as someone spoken to, not something said about them: "thank you both", "over
# to you two", "good to have you both".
ADDRESSED = re.compile(
    r"\b(?:thank|thanks|to|with|for|from|of|at|hear|see|have|having|ask|let|tell|give|meet"
    r"|between)\s+$",
    re.IGNORECASE,
)
# With no fact in it, "you both..." claims something only if it says they're alike: "you
# two have so much in common" does, "you both with us tonight" doesn't.
LIKENESS = re.compile(r"\b(?:common|share|shared|alike|same|kindred|similar)\b", re.IGNORECASE)
# A clause that says how they differ claims nothing shared ("you both have such different
# mornings"); whether it makes too much of it is the evaluation's narrow word check.
DIFFERENCE = re.compile(r"\b(?:different|differently|differ|unlike|contrast)\b", re.IGNORECASE)
CLAUSE_END = re.compile(r"[.,;:?!—–]|\s-\s")
# Words that carry no fact of their own in a claim: what it says they share is the rest.
GENERIC = frozenset((
    "both", "share", "shared", "common", "same", "alike", "together", "also", "love", "loves",
    "like", "likes", "enjoy", "enjoys", "find", "finds", "seem", "seems", "sound", "sounds",
    "really", "such", "quite", "thing", "things", "kind", "sort", "much", "little", "bit",
    "lot", "lots", "each", "other", "something", "everything", "anything", "having", "been",
    "getting", "make", "makes", "making", "quality", "sense", "real", "whole", "pretty",
    # Verbs that only carry the claim ("you've both had it come out flat").
    "come", "came", "comes", "coming", "went", "goes", "going", "gone", "said", "says",
    "made", "take", "took", "taken", "give", "gave", "keep", "kept", "feel", "feels", "felt",
    "know", "knew", "think", "thought", "want", "wants", "need", "needs", "seen", "done",
))  # fmt: skip
SUFFIXES = ("ingly", "edly", "ings", "ing", "ies", "ied", "ed", "es", "er", "s", "ly", "y", "e")


def root(word: str) -> str:
    """A word without its ending, so "baking" meets "bake" and "hobbies" meets "hobby"."""
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if len(word) > 3 and word[-1] == word[-2]:  # "runn" for "running"
        word = word[:-1]
    return word


def facts(text: str) -> set[str]:
    """The words in some text that could carry a fact: longer than three letters, not
    common, not generic, by root."""
    found = re.findall(r"[a-z']+", text.lower())
    return {
        root(word)
        for word in found
        if len(word) > 3 and "'" not in word and word not in COMMON_WORDS and word not in GENERIC
    }


def claims(line: str) -> list[str]:
    """What the line says the daters share, one clause for each claim: from the claim to
    the end of its clause. A question that only asks them both is no claim."""
    found = []
    for match in CLAIM.finditer(line):
        start = max((m.end() for m in CLAUSE_END.finditer(line, 0, match.start())), default=0)
        before = line[start : match.start()]
        if ASKING.search(before) and not RESTING.search(before):
            continue
        rest = line[match.end() :]
        end = CLAUSE_END.search(rest)
        said = rest[: end.start()] if end else rest
        if match.group(0).lower().startswith("you") and (
            ADDRESSED.search(before)
            or not said.strip()
            or not (facts(said) or LIKENESS.search(said))
        ):
            continue  # spoken to them, or nothing said about them
        if DIFFERENCE.search(said):
            continue  # how they differ, not what they share
        found.append((match.group(0) + said).strip())
    return found


def unsupported(line: str, evidence: Mapping[str, Iterable[str]]) -> list[str]:
    """The claims of common ground in `line` that the daters' own words don't support, given
    `evidence`: for each dater, what they said themselves (their card, their turns). A claim
    is supported only if every fact word in it is in each dater's own words; a claim with no
    fact word ("you two have so much in common") needs some fact word both of them used."""
    if len(evidence) < 2:
        return []  # one dater: no "both" to claim
    theirs = [set().union(*(facts(text) for text in said)) for said in evidence.values()]
    found = []
    for claim in claims(line):
        words = facts(claim)
        if words:
            supported = all(words <= own for own in theirs)
        else:
            supported = bool(set.intersection(*theirs))
        if not supported:
            found.append(claim)
    return found


# A difference made bigger than anything a dater said ("opposite sides of the clock", "night
# and day"): only in a dater's own words ("we're total opposites"), never Nova's framing.
DRAMATIZED = re.compile(
    r"\b(?:opposites?|polar|night and day|worlds apart|(?:couldn't|could not) be more different"
    r"|(?:complete|completely|total|totally) different|clash(?:es|ing)?|rivals?|enemies"
    r"|at war)\b",
    re.IGNORECASE,
)


def dramatized(line: str, evidence: Mapping[str, Iterable[str]]) -> list[str]:
    """The words in `line` that make a difference between the daters dramatic, unless a dater
    used them first (their card, their turns): a narrow word check, as for common ground."""
    said = " ".join(text for texts in evidence.values() for text in texts).lower()
    return [m.group(0) for m in DRAMATIZED.finditer(line) if m.group(0).lower() not in said]
