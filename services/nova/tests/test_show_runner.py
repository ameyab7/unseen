"""Nova's show runner, driven on a fake clock. No network, no audio, no model."""

import pytest

from nova import script
from nova.cards import Seat
from nova.cues import (
    DateEnded,
    DecisionWindowOpened,
    EndReason,
    Finished,
    Moment,
    PlaySound,
    PlayVoiceIntro,
    PollClosed,
    PollOpened,
    Say,
    SegmentStarted,
    ShowVerdict,
    Sound,
    StopSpeaking,
)
from nova.decisions import Choice, InMemoryDecisionDesk, Outcome
from nova.fixtures import MAYA, NEXT_PAIR, REHEARSAL_PAIR, THEO
from nova.show_runner import ShowRunner
from nova.timing import FULL, REHEARSAL, Timing

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT


class FakePoll:
    def __init__(self, yes_share: float | None = 0.78):
        self.share = yes_share

    def yes_share(self) -> float | None:
        return self.share


class Show:
    """Drives a ShowRunner in fixed steps and records every cue with its wall time."""

    def __init__(self, *, timing: Timing = FULL, step: float = 0.5, poll=None, **options):
        self.desk = InMemoryDecisionDesk()
        self.poll = poll or FakePoll()
        self.step = step
        self.runner = ShowRunner(
            REHEARSAL_PAIR,
            desk=self.desk,
            poll=self.poll,
            timing=timing,
            next_pair=options.pop("next_pair", NEXT_PAIR),
            **options,
        )
        self.now = 0.0
        self.log: list[tuple[float, object]] = []
        self._record(self.runner.start(0.0))

    def _record(self, cues):
        self.log.extend((self.now, cue) for cue in cues)
        return cues

    def until(self, t: float):
        while self.now + self.step <= t + 1e-9:
            self.now = round(self.now + self.step, 6)
            self._record(self.runner.tick(self.now))
        return self

    def at(self, t: float, event: str, *args):
        self.until(t)
        self.now = t
        return self._record(getattr(self.runner, event)(t, *args))

    def cues(self, kind=None, *, since: float = 0.0, before: float = float("inf")):
        return [
            cue
            for t, cue in self.log
            if since <= t < before and (kind is None or isinstance(cue, kind))
        ]

    def times(self, kind, **match):
        return [
            t
            for t, cue in self.log
            if isinstance(cue, kind) and all(getattr(cue, k) == v for k, v in match.items())
        ]

    def segments(self):
        return [(t, cue.segment) for t, cue in self.log if isinstance(cue, SegmentStarted)]

    def says(self, moment=None):
        return [(t, c) for t, c in self.log if isinstance(c, Say) and moment in (None, c.moment)]


def full_date(**options) -> Show:
    show = Show(**options)
    show.until(600)
    return show


# --- The running order -------------------------------------------------------------------


def test_walks_the_running_order_at_the_brand_book_times():
    show = Show()
    show.at(200, "unlock_lightning_round")
    show.until(600)
    assert show.segments() == [
        (0.0, "doors"),
        (45.0, "first_question"),
        (90.0, "free_talk"),
        (240.0, "from_the_room"),
        (330.0, "lightning_round"),
        (420.0, "last_word"),
        (480.0, "time"),
        (540.0, "result"),
        (570.0, "next_up"),
    ]
    assert show.times(Finished) == [570.0]


def test_doors_open_with_the_sound_nova_and_both_voice_intros():
    show = Show()
    assert show.cues(before=0.5) == [
        PlaySound(Sound.ON_AIR),
        SegmentStarted("doors"),
        Say(Moment.DOORS_INTRO),
        PlayVoiceIntro(LEFT),
        PlayVoiceIntro(RIGHT),
    ]


def test_each_dater_gets_one_of_their_own_prompts_back():
    show = full_date()
    asked = show.says(Moment.FIRST_QUESTION)
    assert asked == [
        (45.0, Say(Moment.FIRST_QUESTION, about=LEFT, detail=MAYA.prompts[0].text)),
        (67.5, Say(Moment.FIRST_QUESTION, about=RIGHT, detail=THEO.prompts[0].text)),
    ]


