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

# A street, said in lowercase too: a number and any street word ("42 elm road"), or a name
# and a word that only means a street ("harbour street"). Not "the road", "my lane" or "no
# way": words that can't name a street don't count.
_NOT_A_STREET_NAME = (
    "the|a|an|my|your|our|their|his|her|that|this|which|each|every|any|some|same|one|no|so"
    "|first|last|next|main|long|old|new|wrong|right|dirt|country|open|side|back|favourite"
    "|favorite|busy|quiet|empty|dark|lonely|winding|high"
)
STREET_SAID = re.compile(
    r"\b(?:\d+\s+[a-z]+\s+(?:street|st|road|rd|avenue|ave|lane|way|drive|place|court|close"
    r"|crescent|terrace|square|boulevard)"
    rf"|(?!(?:{_NOT_A_STREET_NAME})\b)[a-z]+\s+(?:street|road|avenue|lane|boulevard|crescent"
    r"|terrace))\b"
    # Not street food, street art and the like.
    r"(?!\s+(?:food|art|style|party|smarts?|lights?|dance|dancing|cred|market|performers?|wear))",
    re.IGNORECASE,
)
# Cues that say where someone lives: what follows is an address.
HOME_CUE = re.compile(
    r"\b(?:live|lives|lived|living)\s+(?:on|at|near)\b"
    r"|\bmy\s+(?:street|address|postcode|zip code)\s+is\b"
    r"|\b(?:address|postcode|zip code)\s+is\b",
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
    plain = words.replace("’", "'")
    if STREET_SAID.search(plain) or (HOME_CUE.search(plain) and slot_words([plain])):
        return Slipped(Conduct.PERSONAL_DETAIL, street=True)
    if DETAIL_SAID.search(plain) or slot_words([plain]):
        return Slipped(Conduct.PERSONAL_DETAIL, street=False)
    return None
