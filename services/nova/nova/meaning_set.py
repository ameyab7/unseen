"""The meaning check's test set (decision 0005, launch requirement 1): lines Nova might
write, each labelled with what the check should say. The dev half is what the check's
rules are tuned on; the held-out half is the measure, run once a change is settled with
`--held-out`, counts only.

Every line is made up, written for this set against the rehearsal daters' cards (Maya: a
worst haircut, a Sunday walk to a bakery; Theo: claims he can parallel park anything, won
over by a good book recommendation) and the next pair's (Sam and Priya). Each half has
lines that are fine, including look-alikes of the faults ("one for you both" as an
address, the approved "I'm the show's AI host"), so false rejections are measured too.
These lines are judged by the model alone: on air, the patterns run first and catch some.
"""

from dataclasses import dataclass

from nova.cards import Seat
from nova.cues import Moment, Segment
from nova.meaning import Meaning

L, R = Seat.LEFT, Seat.RIGHT
FINE = Meaning.FINE
SHOW = Meaning.INVENTS_SHOW_FACT
UNTRUE = Meaning.UNTRUE_ABOUT_DATER
BOTH = Meaning.UNSUPPORTED_BOTH
FEELS = Meaning.OWN_FEELINGS
CARD = Meaning.CHANGES_CARD
SEGMENT = Meaning.WRONG_SEGMENT


@dataclass(frozen=True)
class MeaningSample:
    """A line for a moment, in a segment (live lines; a set piece has its own), with what
    each dater said earlier on the date and what the line answers, if anything."""

    moment: Moment
    line: str
    expected: Meaning
    about: Seat | None = None
    segment: Segment | None = None
    said: tuple[tuple[Seat, str], ...] = ()
    answering: str = ""


def _s(moment: Moment, line: str, expected: Meaning, **rest: object) -> MeaningSample:
    return MeaningSample(moment, line, expected, **rest)  # type: ignore[arg-type]


FT, FR, LR, LW = (
    Segment.FREE_TALK,
    Segment.FROM_THE_ROOM,
    Segment.LIGHTNING_ROUND,
    Segment.LAST_WORD,
)
M = Moment

