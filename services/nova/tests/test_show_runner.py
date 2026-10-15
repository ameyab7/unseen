"""Nova's show runner, driven on a fake clock. No network, no audio, no model."""

import random

import pytest

from nova import script
from nova.cards import Seat
from nova.cues import (
    AlertModerator,
    DateEnded,
    DecisionWindowOpened,
    EndReason,
    Finished,
    LightningMeterClosed,
    ModeratorAlert,
    Moment,
    PlayFallbackSegment,
    PlaySound,
    PlayVoiceIntro,
    PollClosed,
    PollOpened,
    RoomFacts,
    Say,
    Segment,
    SegmentStarted,
    ShowVerdict,
    Sound,
    StopSpeaking,
)
from nova.decisions import Choice, InMemoryDecisionDesk, Outcome
from nova.distress import Verdict
from nova.fixtures import MAYA, NEXT_PAIR, REHEARSAL_PAIR, THEO
from nova.questions import AudienceQuestion, QuestionStatus
from nova.show_runner import PollTally, ShowRunner
from nova.timing import FULL, REHEARSAL, Timing

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT


class FakePoll:
    def __init__(self, yes: int = 78, no: int = 22):
        self.yes, self.no = yes, no

    def tally(self) -> PollTally:
        return PollTally(yes=self.yes, no=self.no)


def approved(text: str) -> AudienceQuestion:
    return AudienceQuestion(text, QuestionStatus.APPROVED)


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
            first_up=options.pop("first_up", LEFT),
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


def test_whoever_goes_first_leads_the_first_question_and_the_last_word():
    show = full_date(first_up=RIGHT)
    assert [(t, c.about) for t, c in show.says(Moment.FIRST_QUESTION)] == [
        (45.0, RIGHT),
        (67.5, LEFT),
    ]
    assert [(t, c.about) for t, c in show.says(Moment.LAST_WORD)] == [(420.0, RIGHT), (445.0, LEFT)]


def test_who_goes_first_is_random_for_each_date():
    firsts = {
        ShowRunner(
            REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=FakePoll(), rng=random.Random(seed)
        ).first_up
        for seed in range(20)
    }
    assert firsts == {LEFT, RIGHT}


def test_time_opens_the_private_decision_and_the_room_poll():
    show = full_date()
    assert show.cues(since=480, before=480.5) == [
        PlaySound(Sound.TIMES_UP),
        SegmentStarted("time"),
        DecisionWindowOpened(seconds=60.0),
        PollOpened(script.POLL_QUESTION),
        Say(Moment.TIME, text="That's time. Maya and Theo are deciding, privately."),
        Say(Moment.ROOM_TALK, room=RoomFacts(Segment.TIME, poll_open=True, questions_asked=0)),
    ]


def test_rehearsal_timing_runs_a_whole_date_in_about_three_minutes():
    show = Show(timing=REHEARSAL, step=0.25)
    show.until(240)
    assert show.times(Finished) == [171.0]
    assert [segment for _, segment in show.segments()][-2:] == ["result", "next_up"]


def test_the_result_thanks_both_daters_with_an_outro_after_the_verdict():
    show = full_date()
    assert show.cues(since=540, before=540.5) == [
        PollClosed(),
        SegmentStarted("result"),
        Say(Moment.RESULT, text=script.RESULT_NOT_MUTUAL),
        ShowVerdict(yes_share=0.78),
        Say(Moment.OUTRO),
        Say(Moment.INVITATION, text=script.INVITATION),
        DateEnded(EndReason.COMPLETED, Outcome.NOT_MUTUAL),
    ]


def test_a_dater_talking_to_nova_on_air_gets_a_reply():
    show = Show()
    asked = show.at(100, "dater_asked_nova", LEFT, "Nova, are you human?")
    assert asked == [Say(Moment.REPLY, about=LEFT, detail="Nova, are you human?")]