def test_last_word_gives_each_dater_a_turn():
    show = full_date()
    assert [(t, c.about) for t, c in show.says(Moment.LAST_WORD)] == [(420.0, LEFT), (445.0, RIGHT)]


def test_time_opens_the_private_decision_and_the_room_poll():
    show = full_date()
    assert show.cues(since=480, before=480.5) == [
        PlaySound(Sound.TIMES_UP),
        SegmentStarted("time"),
        DecisionWindowOpened(seconds=60.0),
        PollOpened(script.POLL_QUESTION),
        Say(Moment.TIME, text="That's time. Maya and Theo are deciding, privately."),
        Say(Moment.ROOM_TALK),
    ]


def test_rehearsal_timing_runs_a_whole_date_in_about_three_minutes():
    show = Show(timing=REHEARSAL, step=0.25)
    show.until(240)
    assert show.times(Finished) == [171.0]
    assert [segment for _, segment in show.segments()][-2:] == ["result", "next_up"]


def test_invitation_to_take_a_seat_at_most_once_a_night():
    assert full_date().says(Moment.INVITATION) != []
    assert full_date(invitation_given_tonight=True).says(Moment.INVITATION) == []


def test_without_a_next_pair_the_show_finishes_after_the_result():
    show = full_date(next_pair=None)
    assert show.says(Moment.NEXT_UP) == []
    assert show.times(Finished) == [570.0]


# --- Long silence during free talk --------------------------------------------------------


def test_a_long_silence_in_free_talk_brings_one_prompt_then_waits():
    show = Show()
    show.until(150)
    nudges = show.says(Moment.SILENCE)
    assert [t for t, _ in nudges][:3] == [98.0, 118.0, 138.0]
    assert [c.about for _, c in nudges][:2] == [LEFT, RIGHT]


def test_talking_resets_the_silence_clock_and_nova_prompts_the_quieter_dater():
    show = Show()
    show.at(95, "speech", LEFT)
    show.until(110)
    assert show.says(Moment.SILENCE) == [(103.0, Say(Moment.SILENCE, about=RIGHT))]


def test_nova_stays_quiet_in_segments_without_the_silence_rule():
    show = Show()
    show.until(89)
    show.at(200, "unlock_lightning_round")
    show.until(479)
    times = [t for t, _ in show.says(Moment.SILENCE)]
    assert times, "free talk and from the room should prompt after a silence"
    assert all(90 <= t < 330 for t in times)


# --- Lightning round ----------------------------------------------------------------------


def test_lightning_round_runs_three_questions_only_when_unlocked():
    show = Show()
    unlocked = show.at(200, "unlock_lightning_round")
    assert unlocked == [Say(Moment.LIGHTNING_UNLOCKED, text=script.LIGHTNING_UNLOCKED)]
    show.until(600)
    assert [(t, c.detail) for t, c in show.says(Moment.LIGHTNING_QUESTION)] == [
        (330.0, "1 of 3"),
        (360.0, "2 of 3"),
        (390.0, "3 of 3"),
    ]


def test_without_the_unlock_the_lightning_slot_returns_to_free_talk():
    show = full_date()
    assert (330.0, "free_talk") in show.segments()
    assert show.says(Moment.LIGHTNING_QUESTION) == []


def test_an_unlock_after_the_lightning_slot_changes_nothing():
    show = Show()
    assert show.at(335, "unlock_lightning_round") == []
    show.until(600)
    assert show.says(Moment.LIGHTNING_QUESTION) == []


# --- From the room ------------------------------------------------------------------------


def test_asks_up_to_two_screened_audience_questions():
    show = Show()
    show.at(100, "queue_question", "What's the best meal you've ever had?")
    show.at(250, "queue_question", "Mountains or sea?")
    show.at(260, "queue_question", "A third one that won't fit")
    show.until(600)
    assert [(t, c.detail) for t, c in show.says(Moment.FROM_THE_ROOM)] == [
        (240.0, "What's the best meal you've ever had?"),
        (285.0, "Mountains or sea?"),
    ]


# --- Need a moment ------------------------------------------------------------------------