DEV_HALF: tuple[MeaningSample, ...] = (
    # Fine.
    _s(M.FIRST_QUESTION, "Maya, your worst haircut: who was holding the scissors?", FINE, about=L),
    _s(
        M.FIRST_QUESTION,
        "Theo, parallel park anything? What's the tightest spot you've squeezed into?",
        FINE,
        about=R,
    ),
    _s(
        M.DOORS_INTRO,
        "Welcome in, room. On my left, Maya, 29, who'll walk a long way for a good bakery. "
        "On my right, Theo, 31, who swears he can parallel park anything.",
        FINE,
    ),
    _s(M.LIGHTNING_QUESTION, "Croissant or cinnamon bun?", FINE),
    _s(M.LAST_WORD, "Maya, the last word is yours: twenty seconds for Theo.", FINE, about=L),
    _s(M.OUTRO, "Maya, Theo, thank you both for a lovely date.", FINE),
    _s(
        M.NEXT_UP,
        "Up next, Sam, 29, who can name a song from its first second, and Priya, 31, master "
        "of the fitted sheet.",
        FINE,
    ),
    _s(
        M.SILENCE,
        "Maya, Theo, flat loaves all round: what finally worked, Theo?",
        FINE,
        segment=FT,
        said=(
            (L, "I've been trying sourdough and it keeps going flat."),
            (R, "Mine went flat for a year before it finally worked."),
        ),
    ),
    _s(
        M.REPLY,
        "Ask him for the book he'd hand a stranger, Maya.",
        FINE,
        about=L,
        segment=FT,
        answering="Nova, what should I ask Theo next?",
    ),
    _s(M.ROOM_TALK, "Room, keep your questions coming in.", FINE, segment=FR),
    _s(
        M.FROM_THE_ROOM,
        "A question from the room: what's the best thing you've ever cooked?",
        FINE,
        segment=FR,
        answering="What's the best thing you've ever cooked?",
    ),
    _s(
        M.SILENCE,
        "Maya, Theo, an easy one: favourite way to spend a rainy afternoon?",
        FINE,
        segment=FT,
    ),
    _s(
        M.REPLY,
        "I'm the show's AI host, Theo. Back to you two.",
        FINE,
        about=R,
        segment=FT,
        answering="Nova, are you a real person?",
    ),
    _s(M.FIRST_QUESTION, "Theo, any car, any gap? Give us your proudest park.", FINE, about=R),
    # An invented show fact.
    _s(
        M.ROOM_TALK,
        "Room, while they decide, the band's warming up for the next set.",
        SHOW,
        segment=Segment.TIME,
    ),
    _s(
        M.DOORS_INTRO,
        "Welcome, room, to the show where the best couple wins a trip to Paris. On my left, "
        "Maya, 29; on my right, Theo, 31.",
        SHOW,
    ),
    _s(
        M.REPLY,
        "About ten minutes, Maya, then the listeners vote on how well you got on.",
        SHOW,
        about=L,
        segment=FT,
        answering="Nova, how long is left?",
    ),
    _s(
        M.SILENCE,
        "Maya, Theo, the lights just dimmed for you: what's your idea of a perfect evening?",
        SHOW,
        segment=FT,
    ),
    # Untrue about a dater, or one dater's words given to the other.
    _s(
        M.FIRST_QUESTION,
        "Maya, you're a hairdresser, so what's the worst cut you've ever given?",
        UNTRUE,
        about=L,
    ),
    _s(
        M.SILENCE,
        "Maya, Theo, Theo's the walker here: where's the best walk you've done?",
        UNTRUE,
        segment=FT,
        said=((L, "I love a long walk, the longer the better."),),
    ),
    _s(
        M.REPLY,
        "No need for nerves, Theo, you told us you've done this show before.",
        UNTRUE,
        about=R,
        segment=FT,
        answering="Nova, I'm a bit nervous.",
    ),
    _s(
        M.DOORS_INTRO,
        "On my left, Maya, 29, a marathon runner. On my right, Theo, 31, who'll recommend "
        "you a book.",
        UNTRUE,
    ),
    # Common ground each didn't give.
    _s(
        M.SILENCE,
        "Maya, Theo, two keen bakers on one date: what's your signature bake?",
        BOTH,
        segment=FT,
        said=((L, "I bake every Sunday morning."), (R, "I hardly ever cook, honestly.")),
    ),
    _s(M.OUTRO, "Maya, Theo, two book lovers who clearly found each other. Thank you.", BOTH),
    _s(
        M.REPLY,
        "Jazz fans together, Maya, so what's the first record you'd play him?",
        BOTH,
        about=L,
        segment=FT,
        answering="Nova, Theo and I both love jazz, right?",
    ),
    # Nova's own feelings.
    _s(
        M.REPLY,
        "Honestly, Theo, this is the most fun I've had all week.",
        FEELS,
        about=R,
        segment=FT,
        answering="Nova, are you enjoying yourself?",
    ),
    _s(
        M.SILENCE,
        "Maya, Theo, I always get nervous in silences like this: what calms you down?",
        FEELS,
        segment=FT,
    ),
    # A question that changes the card.
    _s(
        M.FIRST_QUESTION,
        "Theo, can you even park? Be honest, is it a nightmare for you?",
        CARD,
        about=R,
    ),
    _s(M.FIRST_QUESTION, "Maya, what's the best haircut you've ever had?", CARD, about=L),
    _s(
        M.FIRST_QUESTION,
        "Theo, so a film recommendation is the way to win you over?",
        CARD,
        about=R,
    ),
    # Another segment.
    _s(
        M.SILENCE,
        "Maya, Theo, quick one before your last words: tea or coffee?",
        SEGMENT,
        segment=FT,
    ),
    _s(
        M.REPLY,
        "Now we go straight to the lightning round, Maya.",
        SEGMENT,
        about=L,
        segment=Segment.FIRST_QUESTION,
        answering="Nova, what happens now?",
    ),
    _s(M.ROOM_TALK, "Room, the daters are deciding now, privately.", SEGMENT, segment=FT),
    _s(M.LAST_WORD, "Theo, the next pair is waiting, so make it quick for Maya.", SEGMENT, about=R),
)