def test_no_reply_while_on_hold_while_deciding_or_after_the_date():
    show = Show()
    show.at(100, "need_a_moment", RIGHT)
    assert show.at(105, "dater_asked_nova", LEFT, "Nova?") == []
    show.at(110, "back", RIGHT)
    # The 10 s hold moved Time from 480 to 490.
    assert show.at(495, "dater_asked_nova", LEFT, "Nova?") == []
    show.until(600)
    assert show.at(650, "dater_asked_nova", LEFT, "Nova?") == []


def test_invitation_to_take_a_seat_at_most_once_a_night():
    assert full_date().says(Moment.INVITATION) != []
    assert full_date(invitation_given_tonight=True).says(Moment.INVITATION) == []


def test_without_a_next_pair_the_show_finishes_after_the_result():
    show = full_date(next_pair=None)
    assert show.says(Moment.NEXT_UP) == []
    assert show.times(Finished) == [570.0]


# --- Long silence during free talk --------------------------------------------------------


def test_a_long_silence_brings_an_open_prompt_to_both_daters_then_waits():
    show = Show()
    show.until(150)
    nudges = show.says(Moment.SILENCE)
    assert [t for t, _ in nudges][:3] == [98.0, 118.0, 138.0]
    assert all(c == Say(Moment.SILENCE) for _, c in nudges), "no dater is singled out"


def test_talking_resets_the_silence_clock():
    show = Show()
    show.at(95, "speech", LEFT)
    show.until(110)
    assert show.says(Moment.SILENCE) == [(103.0, Say(Moment.SILENCE))]


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
    assert unlocked == [
        Say(Moment.LIGHTNING_UNLOCKED, text=script.LIGHTNING_UNLOCKED),
        LightningMeterClosed(),
    ]
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


def test_the_unlock_meter_closes_when_the_lightning_slot_starts():
    assert full_date().times(LightningMeterClosed) == [330.0]


def test_the_unlock_meter_closes_once_the_round_is_unlocked():
    show = Show()
    show.at(200, "unlock_lightning_round")
    show.until(600)
    assert show.times(LightningMeterClosed) == [200.0]


@pytest.mark.parametrize(("event", "args"), [("dater_left", (RIGHT,)), ("moderator_end", ())])
def test_the_unlock_meter_closes_when_the_date_ends_early(event, args):
    show = Show()
    show.at(200, event, *args)
    show.until(600)
    assert show.times(LightningMeterClosed) == [200.0]


# --- From the room ------------------------------------------------------------------------


def test_asks_up_to_two_moderator_approved_questions():
    show = Show()
    show.at(100, "queue_question", approved("What's the best meal you've ever had?"))
    show.at(250, "queue_question", approved("Mountains or sea?"))
    show.at(260, "queue_question", approved("A third one that won't fit"))
    show.until(600)
    assert [(t, c.detail) for t, c in show.says(Moment.FROM_THE_ROOM)] == [
        (240.0, "What's the best meal you've ever had?"),
        (285.0, "Mountains or sea?"),
    ]


@pytest.mark.parametrize(
    "status", [QuestionStatus.SUBMITTED, QuestionStatus.SCREENED, QuestionStatus.REJECTED]
)
def test_a_question_without_moderator_approval_is_never_read(status):
    show = Show()
    show.at(100, "queue_question", AudienceQuestion("Where does Maya work?", status))
    show.at(110, "queue_question", approved("Mountains or sea?"))
    show.until(600)
    spoken = [c.detail or c.text or "" for _, c in show.says()]
    assert not any("Where does Maya work?" in line for line in spoken)
    assert [c.detail for _, c in show.says(Moment.FROM_THE_ROOM)] == ["Mountains or sea?"]


def test_with_only_unapproved_questions_nova_reads_none():
    show = Show()
    show.at(100, "queue_question", AudienceQuestion("Pending", QuestionStatus.SUBMITTED))
    show.at(101, "queue_question", AudienceQuestion("Screened", QuestionStatus.SCREENED))
    show.until(600)
    assert show.says(Moment.FROM_THE_ROOM) == []


