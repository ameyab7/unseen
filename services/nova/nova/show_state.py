"""What a line may say about the show itself, by its words (decision 0005, launch
requirement 1): the deterministic part of the check on Nova's lines, run on every model
line, set pieces and live lines alike. The model's meaning check (nova/meaning.py) judges
the rest; neither replaces the other.

- A show fact no task gives: a break, a studio, coming back after something. Moments that
  talk about the show (the doors, room talk, the outro...) get quality.SHOW_FICTION in full.
  Replies, silences and audience questions only get the plainly made-up show events, since
  a dater's card or words can bring in a holiday break or a stage.
- A segment that isn't happening now: a line that names the lightning round, last words,
  the next pair or an audience question, outside that segment. Nova never says what comes
  next (its rules), so a reply in free talk about "the lightning round" isn't said either.
"""

import re

from nova.cues import Moment, Segment
from nova.quality import SHOW_FICTION, SHOW_MOMENTS

# Show events no live line can know of: the show breaking, coming back, its studio.
LIVE_FICTION = re.compile(
    r"\bbe right back\b|\bback after (?:this|the break)\b|\b(?:after|before) the break\b"
    r"|\b(?:take|taking|have|having) a (?:quick |short |little )?break\b"
    r"|\b(?:in|from) (?:the|our|this) studio\b|\bour studio\b|\bhere in the studio\b",
    re.IGNORECASE,
)
LIVE_MOMENTS = frozenset((Moment.REPLY, Moment.SILENCE, Moment.FROM_THE_ROOM))

# The segments a line could name, by the words that name them.
NAMED: dict[Segment, re.Pattern[str]] = {
    Segment.LIGHTNING_ROUND: re.compile(r"\blightning (?:round|questions?)\b", re.IGNORECASE),
    Segment.LAST_WORD: re.compile(r"\b(?:last|final) words?\b", re.IGNORECASE),
    Segment.NEXT_UP: re.compile(
        r"\bnext (?:pair|couple|daters)\b|\b(?:coming )?up next\b", re.IGNORECASE
    ),
    Segment.FROM_THE_ROOM: re.compile(
        r"\b(?:a |the )?questions? from (?:the room|a listener|the audience|listeners)\b"
        r"|\baudience questions?\b",
        re.IGNORECASE,
    ),
}
# The segment a set piece is said in, written before the date.
SET_PIECE_SEGMENT = {
    Moment.DOORS_INTRO: Segment.DOORS,
    Moment.FIRST_QUESTION: Segment.FIRST_QUESTION,
    Moment.LIGHTNING_QUESTION: Segment.LIGHTNING_ROUND,
    Moment.LAST_WORD: Segment.LAST_WORD,
    Moment.OUTRO: Segment.RESULT,
    Moment.NEXT_UP: Segment.NEXT_UP,
}


def invents_show_fact(moment: Moment, text: str) -> bool:
    """A show fact the line's task never gave it."""
    if moment in SHOW_MOMENTS:
        return SHOW_FICTION.search(text) is not None
    if moment in LIVE_MOMENTS:
        return LIVE_FICTION.search(text) is not None
    return False


def segment_for(moment: Moment, current: Segment | None) -> Segment | None:
    """Where a line is said: its own segment for a set piece, else the one the show runner
    last started (None: not known, so not checked)."""
    return SET_PIECE_SEGMENT.get(moment, current)


def wrong_segments(text: str, segment: Segment | None) -> frozenset[Segment]:
    """The segments the line names that aren't the one it's said in. Not checked when the
    segment isn't known."""
    if segment is None:
        return frozenset()
    return frozenset(s for s, words in NAMED.items() if s is not segment and words.search(text))