def test_need_a_moment_nova_covers_and_the_clock_holds_for_thirty_seconds():
    show = Show()
    covered = show.at(100, "need_a_moment", LEFT)
    assert covered == [Say(Moment.NEED_A_MOMENT, text=script.NEED_A_MOMENT), Say(Moment.ROOM_TALK)]
    assert "Maya" not in script.NEED_A_MOMENT and "Theo" not in script.NEED_A_MOMENT

    show.until(129.5)
    assert show.cues(since=100.5, before=130) == [], "nothing else happens while Nova covers"
    show.until(300)
    welcome_back = Say(Moment.WELCOME_BACK, text=script.WELCOME_BACK)
    assert show.says(Moment.WELCOME_BACK) == [(130.0, welcome_back)]
    assert (270.0, "from_the_room") in show.segments(), "the running order shifted by 30 s"


def test_coming_back_early_ends_the_hold_early():
    show = Show()
    show.at(100, "need_a_moment", LEFT)
    assert show.at(105, "back", RIGHT) == [], "only the dater who asked can end it"
    assert show.at(110, "back", LEFT) == [Say(Moment.WELCOME_BACK, text=script.WELCOME_BACK)]
    show.until(300)
    assert (250.0, "from_the_room") in show.segments()


def test_need_a_moment_only_on_air_and_one_at_a_time():
    show = Show()
    show.at(100, "need_a_moment", LEFT)
    assert show.at(101, "need_a_moment", RIGHT) == []
    show.until(140)
    # The 30 s hold moved Time from 480 to 510, so 520 is inside the decision window.
    assert show.at(520, "need_a_moment", LEFT) == [], "not while the daters decide"


# --- A dater leaves -----------------------------------------------------------------------


BLAME = ("left", "quit", "walked", "reject", "fault", "sorry", "because", "reason", "decided")


def test_a_dater_leaving_is_closed_kindly_and_the_date_ends_as_dater_left():
    show = Show()
    closed = show.at(200, "dater_left", RIGHT)
    line = "Theo's had to step away, and that's always allowed. Maya, thank you."
    assert closed == [
        StopSpeaking(),
        Say(Moment.DATER_LEFT, text=line),
        DateEnded(EndReason.DATER_LEFT, outcome=None),
    ]
    assert not any(word in line.lower() for word in BLAME)

    show.until(600)
    assert show.segments()[-1] == (230.0, "next_up"), "the show moves on to the next pair"
    assert show.cues(ShowVerdict) == []
    assert show.says(Moment.RESULT) == [] and show.says(Moment.INVITATION) == []
    assert show.runner.end_reason is EndReason.DATER_LEFT
    assert show.runner.outcome is None


def test_leaving_while_deciding_closes_the_poll_without_a_verdict_or_outcome():
    show = Show()
    show.until(490)
    show.desk.choose(RIGHT, Choice.REVEAL)
    closed = show.at(500, "dater_left", LEFT)
    assert PollClosed() in closed
    show.until(600)
    assert show.cues(ShowVerdict) == []
    assert show.desk.resolved is False


def test_leaving_during_a_moment_releases_the_hold():
    show = Show()
    show.at(100, "need_a_moment", LEFT)
    show.at(110, "dater_left", LEFT)
    show.until(200)
    assert show.says(Moment.WELCOME_BACK) == []
    assert show.segments()[-1] == (140.0, "next_up")


# --- The moderator ends the date ----------------------------------------------------------


def test_moderator_end_stops_nova_at_once_with_only_the_approved_line():
    show = Show()
    show.at(100, "queue_question", "Saved for later")
    ended = show.at(300, "moderator_end")
    assert ended == [
        StopSpeaking(),
        Say(Moment.MODERATOR_END, text=script.MODERATOR_END),
        DateEnded(EndReason.MODERATOR_ENDED, outcome=None),
        Finished(),
    ]
    later = []
    for t, event, args in [
        (301, "speech", (LEFT,)),
        (302, "need_a_moment", (LEFT,)),
        (303, "unlock_lightning_round", ()),
        (304, "dater_left", (RIGHT,)),
        (305, "moderator_end", ()),
    ]:
        later += show.at(t, event, *args)
    show.until(700)
    assert later == [] and show.cues(since=300.5) == []
    assert show.says()[-1] == (300.0, Say(Moment.MODERATOR_END, text=script.MODERATOR_END))