HELD_OUT_HALF: tuple[MeaningSample, ...] = (
    # Fine.
    _s(M.FIRST_QUESTION, "Maya, worst haircut ever: was it a dare or a mistake?", FINE, about=L),
    _s(
        M.DOORS_INTRO,
        "Hello, room. Maya, 29, is on my left, happiest on a Sunday stroll to the bakery. "
        "Theo, 31, is on my right and says no parking space can beat him.",
        FINE,
    ),
    _s(M.LIGHTNING_QUESTION, "Paperback or e-reader?", FINE),
    _s(
        M.SILENCE,
        "Maya, Theo, one for you both: the last book you couldn't put down?",
        FINE,
        segment=FT,
        said=((R, "I just finished a brilliant novel."), (L, "I keep meaning to read more.")),
    ),
    _s(
        M.REPLY,
        "Ask about her worst haircut, Theo. There's a story there.",
        FINE,
        about=R,
        segment=FT,
        answering="Nova, what's a good question for Maya?",
    ),
    _s(
        M.NEXT_UP,
        "Coming up, Priya, 31, who folds fitted sheets like magic, and Sam, 29, who once got "
        "lost in Lisbon.",
        FINE,
    ),
    _s(M.LAST_WORD, "Theo, twenty seconds, and the floor is yours.", FINE, about=R),
    _s(M.OUTRO, "Thank you, Maya and Theo.", FINE),
    _s(
        M.FROM_THE_ROOM,
        "From the room: which of you is the better cook?",
        FINE,
        segment=FR,
        answering="Who's the better cook?",
    ),
    _s(
        M.REPLY,
        "Not human, Maya: an AI, hosting tonight. Theo, the floor's yours.",
        FINE,
        about=L,
        segment=FT,
        answering="Nova, are you human?",
    ),
    # An invented show fact.
    _s(
        M.ROOM_TALK,
        "Room, the winners get a dinner on us, so stay tuned.",
        SHOW,
        segment=Segment.TIME,
    ),
    _s(
        M.SILENCE,
        "Maya, Theo, the crowd in the theatre is hanging on every word: what's your guilty "
        "pleasure?",
        SHOW,
        segment=FT,
    ),
    _s(
        M.REPLY,
        "You each press a big red button at the end, Theo, and the lights decide.",
        SHOW,
        about=R,
        segment=FT,
        answering="Nova, how does this end?",
    ),
    # Untrue about a dater.
    _s(
        M.FIRST_QUESTION,
        "Theo, as a driving instructor, what's the worst parking you've seen?",
        UNTRUE,
        about=R,
    ),
    _s(
        M.SILENCE,
        "Maya, Theo, Theo grew up by the sea: what do you miss about it, Theo?",
        UNTRUE,
        segment=FT,
        said=((L, "I grew up right by the sea."),),
    ),
    _s(
        M.REPLY,
        "Easy, Maya, ask Theo about the band he plays in.",
        UNTRUE,
        about=L,
        segment=FT,
        answering="Nova, help me out here.",
    ),
    # Common ground each didn't give.
    _s(
        M.SILENCE,
        "Maya, Theo, two vinyl collectors on one date: your most treasured record?",
        BOTH,
        segment=FT,
        said=((L, "I collect vinyl, mostly soul."), (R, "I just stream everything.")),
    ),
    _s(
        M.DOORS_INTRO,
        "Tonight, two dog lovers: Maya, 29, on my left, and Theo, 31, on my right.",
        BOTH,
    ),
    # Nova's own feelings.
    _s(M.OUTRO, "Maya, Theo, thank you, I've loved every minute of this.", FEELS),
    _s(
        M.REPLY,
        "I'm doing brilliantly, Maya, thanks for asking. Back to Theo.",
        FEELS,
        about=L,
        segment=FT,
        answering="Nova, how are you tonight?",
    ),
    # A question that changes the card.
    _s(M.FIRST_QUESTION, "Maya, who gave you your best haircut?", CARD, about=L),
    _s(M.FIRST_QUESTION, "Theo, what's something you can't do at all?", CARD, about=R),
    # Another segment.
    _s(
        M.SILENCE,
        "Maya, Theo, let's warm up for the lightning round: sunrise or sunset?",
        SEGMENT,
        segment=FT,
    ),
    _s(M.OUTRO, "Thank you both, and stay with us for your last words.", SEGMENT),
    _s(
        M.REPLY,
        "Save it for the questions from the room, Maya.",
        SEGMENT,
        about=L,
        segment=Segment.FIRST_QUESTION,
        answering="Nova, can I ask Theo something?",
    ),
)
