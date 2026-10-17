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
    KEEP_STREETS_OFF_AIR = "keep_streets_off_air"  # a street or address slipped
    KEEP_IT_KIND = "keep_it_kind"  # a dater's first unkind remark
    SHOW_PAUSE = "show_pause"  # a dater's second unkind remark: the date pauses


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
    PERSONAL_DETAIL_ON_AIR = "personal_detail_on_air"  # a dater said a phone, street, workplace...
    CONDUCT_FLAG = "conduct_flag"  # a dater's conduct, with its category: listen in now
    SHOW_HELD = "show_held"  # the date is still held for a dater's conduct after 30 s: decide


class Conduct(StrEnum):
    """What a dater's own words do to the other dater or the room (decision 0006)."""

    NONE = "none"
    PERSONAL_DETAIL = "personal_detail"  # a phone, email, handle, address, workplace, school
    UNKIND = "unkind"  # a put-down or rudeness, short of the rest
    HARASSMENT = "harassment"
    SEXUAL = "sexual"
    HATE = "hate"
    THREAT = "threat"
    MINOR = "minor"  # anything about someone under 18
    UNSURE = "unsure"  # might be one of these: the moderator listens in


class Situation(StrEnum):
    """Why Nova is talking to the room."""

    PAUSE = "pause"  # a dater is taking a moment off air (or the date is held for a report)
    SHOW_PAUSED = "show_paused"  # the whole date is held, for both daters
    DECIDING = "deciding"  # the daters are deciding privately
    ENDED_EARLY = "ended_early"  # a dater left: the date is over before its time


@dataclass(frozen=True)
class RoomFacts:
    """What Nova may work from when it talks to the room: facts about the show, never
    names, decisions, spending or anything a dater or listener wrote."""

    segment: Segment
    poll_open: bool
    questions_asked: int  # audience questions read so far in this date
    situation: Situation


@dataclass(frozen=True)
class Say:
    """A line for Nova. With text, Nova says exactly that (an approved line). Without,
    the line writer writes one for this moment, about this dater, from detail; talk to the
    room works from the room facts. number of of: a question's place in its round (the
    lightning round's "2 of 3"). away: a dater off air after the moderator resumed without
    them, whom an audience question or a reply must not name or address."""

    moment: Moment
    text: str | None = None
    about: Seat | None = None
    detail: str | None = None
    room: RoomFacts | None = None
    number: int | None = None
    of: int | None = None
    away: Seat | None = None


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
    """For the moderator console only. Never published to the room. category: for a
    conduct flag, what the dater's words did."""

    alert: ModeratorAlert
    seat: Seat
    category: Conduct | None = None


@dataclass(frozen=True)
class QuestionSkipped:
    """An approved audience question the screen caught on air, skipped without a word: for
    the moderator console only, never the room. A code, never the question."""

    reason: OffLimits


@dataclass(frozen=True)
class BeatFailed:
    """For the moderator console only: a beat of the running order failed (the room poll or
    the decision desk behind it raised) and is tried again shortly, or, gave_up, no more:
    the moderator decides what happens next. The beat's name is a fixed code."""

    beat: str
    gave_up: bool = False


@dataclass(frozen=True)
class DateEnded:
    reason: EndReason
    outcome: Outcome | None


@dataclass(frozen=True)
class PlayFallbackSegment:
    """The recorded fallback segment (technical plan). Plays until the moderator chooses
    what happens next, or a held date carries on, so the room never sits in silence."""


@dataclass(frozen=True)
class StopFallbackSegment:
    """The fallback segment stops: the hold it covered is over, and Nova speaks next."""


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
    | BeatFailed
    | DateEnded
    | PlayFallbackSegment
    | StopFallbackSegment
    | Finished
)