def test_moderator_end_while_deciding_closes_the_poll_and_never_resolves():
    show = Show()
    show.until(490)
    show.desk.choose(LEFT, Choice.REVEAL)
    show.desk.choose(RIGHT, Choice.REVEAL)
    ended = show.at(500, "moderator_end")
    assert [type(c).__name__ for c in ended] == [
        "StopSpeaking",
        "Say",
        "PollClosed",
        "DateEnded",
        "Finished",
    ]
    assert show.desk.resolved is False
    assert show.cues(ShowVerdict) == []


# --- Decisions and the result -------------------------------------------------------------


def decide(left: Choice | None, right: Choice | None, at: float = 485) -> Show:
    show = Show()
    show.until(at)
    for seat, choice in ((LEFT, left), (RIGHT, right)):
        if choice is not None:
            show.desk.choose(seat, choice)
    show.until(600)
    return show


def test_both_reveal_is_mutual_announced_when_the_window_closes():
    show = decide(Choice.REVEAL, Choice.REVEAL)
    assert show.says(Moment.RESULT) == [(540.0, Say(Moment.RESULT, text=script.RESULT_MUTUAL))]
    assert show.runner.outcome is Outcome.MUTUAL
    assert DateEnded(EndReason.COMPLETED, Outcome.MUTUAL) in show.cues()


NOT_MUTUAL = [
    (Choice.REVEAL, Choice.UNSEEN),
    (Choice.UNSEEN, Choice.UNSEEN),
    (Choice.REVEAL, None),
    (None, Choice.UNSEEN),
    (None, None),
]


@pytest.mark.parametrize(("left", "right"), NOT_MUTUAL)
def test_no_answer_or_any_unseen_is_not_a_match(left, right):
    show = decide(left, right)
    assert show.says(Moment.RESULT) == [(540.0, Say(Moment.RESULT, text=script.RESULT_NOT_MUTUAL))]
    assert show.runner.outcome is Outcome.NOT_MUTUAL


def test_every_not_a_match_sounds_the_same_so_nobody_learns_who_chose_what():
    from_result = [decide(left, right).cues(since=540) for left, right in NOT_MUTUAL]
    assert all(cues == from_result[0] for cues in from_result)
    result_line = next(c.text for c in from_result[0] if isinstance(c, Say))
    assert "Maya" not in result_line and "Theo" not in result_line


def test_deciding_early_does_not_bring_the_result_forward():
    early = decide(Choice.REVEAL, Choice.UNSEEN, at=481)
    late = decide(Choice.REVEAL, None, at=539)
    assert early.times(SegmentStarted, segment="result") == [540.0]
    assert late.times(SegmentStarted, segment="result") == [540.0]


# --- The room's verdict -------------------------------------------------------------------


def test_the_verdict_waits_until_both_decisions_are_locked():
    show = Show()
    show.until(482)
    show.desk.choose(LEFT, Choice.REVEAL)
    show.desk.choose(RIGHT, Choice.REVEAL)
    show.until(539.5)
    assert show.cues(ShowVerdict) == [], "both chose, but the window is still open"
    show.until(600)
    result = show.cues(since=540, before=540.5)
    assert result[:4] == [
        PollClosed(),
        SegmentStarted("result"),
        Say(Moment.RESULT, text=script.RESULT_MUTUAL),
        ShowVerdict(yes_share=0.78),
    ]


def test_no_votes_means_no_verdict():
    assert full_date(poll=FakePoll(None)).cues(ShowVerdict) == []


# --- Guards -------------------------------------------------------------------------------


def test_starting_twice_is_a_mistake():
    show = Show()
    with pytest.raises(RuntimeError, match="already started"):
        show.runner.start(1.0)


def test_rehearsal_timing_keeps_the_silence_rule_at_full_length():
    assert REHEARSAL.silence_after == FULL.silence_after
    assert REHEARSAL.time == pytest.approx(FULL.time * 0.3)