# --- Need a moment ------------------------------------------------------------------------


PAUSE_FACTS = RoomFacts(Segment.FREE_TALK, poll_open=False, questions_asked=0)
PAUSE_TALK = Say(Moment.ROOM_TALK, room=PAUSE_FACTS)
COVER = [Say(Moment.NEED_A_MOMENT, text=script.NEED_A_MOMENT), PAUSE_TALK]
WELCOME_BACK = Say(Moment.WELCOME_BACK, text=script.WELCOME_BACK)
BEAR_WITH_US = Say(Moment.BEAR_WITH_US, text=script.BEAR_WITH_US)
NOT_BACK = [AlertModerator(ModeratorAlert.PAUSE_NOT_BACK, LEFT), BEAR_WITH_US]


def paused_and_not_back() -> Show:
    """Maya asks for a moment at 100 s and isn't back by 130 s."""
    show = Show()
    show.at(100, "need_a_moment", LEFT)
    show.until(130)
    return show


def test_need_a_moment_nova_covers_and_the_clock_holds_until_the_dater_is_back():
    show = Show()
    assert show.at(100, "need_a_moment", LEFT) == COVER
    assert "Maya" not in script.NEED_A_MOMENT and "Theo" not in script.NEED_A_MOMENT

    show.until(119.5)
    assert show.cues(since=100.5, before=120) == [], "nothing else happens while Nova covers"
    assert show.at(120, "back", LEFT) == [WELCOME_BACK]
    show.until(300)
    assert (260.0, "from_the_room") in show.segments(), "the running order shifted by 20 s"
    assert show.cues(AlertModerator) == []


def test_only_the_dater_who_asked_can_end_the_pause():
    show = Show()
    show.at(100, "need_a_moment", LEFT)
    assert show.at(105, "back", RIGHT) == []
    assert show.at(110, "back", LEFT) == [WELCOME_BACK]


def test_not_back_after_thirty_seconds_nova_asks_the_room_to_bear_with_us_once():
    show = paused_and_not_back()
    assert show.cues(since=130, before=130.5) == NOT_BACK
    show.until(400)
    assert show.says(Moment.BEAR_WITH_US) == [(130.0, BEAR_WITH_US)], "said once"
    assert show.cues(AlertModerator) == [NOT_BACK[0]], "alerted once"
    assert show.says(Moment.WELCOME_BACK) == [], "nobody came back"
    assert show.segments()[-1] == (90.0, "free_talk"), "the show stays on hold"
    assert show.cues(since=130.5) == []


def test_the_moderator_can_keep_waiting_until_the_dater_is_back():
    show = paused_and_not_back()
    assert show.at(140, "moderator_keep_waiting") == []
    show.until(200)
    assert show.cues(since=130.5) == [], "still on hold, and Nova says nothing more"
    assert show.at(210, "back", LEFT) == [WELCOME_BACK]
    show.until(400)
    assert (350.0, "from_the_room") in show.segments(), "the running order shifted by 110 s"


def test_the_moderator_can_resume_without_the_dater():
    show = paused_and_not_back()
    assert show.at(140, "moderator_resume") == [], "no welcome back: nobody came back"
    assert show.at(150, "back", LEFT) == [], "the pause is over"
    show.until(400)
    assert show.says(Moment.WELCOME_BACK) == []
    assert (280.0, "from_the_room") in show.segments(), "the running order shifted by 40 s"


def test_the_moderator_can_end_the_date_while_waiting():
    show = paused_and_not_back()
    ended = show.at(140, "moderator_end")
    assert ended == [
        StopSpeaking(),
        Say(Moment.MODERATOR_END, text=script.MODERATOR_END),
        LightningMeterClosed(),
        DateEnded(EndReason.MODERATOR_ENDED, outcome=None),
        PlayFallbackSegment(),
        Finished(),
    ]
    assert show.says(Moment.WELCOME_BACK) == []


