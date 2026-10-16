"""A date's set pieces, in one place: how many questions each round has, and the cues that
ask for them. The show runner cues them on the clock, the line writer prepares them before
the date, and the evaluation runs them, so all three always agree (brand book running
order).
"""

from nova.cards import Pair, Seat
from nova.cues import Moment, Say

LIGHTNING_QUESTIONS = 3
AUDIENCE_QUESTIONS = 2


def first_question(pair: Pair, seat: Seat) -> Say:
    """The dater's first prompt from their own blind card."""
    return Say(Moment.FIRST_QUESTION, about=seat, detail=pair.card(seat).prompts[0].text)


def lightning_question(number: int) -> Say:
    """Question number of LIGHTNING_QUESTIONS, read when the cue is made."""
    return Say(Moment.LIGHTNING_QUESTION, number=number, of=LIGHTNING_QUESTIONS)


def last_word(seat: Seat) -> Say:
    return Say(Moment.LAST_WORD, about=seat)


def next_up(next_pair: Pair) -> Say:
    return Say(Moment.NEXT_UP, detail=next_pair.label)


def set_pieces(pair: Pair, next_pair: Pair | None, *, first: Seat = Seat.LEFT) -> list[Say]:
    """Every set piece of a date, in running order, first being who answers first."""
    cues = [
        Say(Moment.DOORS_INTRO),
        *(first_question(pair, seat) for seat in (first, first.other)),
        *(lightning_question(n) for n in range(1, LIGHTNING_QUESTIONS + 1)),
        *(last_word(seat) for seat in (first, first.other)),
        Say(Moment.OUTRO),
    ]
    if next_pair is not None:
        cues.append(next_up(next_pair))
    return cues
