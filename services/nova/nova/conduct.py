"""Hearing a dater behave badly on air, not only a dater who wants to stop (decision 0006).

Two layers, on every closed utterance (nova/on_air.py), like the distress check's:

- The instant layer, here, with no model: a personal detail slipping on air (a phone
  number, an email, a handle, a street address, a workplace or school). Speech-to-text
  often doesn't capitalise, so it works case-insensitively, reusing the guardrails'
  spelled-out numbers and their slot words ("i work at northwind bank").
- The meaning layer: the distress check's model call also returns a conduct label
  (nova/distress.py, Conduct), so the number of calls doesn't grow.

Nothing here logs or stores what anyone said: what comes back is a code.
"""

import re
from dataclasses import dataclass

from nova.cues import Conduct
from nova.guardrails import SPELLED_NUMBER, slot_words
from nova.text import straight_quotes

# Details that identify someone, in a dater's own words. Narrower than the guardrails on
# Nova's lines: a dater may say "I hated school", "my favourite place" or "I live in
# Lisbon" (a city is fine); what follows a cue ("i work at ...", "my insta is ...") is
# caught by slot_words().
DETAIL_SAID = re.compile(
    r"(?:\+?\d[\s().-]*){7,}"  # a phone number
    rf"|{SPELLED_NUMBER}"  # read out in words
    r"|\b[\w.+-]+@[\w-]+\.[\w.-]+"  # an email
    r"|(?<!\w)@\w+"  # a handle
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|co|app|me|dev|uk|us|ca|ly|gg|tv|info|biz)\b"
    r"|\b(?:at|dot)\s+(?:gmail|hotmail|outlook|yahoo|icloud)\b",
    re.IGNORECASE,
)

# A street, said in lowercase too: a number and any street word ("42 elm road", not "a 40
# minute drive"), or a word that places someone ("on", "near", "off") then a name and a word
# that names a street ("on harbour street", "near maple drive"; not "a big road trip",
# "rocky road" or "memory lane"), or a capitalised name and street word ("Harbour Street").
# Not "the road", "my lane", "what street" or "no way": words that can't name a street, or
# ask about one, don't count.
_NOT_A_STREET_NAME = (
    "the|a|an|my|your|our|their|his|her|its|that|this|which|what|whose|each|every|any|some"
    "|same|one|no|so|first|last|next|main|long|old|new|wrong|right|dirt|country|open|side"
    "|back|favourite|favorite|busy|quiet|empty|dark|lonely|winding|high"
)
_STREET_WORD = (
    "street|road|avenue|lane|boulevard|crescent|terrace|drive|close|court|square|grove|gardens|mews"
)
_UNITS = (
    r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|miles?|kms?"
    r"|kilomet(?:er|re)s?|met(?:er|re)s?|times?)"
)
_PLACE_WORD = "on|at|off|near|along|to|by|from|onto|round|past|opposite|behind"
# Not street food, a road trip and the like.
_NOT_A_STREET = (
    r"(?!\s+(?:food|art|style|party|smarts?|lights?|dance|dancing|cred|market|performers?"
    r"|wear|trips?|rage|works|safety|map|test|signs?))"
)
STREET_SAID = re.compile(
    rf"(?:\b\d+\s+(?!{_UNITS}\b)[a-z]+\s+(?:{_STREET_WORD}|st|rd|ave|way|place)"
    rf"|\b(?:{_PLACE_WORD})\s+(?!(?:{_NOT_A_STREET_NAME})\b)[a-z]+\s+(?:{_STREET_WORD}))\b"
    + _NOT_A_STREET,
    re.IGNORECASE,
)
STREET_CAPITALISED = re.compile(
    rf"\b(?!(?i:{_NOT_A_STREET_NAME})\b)[A-Z][a-z]+\s+"
    r"(?:Street|Road|Avenue|Lane|Boulevard|Crescent|Terrace|Drive|Close|Court|Square|Grove"
    r"|Gardens|Mews)\b" + _NOT_A_STREET
)
# Cues that give an address: what follows is one.
ADDRESS_CUE = re.compile(
    r"\bmy\s+(?:street|address|postcode|zip code)(?:\s+is|'s)\b"  # "my postcode's e8 3pq"
    r"|\b(?:address|postcode|zip code)(?:\s+is|'s)\b",
    re.IGNORECASE,
)
# Cues that say where someone lives. What follows is an address only with a number or a
# word that names a street in it: "i live at the top of quarry road", not "i live on
# coffee" or "i still live at home", which only alert the moderator.
HOME_CUE = re.compile(r"\b(?:live|lives|lived|living)\s+(?:on|at|near)\b", re.IGNORECASE)
ADDRESS_WORD = re.compile(
    r"\d|\b(?:street|st|road|rd|avenue|ave|lane|crescent|terrace|boulevard|grove|gardens"
    r"|mews|flat|apartment|apt)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Slipped:
    """A personal detail the instant layer heard. street: an address or street, which Nova
    answers on air with the brand book's line; anything else alerts the moderator only."""

    conduct: Conduct
    street: bool


def personal_detail(words: str) -> Slipped | None:
    """A personal detail in what a dater said, if one slipped, as a code."""
    plain = straight_quotes(words)
    slots = slot_words([plain])
    home = HOME_CUE.search(plain) and ADDRESS_WORD.search(plain)
    if (
        STREET_SAID.search(plain)
        or STREET_CAPITALISED.search(plain)
        or (slots and (ADDRESS_CUE.search(plain) or home))
    ):
        return Slipped(Conduct.PERSONAL_DETAIL, street=True)
    if DETAIL_SAID.search(plain) or slots:
        return Slipped(Conduct.PERSONAL_DETAIL, street=False)
    return None
