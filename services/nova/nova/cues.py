"""What the show runner asks for: lines for Nova, sounds, and room events.

When these become room events (a later task), their shapes get zod schemas in
packages/shared, and these classes follow them.
"""

from dataclasses import dataclass
from enum import StrEnum

from nova.cards import Seat
from nova.decisions import Outcome
from nova.questions import OffLimits


class Segment(StrEnum):
    """The brand book's running order."""

    DOORS = "doors"
    FIRST_QUESTION = "first_question"
    FREE_TALK = "free_talk"
    FROM_THE_ROOM = "from_the_room"
    LIGHTNING_ROUND = "lightning_round"
    LAST_WORD = "last_word"
    TIME = "time"
    RESULT = "result"
    NEXT_UP = "next_up"


class Moment(StrEnum):
    """Why Nova speaks. Moments with an approved line carry it in Say.text."""

    DOORS_INTRO = "doors_intro"
    FIRST_QUESTION = "first_question"
    SILENCE = "silence"
    FROM_THE_ROOM = "from_the_room"
    LIGHTNING_UNLOCKED = "lightning_unlocked"
    LIGHTNING_QUESTION = "lightning_question"
    LAST_WORD = "last_word"
    TIME = "time"
    ROOM_TALK = "room_talk"
    RESULT = "result"
    OUTRO = "outro"
    INVITATION = "invitation"
    NEXT_UP = "next_up"
    REPLY = "reply"
    NEED_A_MOMENT = "need_a_moment"
    BEAR_WITH_US = "bear_with_us"
    WELCOME_BACK = "welcome_back"
    DATER_LEFT = "dater_left"
    MODERATOR_END = "moderator_end"


class Sound(StrEnum):
    ON_AIR = "on_air"  # three rising notes as a date starts
    TIMES_UP = "times_up"  # two soft falling notes


class EndReason(StrEnum):
    COMPLETED = "completed"
    DATER_LEFT = "dater_left"
    MODERATOR_ENDED = "moderator_ended"


class ModeratorAlert(StrEnum):
    DATER_REPORTED = "dater_reported"  # a dater pressed Report: the console's most urgent alert
    PAUSE_NOT_BACK = "pause_not_back"  # a dater asked for a moment and isn't back after 30 s
    REPEATED_PAUSE = "repeated_pause"  # the second (or later) pause in this date
    DATER_DISTRESSED = "dater_distressed"  # a dater wants to stop: Nova paused for them
    POSSIBLE_DISTRESS = "possible_distress"  # the distress check says maybe: listen in
    DISTRESS_UNCHECKED = "distress_unchecked"  # the distress check failed or was too slow


@dataclass(frozen=True)
class RoomFacts:
    """What Nova may work from when it talks to the room: facts about the show, never
    names, decisions, spending or anything a dater or listener wrote."""

    segment: Segment
    poll_open: bool
    questions_asked: int  # audience questions read so far in this date


@dataclass(frozen=True)
class Say:
    """A line for Nova. With text, Nova says exactly that (an approved line). Without,
    the line writer writes one for this moment, about this dater, from detail; talk to the
    room works from the room facts. number of of: a question's place in its round (the
    lightning round's "2 of 3")."""

    moment: Moment
    text: str | None = None
    about: Seat | None = None
    detail: str | None = None
    room: RoomFacts | None = None
    number: int | None = None
    of: int | None = None


@dataclass(frozen=True)
class SegmentStarted:
    segment: Segment


@dataclass(frozen=True)
class PlaySound:
    sound: Sound


@dataclass(frozen=True)
class PlayVoiceIntro:
    seat: Seat


@dataclass(frozen=True)
class StopSpeaking:
    """Cut Nova off mid-line."""


@dataclass(frozen=True)
class DecisionWindowOpened:
    seconds: float


@dataclass(frozen=True)
class PollOpened:
    question: str


@dataclass(frozen=True)
class PollClosed:
    pass


@dataclass(frozen=True)
class ShowVerdict:
    yes_share: float


@dataclass(frozen=True)
class LightningMeterClosed:
    """Gestures stop counting toward the lightning unlock: at 5:30, when it's unlocked, or
    when the date ends early. No listener can pay toward an unlock that can't happen."""


@dataclass(frozen=True)
class AlertModerator:
    """For the moderator console only. Never published to the room."""

    alert: ModeratorAlert
    seat: Seat


@dataclass(frozen=True)
class QuestionSkipped:
    """An approved audience question the screen caught on air, skipped without a word: for
    the moderator console only, never the room. A code, never the question."""

    reason: OffLimits


@dataclass(frozen=True)
class DateEnded:
    reason: EndReason
    outcome: Outcome | None


@dataclass(frozen=True)
class PlayFallbackSegment:
    """The recorded fallback segment (technical plan). Plays until the moderator chooses
    what happens next, so the room never sits in silence."""


@dataclass(frozen=True)
class Finished:
    """Nothing more to say for this date."""


type Cue = (
    Say
    | SegmentStarted
    | PlaySound
    | PlayVoiceIntro
    | StopSpeaking
    | DecisionWindowOpened
    | PollOpened
    | PollClosed
    | ShowVerdict
    | LightningMeterClosed
    | AlertModerator
    | QuestionSkipped
    | DateEnded
    | PlayFallbackSegment
    | Finished
)