def test_a_second_pause_by_the_same_dater_alerts_the_moderator_and_there_is_no_limit():
    show = Show()
    show.at(100, "need_a_moment", LEFT)
    show.at(105, "back", LEFT)
    assert show.at(150, "need_a_moment", RIGHT) == COVER, "Theo's first pause: no alert"
    show.at(155, "back", RIGHT)
    second = [*COVER, AlertModerator(ModeratorAlert.REPEATED_PAUSE, LEFT)]
    assert show.at(200, "need_a_moment", LEFT) == second, "Maya's second pause"
    show.at(205, "back", LEFT)
    assert show.at(220, "need_a_moment", LEFT) == second, "and her third"


def test_talk_to_the_room_comes_with_safe_facts_about_the_show():
    show = Show()
    show.at(100, "queue_question", approved("Mountains or sea?"))
    show.at(250, "need_a_moment", LEFT)  # the question was read at 240
    show.at(255, "back", LEFT)
    show.until(600)
    assert [cue.room for _, cue in show.says(Moment.ROOM_TALK)] == [
        RoomFacts(Segment.FROM_THE_ROOM, poll_open=False, questions_asked=1),
        RoomFacts(Segment.TIME, poll_open=True, questions_asked=1),
    ]


def test_need_a_moment_only_on_air_and_one_at_a_time():
    show = Show()
    show.at(100, "need_a_moment", LEFT)
    assert show.at(101, "need_a_moment", RIGHT) == []
    show.at(110, "back", LEFT)
    # The 10 s hold moved Time from 480 to 490, so 520 is inside the decision window.
    assert show.at(520, "need_a_moment", LEFT) == [], "not while the daters decide"


# --- A dater who wants to stop ------------------------------------------------------------


KIND = Say(Moment.NEED_A_MOMENT, text=script.DISTRESS_PAUSE)
DISTRESSED = AlertModerator(ModeratorAlert.DATER_DISTRESSED, LEFT)
MAYBE_DISTRESSED = AlertModerator(ModeratorAlert.POSSIBLE_DISTRESS, LEFT)


@pytest.mark.parametrize(
    "words",
    ["Nova, I want to stop.", "I'm not comfortable.", "Can we end this?", "I need a moment."],
)
def test_a_dater_who_clearly_wants_to_stop_gets_a_kind_line_a_pause_and_the_moderator(words):
    """Nova stops mid-line first and nothing already queued is said: the kind line comes
    at once, never after whatever Nova was saying."""
    show = Show()
    assert show.at(100, "dater_asked_nova", LEFT, words) == [
        DISTRESSED,
        StopSpeaking(),
        KIND,
        PAUSE_TALK,
    ]
    show.until(119.5)
    assert show.cues(since=100.5, before=120) == [], "the show holds, as for a moment"
    assert show.at(120, "back", LEFT) == [WELCOME_BACK]
    show.until(300)
    assert (260.0, "from_the_room") in show.segments(), "the running order shifted by 20 s"


def test_the_kind_line_is_the_one_the_product_owner_approved():
    assert script.DISTRESS_PAUSE == "Of course. Let's take a breather."


def test_nova_never_deflects_a_dater_who_clearly_wants_to_stop():
    show = Show()
    cues = show.at(100, "dater_asked_nova", RIGHT, "Nova, help, can we end this?")
    assert Say(Moment.REPLY, about=RIGHT, detail="Nova, help, can we end this?") not in cues
    assert [c.moment for c in cues if isinstance(c, Say)] == [
        Moment.NEED_A_MOMENT,
        Moment.ROOM_TALK,
    ], "no reply cue, so the model never sees the words and never says it'll stay out of it"


@pytest.mark.parametrize("words", ["Help.", "Nova, help me out, what should I ask Theo?"])
def test_words_the_instant_layer_doesnt_know_get_a_reply_cue(words):
    """The meaning layer checks them outside the runner; the verdict comes back through
    distress_check, and whoever runs the show holds the reply until then."""
    show = Show()
    assert show.at(100, "dater_asked_nova", LEFT, words) == [
        Say(Moment.REPLY, about=LEFT, detail=words)
    ]


def test_a_yes_from_the_distress_check_pauses_as_for_an_unmistakable_request():
    show = Show()
    assert show.at(100, "distress_check", LEFT, Verdict.YES) == [
        DISTRESSED,
        StopSpeaking(),
        KIND,
        PAUSE_TALK,
    ]
    show.until(119.5)
    assert show.cues(since=100.5, before=120) == [], "the show holds, as for a moment"


def test_a_maybe_alerts_the_moderator_and_nothing_else():
    show = Show()
    assert show.at(100, "distress_check", LEFT, Verdict.MAYBE) == [MAYBE_DISTRESSED]
    show.until(300)
    assert show.says(Moment.NEED_A_MOMENT) == [], "no pause"
    assert (240.0, "from_the_room") in show.segments(), "the show clock never stopped"


def test_a_check_that_failed_or_was_too_slow_alerts_the_moderator_to_listen_in():
    show = Show()
    assert show.at(100, "distress_check", LEFT, Verdict.UNCHECKED) == [
        AlertModerator(ModeratorAlert.DISTRESS_UNCHECKED, LEFT)
    ]


def test_a_no_changes_nothing():
    show = Show()
    assert show.at(100, "distress_check", LEFT, Verdict.NO) == []


@pytest.mark.parametrize("words", ["I want to stop.", "Theo, I'm not comfortable."])
def test_a_dater_who_wants_to_stop_is_heard_even_when_not_talking_to_nova(words):
    show = Show()
    assert show.at(100, "dater_said", LEFT, words) == [DISTRESSED, StopSpeaking(), KIND, PAUSE_TALK]
    show.until(119.5)
    assert show.cues(since=100.5, before=120) == [], "the show holds, as for a moment"


def test_words_not_to_nova_that_the_instant_layer_doesnt_know_are_left_to_the_meaning_layer():
    show = Show()
    assert show.at(100, "dater_said", LEFT, "Honestly I'm scared.") == []


def test_ordinary_words_not_to_nova_change_nothing():
    show = Show()
    assert show.at(100, "dater_said", LEFT, "I think tea, honestly.") == []
    show.until(300)
    assert (240.0, "from_the_room") in show.segments(), "the show clock never stopped"


def test_after_a_distress_pause_the_usual_pause_rules_apply():
    show = Show()
    show.at(100, "dater_asked_nova", LEFT, "I'm not comfortable.")
    show.until(130)
    assert show.cues(since=130, before=130.5) == NOT_BACK, "not back in 30 s"
    show.at(140, "back", LEFT)
    assert show.at(200, "dater_asked_nova", LEFT, "I need a moment.") == [
        DISTRESSED,
        StopSpeaking(),
        KIND,
        PAUSE_TALK,
        AlertModerator(ModeratorAlert.REPEATED_PAUSE, LEFT),
    ]


def test_when_nova_cant_pause_or_answer_it_stays_quiet_but_the_moderator_is_always_alerted():
    show = Show()
    show.at(100, "need_a_moment", RIGHT)
    assert show.at(105, "dater_asked_nova", LEFT, "I want to stop.") == [DISTRESSED], "a pause"
    assert show.at(106, "distress_check", LEFT, Verdict.YES) == [DISTRESSED], "a pause"
    assert show.at(107, "distress_check", LEFT, Verdict.MAYBE) == [MAYBE_DISTRESSED]
    assert show.at(108, "dater_asked_nova", LEFT, "Help.") == [], "no reply on a pause"
    show.at(110, "back", RIGHT)
    # The 10 s hold moved Time from 480 to 490 and the result from 540 to 550.
    assert show.at(495, "dater_asked_nova", LEFT, "Can we end this?") == [DISTRESSED], "deciding"
    assert show.at(496, "distress_check", LEFT, Verdict.YES) == [DISTRESSED], "deciding"
    show.until(560)
    assert show.at(560, "dater_asked_nova", LEFT, "Please stop.") == [DISTRESSED], (
        "after the result"
    )
    show.until(700)
    assert show.at(700, "dater_asked_nova", LEFT, "Please stop.") == [DISTRESSED], "after the show"


# --- A dater leaves -----------------------------------------------------------------------


BLAME = ("left", "quit", "walked", "reject", "fault", "sorry", "because", "reason", "decided")


def test_a_dater_leaving_is_closed_kindly_and_the_date_ends_as_dater_left():
    show = Show()
    closed = show.at(200, "dater_left", RIGHT)
    line = "Theo's had to step away, and that's always allowed. Maya, thank you."
    assert closed == [
        StopSpeaking(),
        Say(Moment.DATER_LEFT, text=line),
        LightningMeterClosed(),
        DateEnded(EndReason.DATER_LEFT, outcome=None),
    ]
    assert not any(word in line.lower() for word in BLAME)

    show.until(600)
    assert show.segments()[-1] == (230.0, "next_up"), "the show moves on to the next pair"
    assert show.cues(ShowVerdict) == []
    assert show.says(Moment.RESULT) == [] and show.says(Moment.INVITATION) == []
    assert show.says(Moment.OUTRO) == []
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


def test_leaving_during_a_moment_releases_the_hold_with_no_alert():
    show = Show()
    show.at(100, "need_a_moment", LEFT)
    show.at(110, "dater_left", LEFT)
    show.until(200)
    assert show.says(Moment.WELCOME_BACK) == []
    assert show.cues(AlertModerator) == []
    assert show.segments()[-1] == (140.0, "next_up")


# --- The moderator ends the date ----------------------------------------------------------


def test_moderator_end_stops_nova_at_once_with_only_the_approved_line_then_the_fallback():
    assert script.MODERATOR_END == "We're ending this date here. Thanks for listening, everyone."
    show = Show()
    show.at(100, "queue_question", approved("Saved for later"))
    ended = show.at(300, "moderator_end")
    assert ended == [
        StopSpeaking(),
        Say(Moment.MODERATOR_END, text=script.MODERATOR_END),
        LightningMeterClosed(),
        DateEnded(EndReason.MODERATOR_ENDED, outcome=None),
        PlayFallbackSegment(),
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
        "PlayFallbackSegment",
        "Finished",
    ]
    assert show.desk.resolved is False
    assert show.cues(ShowVerdict) == []


def test_moderator_end_after_the_result_stops_nova_and_plays_the_fallback():
    show = Show()
    show.until(550)
    assert show.at(555, "moderator_end") == [StopSpeaking(), PlayFallbackSegment(), Finished()]
    show.until(700)
    assert show.says(Moment.NEXT_UP) == []


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


@pytest.mark.parametrize(
    ("yes", "no", "verdict"),
    [(0, 0, None), (3, 1, None), (4, 1, ShowVerdict(yes_share=0.8)), (0, 5, ShowVerdict(0.0))],
)
def test_the_verdict_is_hidden_when_fewer_than_five_people_voted(yes, no, verdict):
    shown = full_date(poll=FakePoll(yes, no)).cues(ShowVerdict)
    assert shown == ([verdict] if verdict else [])


# --- Guards -------------------------------------------------------------------------------


def test_starting_twice_is_a_mistake():
    show = Show()
    with pytest.raises(RuntimeError, match="already started"):
        show.runner.start(1.0)


def test_rehearsal_timing_keeps_the_silence_rule_at_full_length():
    assert REHEARSAL.silence_after == FULL.silence_after
    assert REHEARSAL.time == pytest.approx(FULL.time * 0.3)
