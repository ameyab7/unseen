"""Nova's show runner, driven on a fake clock. No network, no audio, no model."""

import dataclasses
import random

import pytest

from nova import running_order, script
from nova.cards import Seat
from nova.cues import (
    AlertModerator,
    BeatFailed,
    Conduct,
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
    QuestionSkipped,
    RoomFacts,
    Say,
    Segment,
    SegmentStarted,
    ShowVerdict,
    Situation,
    Sound,
    StopFallbackSegment,
    StopSpeaking,
)
from nova.decisions import Choice, DecisionError, InMemoryDecisionDesk, Outcome
from nova.distress import Verdict
from nova.fixtures import MAYA, NEXT_PAIR, REHEARSAL_PAIR, THEO
from nova.questions import AudienceQuestion, OffLimits, QuestionStatus
from nova.show_runner import TURN_MOMENTS, PollTally, ShowRunner, rounded_share
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


def replace_timing(**changes) -> Timing:
    return dataclasses.replace(FULL, **changes)


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
        Say(Moment.ROOM_TALK, room=RoomFacts(Segment.TIME, True, 0, Situation.DECIDING)),
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
        ShowVerdict(yes_share=0.8),
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


def test_talking_resets_the_silence_clock_which_counts_from_when_they_stop():
    show = Show()
    show.at(95, "speech_started", LEFT)
    show.at(97, "speech_stopped", LEFT)
    show.until(110)
    assert show.says(Moment.SILENCE) == [(105.0, Say(Moment.SILENCE))]


def test_no_silence_prompt_comes_while_a_dater_is_still_speaking():
    show = Show()
    show.at(95, "speech_started", LEFT)
    show.until(120)
    assert show.says(Moment.SILENCE) == [], "a long story is not a silence"
    show.at(120, "speech_stopped", LEFT)
    show.until(130)
    assert show.says(Moment.SILENCE) == [(128.0, Say(Moment.SILENCE))]


# --- Nova never talks over a dater -----------------------------------------------------------


def test_a_beat_waits_while_a_dater_is_mid_sentence_and_goes_out_when_they_stop():
    show = Show()
    show.at(40, "speech_started", LEFT)
    show.until(46)
    assert (45.0, "first_question") in show.segments(), "the segment starts on time"
    assert show.says(Moment.FIRST_QUESTION) == []
    show.at(47, "speech_stopped", LEFT)
    assert [t for t, _ in show.says(Moment.FIRST_QUESTION)] == [47.0]


def test_a_beat_goes_out_after_the_grace_if_the_dater_keeps_talking():
    show = Show()
    show.at(44, "speech_started", LEFT)
    show.until(60)
    assert [t for t, _ in show.says(Moment.FIRST_QUESTION)] == [50.0], "5 s after it was due"


def test_the_times_up_sound_and_line_wait_while_the_decision_window_opens_on_time():
    show = Show()
    show.at(479, "speech_started", RIGHT)
    show.until(481)
    assert show.times(DecisionWindowOpened) == [480.0], "the window opens on time"
    assert show.cues(PollOpened) and show.cues(PlaySound, since=470) == []
    assert show.says(Moment.TIME) == []
    show.at(482, "speech_stopped", RIGHT)
    assert show.cues(PlaySound, since=470) == [PlaySound(Sound.TIMES_UP)]
    assert [t for t, _ in show.says(Moment.TIME)] == [482.0]


def test_an_unlock_while_a_dater_is_speaking_is_announced_after_they_stop():
    show = Show()
    show.at(200, "speech_started", LEFT)
    unlocked = show.at(201, "unlock_lightning_round")
    assert unlocked == [LightningMeterClosed()], "the meter closes at once"
    stopped = show.at(203, "speech_stopped", LEFT)
    assert Say(Moment.LIGHTNING_UNLOCKED, text=script.LIGHTNING_UNLOCKED) in stopped


def test_safety_lines_never_wait_for_a_dater_to_finish():
    show = Show()
    show.at(100, "speech_started", RIGHT)  # Theo is mid-sentence
    cues = show.at(101, "dater_said", LEFT, "I want to stop.")
    assert Say(Moment.NEED_A_MOMENT, text=script.DISTRESS_PAUSE) in cues
    assert StopSpeaking() in cues


def test_the_result_never_waits():
    show = Show()
    show.at(539, "speech_started", LEFT)
    show.until(541)
    assert [t for t, _ in show.says(Moment.RESULT)] == [540.0]


def test_what_waited_is_dropped_when_a_dater_leaves():
    show = Show()
    show.at(44, "speech_started", LEFT)
    show.until(46)
    show.at(46, "dater_left", RIGHT)
    show.until(80)
    assert show.says(Moment.FIRST_QUESTION) == [], "never said after the date ended"


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
    assert [(t, c.number, c.of) for t, c in show.says(Moment.LIGHTNING_QUESTION)] == [
        (330.0, 1, 3),
        (360.0, 2, 3),
        (390.0, 3, 3),
    ]


def test_the_runner_asks_as_many_lightning_questions_as_the_running_order_says(monkeypatch):
    """With a gap that fits them all before the last word."""
    monkeypatch.setattr(running_order, "LIGHTNING_QUESTIONS", 4)
    show = Show(timing=replace_timing(lightning_gap=20.0))
    show.at(200, "unlock_lightning_round")
    show.until(600)
    asked = show.says(Moment.LIGHTNING_QUESTION)
    assert [(c.number, c.of) for _, c in asked] == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert all(t < FULL.last_word for t, _ in asked), "each gets its gap before the last word"


def test_lightning_questions_that_dont_fit_before_the_last_word_are_refused(monkeypatch):
    """A fourth question 30 s apart would land on the last word, and a fifth be dropped."""
    monkeypatch.setattr(running_order, "LIGHTNING_QUESTIONS", 4)
    with pytest.raises(ValueError, match="lightning questions"):
        Show()


def test_the_approved_unlock_line_says_how_many_questions_there_are():
    """The line was signed off for three: changing the count needs a newly approved line."""
    assert running_order.LIGHTNING_QUESTIONS == 3 and "Three" in script.LIGHTNING_UNLOCKED


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


def test_a_question_the_screen_catches_is_skipped_silently_and_the_next_one_read():
    """Moderator approval is the main gate; the screen is a silent backstop on air. A
    spoken decline would tell the daters and the room a listener asked something off
    limits, so the console gets a code and the room hears the next question instead."""
    show = Show()
    show.at(100, "queue_question", approved("Maya, what does Theo look like?"))
    show.at(101, "queue_question", approved("Mountains or sea?"))
    show.until(300)
    assert [(t, c.detail) for t, c in show.says(Moment.FROM_THE_ROOM)] == [
        (240.0, "Mountains or sea?")
    ]
    assert show.cues(QuestionSkipped) == [QuestionSkipped(OffLimits.LOOKS)]
    spoken = [c.detail or c.text or "" for _, c in show.says()]
    assert not any("look like" in line for line in spoken)


def test_with_only_unapproved_questions_nova_reads_none():
    show = Show()
    show.at(100, "queue_question", AudienceQuestion("Pending", QuestionStatus.SUBMITTED))
    show.at(101, "queue_question", AudienceQuestion("Screened", QuestionStatus.SCREENED))
    show.until(600)
    assert show.says(Moment.FROM_THE_ROOM) == []


# --- Need a moment ------------------------------------------------------------------------


PAUSE_FACTS = RoomFacts(Segment.FREE_TALK, False, 0, Situation.PAUSE)
PAUSE_TALK = Say(Moment.ROOM_TALK, room=PAUSE_FACTS, number=1, of=3)  # a hold's first line


def cover_line(n: int) -> Say:
    """A hold's nth line to the room: every 10 s, at most 3."""
    return Say(Moment.ROOM_TALK, room=PAUSE_FACTS, number=n, of=3)


COVER = [Say(Moment.NEED_A_MOMENT, text=script.NEED_A_MOMENT), PAUSE_TALK]
MOMENT = [StopSpeaking(), *COVER]  # a dater's own moment: Nova stops mid-line first
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
    assert show.at(100, "need_a_moment", LEFT) == MOMENT
    assert "Maya" not in script.NEED_A_MOMENT and "Theo" not in script.NEED_A_MOMENT

    show.until(114.5)
    assert show.cues(since=100.5, before=115) == [cover_line(2)], "Nova covers every 10 s"
    assert show.at(115, "back", LEFT) == [WELCOME_BACK]
    show.until(300)
    assert (255.0, "from_the_room") in show.segments(), "the running order shifted by 15 s"
    assert show.cues(AlertModerator) == []


def test_only_the_dater_who_asked_can_end_the_pause():
    show = Show()
    show.at(100, "need_a_moment", LEFT)
    assert show.at(105, "back", RIGHT) == []
    assert show.at(110, "back", LEFT) == [WELCOME_BACK]


def test_not_back_after_thirty_seconds_nova_asks_the_room_to_bear_with_us_once():
    show = paused_and_not_back()
    assert show.cues(since=100.5, before=130) == [cover_line(2), cover_line(3)]
    assert show.cues(since=130, before=130.5) == [*NOT_BACK, PlayFallbackSegment()]
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
    assert show.cues(since=130.5) == [], "still on hold: the fallback segment plays"
    assert show.at(210, "back", LEFT) == [StopFallbackSegment(), WELCOME_BACK]
    show.until(400)
    assert (350.0, "from_the_room") in show.segments(), "the running order shifted by 110 s"


def test_the_moderator_can_resume_without_the_dater_and_nova_welcomes_them_back_later():
    """Before, a dater who came back after the moderator resumed got nothing, and nothing
    marked their return. "And we're back." is true then, so Nova says it (decision 0004)."""
    show = paused_and_not_back()
    resumed = show.at(140, "moderator_resume")
    assert resumed == [StopFallbackSegment()], "no welcome back: nobody came back"
    assert show.at(150, "back", LEFT) == [WELCOME_BACK]
    assert show.at(160, "back", LEFT) == [], "once"
    show.until(400)
    assert (280.0, "from_the_room") in show.segments(), "the running order shifted by 40 s"


def resumed_without_maya() -> Show:
    """Maya (first up) pauses at 100 s and isn't back; the moderator resumes at 140 s, so
    the running order runs 40 s late."""
    show = paused_and_not_back()
    show.at(140, "moderator_resume")
    return show


def test_while_a_dater_is_away_their_last_word_is_skipped_and_the_others_comes_on_time():
    show = resumed_without_maya()
    show.until(600)
    assert [(t, c.about) for t, c in show.says(Moment.LAST_WORD)] == [(485.0, RIGHT)]


def test_while_a_dater_is_away_their_first_question_is_skipped():
    show = Show()
    show.at(10, "need_a_moment", LEFT)  # during the doors
    show.until(40)
    show.at(40, "moderator_resume")
    show.until(120)
    assert [(t, c.about) for t, c in show.says(Moment.FIRST_QUESTION)] == [(97.5, RIGHT)]
    assert [c.away for _, c in show.says(Moment.FIRST_QUESTION)] == [LEFT]


def test_while_a_dater_is_away_the_others_last_word_is_marked_so_it_never_names_them():
    """Before, Theo's last word, written before the date, asked what he hoped Maya would
    discover about him while she was away (the scenario run)."""
    show = resumed_without_maya()
    show.until(600)
    assert [(c.about, c.away) for _, c in show.says(Moment.LAST_WORD)] == [(RIGHT, LEFT)]


def test_with_both_daters_on_air_no_turn_is_marked():
    show = Show()
    show.until(600)
    turns = [*show.says(Moment.FIRST_QUESTION), *show.says(Moment.LAST_WORD)]
    assert len(turns) == 4 and all(c.away is None for _, c in turns)


def test_while_a_dater_is_away_a_long_silence_brings_a_prompt_for_the_one_whos_here():
    show = resumed_without_maya()
    show.until(170)
    after = [(t, c) for t, c in show.says(Moment.SILENCE) if t >= 140]
    assert after == [(158.0, Say(Moment.SILENCE, about=RIGHT))], "20 s after the one at 98 s"


def test_once_the_dater_is_back_their_turns_and_both_daters_silences_return():
    show = resumed_without_maya()
    show.at(200, "back", LEFT)
    show.until(600)
    assert [c.about for _, c in show.says(Moment.LAST_WORD)] == [LEFT, RIGHT]
    silences = [c for t, c in show.says(Moment.SILENCE) if t > 200]
    assert silences and all(c.about is None for c in silences)


def test_a_return_after_the_date_has_ended_changes_nothing():
    show = resumed_without_maya()
    show.until(700)
    assert show.runner.finished
    assert show.at(700, "back", LEFT) == []


def test_after_a_report_the_reporter_isnt_welcomed_back_their_date_ended_in_the_app():
    show = Show()
    show.at(100, "dater_reported", LEFT)
    show.at(110, "moderator_resume")
    assert show.at(120, "back", LEFT) == []


def test_nova_covers_a_hold_every_ten_seconds_three_lines_at_most_then_the_segment_plays():
    show = Show(timing=replace_timing(cover_every=8.0))
    show.at(100, "need_a_moment", LEFT)
    show.until(129.5)
    assert [t for t, _ in show.says(Moment.ROOM_TALK)] == [100.0, 108.0, 116.0], "three lines"
    assert show.times(PlayFallbackSegment) == [124.0], "once the lines run out"
    show.until(140)
    assert show.times(PlayFallbackSegment) == [124.0], "it plays on, started once"


def test_the_fallback_segment_stops_before_nova_speaks_again():
    show = paused_and_not_back()
    back = show.at(150, "back", LEFT)
    assert back == [StopFallbackSegment(), WELCOME_BACK], "stopped first, then Nova"


def test_a_hold_that_ends_before_the_segment_never_plays_or_stops_it():
    show = Show()
    show.at(100, "need_a_moment", LEFT)
    assert show.at(105, "back", LEFT) == [WELCOME_BACK]
    assert show.cues(PlayFallbackSegment) == show.cues(StopFallbackSegment) == []


def test_the_moderator_can_end_the_date_while_waiting():
    show = paused_and_not_back()
    ended = show.at(140, "moderator_end")
    assert ended == [
        StopFallbackSegment(),
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
    assert show.at(150, "need_a_moment", RIGHT) == MOMENT, "Theo's first pause: no alert"
    show.at(155, "back", RIGHT)
    second = [*MOMENT, AlertModerator(ModeratorAlert.REPEATED_PAUSE, LEFT)]
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
        RoomFacts(Segment.FROM_THE_ROOM, False, 1, Situation.PAUSE),
        RoomFacts(Segment.TIME, True, 1, Situation.DECIDING),
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
    assert show.cues(since=100.5, before=120) == [cover_line(2)], "held, as for a moment"
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
    assert show.cues(since=100.5, before=120) == [cover_line(2)], "held, as for a moment"


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
    assert show.cues(since=100.5, before=120) == [cover_line(2)], "held, as for a moment"


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
    assert show.cues(since=130, before=130.5) == [*NOT_BACK, PlayFallbackSegment()]
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
        Say(Moment.ROOM_TALK, room=RoomFacts(Segment.FREE_TALK, False, 0, Situation.ENDED_EARLY)),
    ]
    assert not any(word in line.lower() for word in BLAME)

    show.until(600)
    assert show.segments()[-1] == (212.0, "next_up"), "the next pair 12 s later, not 30"
    assert show.cues(ShowVerdict) == []
    assert show.says(Moment.RESULT) == [] and show.says(Moment.INVITATION) == []
    assert show.says(Moment.OUTRO) == []
    assert show.runner.end_reason is EndReason.DATER_LEFT
    assert show.runner.outcome is None


def test_a_dater_leaving_with_no_next_pair_hands_over_to_the_fallback_segment():
    show = Show(next_pair=None)
    show.at(200, "dater_left", RIGHT)
    show.until(600)
    assert show.times(PlayFallbackSegment) == [212.0], "no dead air after the leave"
    assert show.runner.finished


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
    assert show.segments()[-1] == (122.0, "next_up")


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
        (301, "speech_started", (LEFT,)),
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
        ShowVerdict(yes_share=0.8),
    ]


@pytest.mark.parametrize(
    ("yes", "no", "verdict"),
    [
        (0, 0, None),
        (4, 1, None),
        (15, 4, None),
        (16, 4, ShowVerdict(yes_share=0.8)),
        (0, 20, ShowVerdict(0.0)),
    ],
)
def test_the_verdict_is_hidden_when_fewer_than_twenty_people_voted(yes, no, verdict):
    """A share of a handful of votes is noise, and the daters see it again in their recap."""
    shown = full_date(poll=FakePoll(yes, no)).cues(ShowVerdict)
    assert shown == ([verdict] if verdict else [])


@pytest.mark.parametrize(
    ("yes", "total", "share"),
    [
        (78, 100, 0.8),
        (75, 100, 0.8),
        (74, 100, 0.7),
        (81, 100, 0.8),
        (5, 100, 0.1),
        (4, 100, 0.0),
        (100, 100, 1.0),
        (13, 20, 0.7),
        (0, 20, 0.0),
    ],
)
def test_the_verdict_is_rounded_to_the_nearest_ten_percent(yes, total, share):
    assert rounded_share(yes, total) == share


# --- Guards -------------------------------------------------------------------------------


# --- A dater reports ----------------------------------------------------------------------

REPORTED = AlertModerator(ModeratorAlert.DATER_REPORTED, LEFT)
MOMENT_LINE = Say(Moment.NEED_A_MOMENT, text=script.NEED_A_MOMENT)
REPEATED = AlertModerator(ModeratorAlert.REPEATED_PAUSE, LEFT)


def test_a_report_alerts_the_moderator_first_then_pauses_the_date_naming_nobody():
    """The prototype's report sheet: "Nova pauses the date and a live moderator listens in."
    The need-a-moment cover, not the kind line: nothing on air answers or names the report."""
    show = Show()
    assert show.at(100, "dater_reported", LEFT) == [REPORTED, StopSpeaking(), *COVER]
    show.until(130)
    assert show.cues(since=100.5, before=130) == [cover_line(2), cover_line(3)], "it holds"
    assert show.cues(since=130, before=130.5) == [*NOT_BACK, PlayFallbackSegment()]


def test_beats_already_due_come_out_before_a_report():
    show = Show()
    show.until(44.5)
    cues = show.runner.dater_reported(45.0, LEFT)  # the first question is due at 45 s
    report = cues.index(REPORTED)
    assert Moment.FIRST_QUESTION in [c.moment for c in cues[:report] if isinstance(c, Say)]
    assert cues[report : report + 3] == [REPORTED, StopSpeaking(), MOMENT_LINE]
    assert cues[report + 3].moment is Moment.ROOM_TALK


def test_the_reporters_own_back_never_ends_the_pause():
    """Their date ends in the app; only the moderator decides what happens next."""
    show = Show()
    show.at(100, "dater_reported", LEFT)
    assert show.at(110, "back", LEFT) == []
    show.until(300)
    assert show.says(Moment.WELCOME_BACK) == []
    assert show.segments()[-1] == (90.0, "free_talk"), "still on hold"


def test_after_a_report_the_moderator_resumes_without_the_dater():
    show = Show()
    show.at(100, "dater_reported", LEFT)
    assert show.at(120, "moderator_resume") == []
    show.until(300)
    assert (260.0, "from_the_room") in show.segments(), "the running order shifted by 20 s"
    assert show.says(Moment.WELCOME_BACK) == []


def test_after_a_report_the_moderator_can_end_the_date():
    show = Show()
    show.at(100, "dater_reported", LEFT)
    cues = show.at(120, "moderator_end")
    assert cues[0] == StopSpeaking()
    assert Say(Moment.MODERATOR_END, text=script.MODERATOR_END) in cues
    assert show.runner.end_reason is EndReason.MODERATOR_ENDED


def test_a_report_never_counts_as_a_repeated_pause():
    show = Show()
    show.at(100, "dater_reported", LEFT)
    show.at(110, "moderator_resume")
    assert show.at(150, "need_a_moment", LEFT) == MOMENT, "the dater's first own pause"
    show.at(160, "back", LEFT)
    show.at(200, "dater_reported", LEFT)
    show.at(210, "moderator_resume")
    assert show.at(230, "need_a_moment", LEFT) == [*MOMENT, REPEATED], "their second own pause"
    assert show.cues(AlertModerator).count(REPEATED) == 1


def test_a_report_while_the_other_dater_is_on_a_pause_only_alerts():
    show = Show()
    show.at(100, "need_a_moment", RIGHT)
    assert show.at(105, "dater_reported", LEFT) == [REPORTED]


def test_a_report_while_the_daters_decide_only_alerts():
    show = Show()
    show.until(500)  # Time is at 480 s: the daters are deciding, off air
    assert show.at(500, "dater_reported", LEFT) == [REPORTED]


# --- Before the date starts and after it ends ----------------------------------------------

UNCHECKED = AlertModerator(ModeratorAlert.DISTRESS_UNCHECKED, LEFT)


def not_started() -> ShowRunner:
    return ShowRunner(
        REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=FakePoll(), next_pair=NEXT_PAIR,
        first_up=LEFT,
    )  # fmt: skip


def finished() -> ShowRunner:
    runner = full_date().runner
    assert runner.finished
    return runner


def run(runner: ShowRunner, until: float = 600.0, step: float = 0.5) -> list:
    """Starts the date at 0 s and ticks it: every (time, cue)."""
    log, now = [(0.0, cue) for cue in runner.start(0.0)], 0.0
    while now + step <= until:
        now = round(now + step, 6)
        log += [(now, cue) for cue in runner.tick(now)]
    return log


ANY_TIME = [
    ("dater_said", (LEFT, "I want to stop."), [DISTRESSED]),
    ("dater_said", (LEFT, "I think tea, honestly."), []),
    ("dater_asked_nova", (LEFT, "Nova, I want to stop."), [DISTRESSED]),
    ("dater_asked_nova", (LEFT, "Nova, tea or coffee?"), []),
    ("distress_check", (LEFT, Verdict.YES), [DISTRESSED]),
    ("distress_check", (LEFT, Verdict.MAYBE), [MAYBE_DISTRESSED]),
    ("distress_check", (LEFT, Verdict.UNCHECKED), [UNCHECKED]),
    ("distress_check", (LEFT, Verdict.NO), []),
    ("dater_reported", (LEFT,), [REPORTED]),
    ("speech_started", (LEFT,), []),
    ("speech_stopped", (LEFT,), []),
    ("need_a_moment", (LEFT,), []),
    ("back", (LEFT,), []),
    ("moderator_keep_waiting", (), []),
    ("moderator_resume", (), []),
]


@pytest.mark.parametrize(("method", "args", "expected"), ANY_TIME)
@pytest.mark.parametrize(("when", "now"), [("before the start", 5.0), ("after the end", 900.0)])
def test_off_air_alerts_still_reach_the_moderator_and_nothing_else_happens(
    method, args, expected, when, now
):
    runner = not_started() if when == "before the start" else finished()
    assert getattr(runner, method)(now, *args) == expected


def test_nothing_before_the_start_changes_the_date():
    runner = not_started()
    for method, args, _ in ANY_TIME:
        getattr(runner, method)(5.0, *args)
    log = run(runner)
    assert not runner.finished or runner.end_reason is EndReason.COMPLETED
    assert [cue for _, cue in log if isinstance(cue, AlertModerator)] == []
    assert [s for _, s in log if isinstance(s, SegmentStarted)][0] == SegmentStarted(Segment.DOORS)


def test_an_approved_question_before_the_start_is_kept_for_the_date():
    runner = not_started()
    assert runner.queue_question(5.0, approved("Mountains or sea?")) == []
    asked = [
        (t, c.detail) for t, c in run(runner) if getattr(c, "moment", None) is Moment.FROM_THE_ROOM
    ]
    assert asked == [(240.0, "Mountains or sea?")]


def test_an_unlock_before_the_start_is_kept_and_announced_after_the_doors_intro():
    """A paid unlock is never lost (decision 0004). The meter closes at once."""
    runner = not_started()
    assert runner.unlock_lightning_round(5.0) == [LightningMeterClosed()]
    log = run(runner)
    doors = [cue for t, cue in log if t == 0.0]
    assert doors[-1] == Say(Moment.LIGHTNING_UNLOCKED, text=script.LIGHTNING_UNLOCKED)
    assert isinstance(doors[-2], PlayVoiceIntro)
    questions = [t for t, c in log if getattr(c, "moment", None) is Moment.LIGHTNING_QUESTION]
    assert questions == [330.0, 360.0, 390.0]
    assert LightningMeterClosed() not in [cue for _, cue in log], "closed once, before the date"


def test_ticking_before_the_start_is_still_a_mistake():
    with pytest.raises(RuntimeError, match="start"):
        not_started().tick(5.0)


def test_starting_twice_is_a_mistake():
    show = Show()
    with pytest.raises(RuntimeError, match="already started"):
        show.runner.start(1.0)


def test_rehearsal_timing_keeps_the_silence_rule_at_full_length():
    assert REHEARSAL.silence_after == FULL.silence_after
    assert REHEARSAL.time == pytest.approx(FULL.time * 0.3)


# --- Review of main at bfaed17: holds that overlap, and the edges of a date --------------------

NEED_MOMENT = Say(Moment.NEED_A_MOMENT, text=script.NEED_A_MOMENT)

ENDED_BEFORE = [LightningMeterClosed(), PlayFallbackSegment(), Finished()]


@pytest.mark.parametrize(
    ("method", "args", "reason"),
    [
        ("dater_left", (LEFT,), EndReason.DATER_LEFT),
        ("moderator_end", (), EndReason.MODERATOR_ENDED),
    ],
)
def test_a_leave_or_an_end_before_the_start_ends_the_date_at_once(method, args, reason):
    """The moderator's end is never lost: a dater who asked to stop during prep is never
    introduced. Nothing has aired, so Nova says nothing and names nobody."""
    runner = not_started()
    cues = getattr(runner, method)(5.0, *args)
    assert [c for c in cues if not isinstance(c, DateEnded)] == ENDED_BEFORE
    assert DateEnded(reason, None) in cues and runner.finished
    assert run(runner) == [], "start() says nothing"
    assert getattr(runner, method)(10.0, *args) == []


@pytest.mark.parametrize(("method", "args"), [("dater_left", (LEFT,)), ("moderator_end", ())])
def test_after_the_end_a_leave_or_an_end_does_nothing(method, args):
    assert getattr(finished(), method)(900.0, *args) == []


def test_a_report_during_the_other_daters_moment_is_never_forgotten():
    """The other dater's return doesn't lift it: only the moderator does, and the reporter
    stays off air."""
    show = Show()
    show.until(100)
    show.at(100, "need_a_moment", RIGHT)
    assert show.at(105, "dater_reported", LEFT) == [REPORTED]
    assert show.at(108, "back", RIGHT) == [], "back, but the hold is the report's now"
    assert show.runner._held_since is not None
    show.at(125, "moderator_resume")
    show.until(600)
    assert not show.says(Moment.WELCOME_BACK)
    assert [say.about for _, say in show.says(Moment.LAST_WORD)] == [RIGHT]


def test_a_report_during_a_conduct_pause_keeps_the_reporter_off_air():
    show = Show()
    show.until(100)
    show.at(100, "dater_conduct", RIGHT, Conduct.UNKIND)
    show.at(102, "dater_conduct", RIGHT, Conduct.UNKIND)  # the whole date holds
    show.at(105, "dater_reported", LEFT)
    show.at(110, "moderator_resume")
    assert show.at(115, "back", LEFT) == []
    show.until(600)
    assert [say.about for _, say in show.says(Moment.LAST_WORD)] == [RIGHT]


def test_a_moment_asked_for_during_another_hold_starts_when_that_hold_ends():
    show = Show()
    show.until(100)
    show.at(100, "need_a_moment", LEFT)
    assert show.at(103, "need_a_moment", RIGHT) == []
    back = show.at(108, "back", LEFT)
    assert NEED_MOMENT in back and WELCOME_BACK not in back, "Theo's own pause, not and we're back"
    assert show.runner._away is RIGHT and show.runner._held_since is not None
    show.until(108 + FULL.moment_alert_after)
    alerts = show.cues(AlertModerator, since=108)
    assert AlertModerator(ModeratorAlert.PAUSE_NOT_BACK, RIGHT) in alerts
    assert show.at(150, "back", RIGHT)[-1] == WELCOME_BACK


def test_a_moment_asked_for_during_a_conduct_pause_starts_when_the_moderator_resumes():
    show = Show()
    show.until(100)
    show.at(100, "dater_conduct", RIGHT, Conduct.UNKIND)
    show.at(102, "dater_conduct", RIGHT, Conduct.UNKIND)
    show.at(104, "need_a_moment", LEFT)
    assert NEED_MOMENT in show.at(110, "moderator_resume")
    assert show.at(115, "back", LEFT)[-1] == WELCOME_BACK


def test_a_dater_who_asked_for_a_moment_and_is_back_before_it_started_gets_none():
    show = Show()
    show.until(100)
    show.at(100, "need_a_moment", LEFT)
    show.at(103, "need_a_moment", RIGHT)
    show.at(105, "back", RIGHT)
    assert show.at(108, "back", LEFT)[-1] == WELCOME_BACK


def test_the_lightning_unlock_is_never_announced_over_a_pause():
    show = Show()
    show.until(200)
    show.at(200, "dater_said", LEFT, "I want to stop.")
    assert show.at(205, "unlock_lightning_round") == [LightningMeterClosed()]
    assert not show.says(Moment.LIGHTNING_UNLOCKED), "nothing while the dater is away"
    assert show.at(215, "back", LEFT)[-1] == WELCOME_BACK
    show.until(216)
    unlocked = show.says(Moment.LIGHTNING_UNLOCKED)
    assert [t for t, _ in unlocked] == [215.5], "once the date is on air again"
    show.until(600)
    assert show.says(Moment.LIGHTNING_QUESTION)[0][0] == pytest.approx(330 + 15)


def test_an_unlock_during_a_hold_is_never_said_if_a_dater_then_leaves():
    show = Show()
    show.until(200)
    show.at(200, "need_a_moment", LEFT)
    show.at(205, "unlock_lightning_round")
    show.at(210, "dater_left", LEFT)
    show.until(600)
    assert not show.says(Moment.LIGHTNING_UNLOCKED)


def test_and_were_back_waits_for_a_hold_that_is_still_running():
    show = resumed_without_maya()
    show.at(200, "need_a_moment", RIGHT)
    assert show.at(205, "back", LEFT) == [], "Theo is still away"
    assert show.at(210, "back", RIGHT)[-1] == WELCOME_BACK
    show.until(600)
    assert len(show.says(Moment.WELCOME_BACK)) == 1
    assert [say.about for _, say in show.says(Moment.LAST_WORD)] == [LEFT, RIGHT]


def test_and_were_back_after_a_conduct_pause_comes_when_the_moderator_resumes():
    show = resumed_without_maya()
    show.at(200, "dater_conduct", RIGHT, Conduct.UNKIND)
    show.at(202, "dater_conduct", RIGHT, Conduct.UNKIND)
    assert show.at(205, "back", LEFT) == []
    assert show.at(210, "moderator_resume")[-1] == WELCOME_BACK


def test_a_second_resume_keeps_the_first_dater_away():
    show = resumed_without_maya()
    show.at(200, "need_a_moment", RIGHT)
    show.at(230, "moderator_resume")
    show.until(600)
    assert show.says(Moment.LAST_WORD) == [], "neither is called on while away"
    assert all(say.about is not LEFT for _, say in show.says(Moment.SILENCE))


def test_with_both_daters_away_nobody_is_prompted_to_fill_a_silence():
    show = resumed_without_maya()
    show.at(200, "need_a_moment", RIGHT)
    show.at(230, "moderator_resume")
    show.until(320)
    assert [t for t, _ in show.says(Moment.SILENCE) if t > 230] == []


class FailingPoll(FakePoll):
    def tally(self):
        raise RuntimeError("the poll service is down")


def test_a_poll_that_fails_still_gives_the_result_and_ends_the_date():
    show = Show(poll=FailingPoll())
    cues = show.until(600).cues()
    assert DateEnded(EndReason.COMPLETED, Outcome.NOT_MUTUAL) in cues
    assert not any(isinstance(c, ShowVerdict) for c in cues) and Finished() in cues


class FlakyDesk(InMemoryDecisionDesk):
    """resolve() fails the first `failures` times."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def resolve(self):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("the database is down")
        return super().resolve()


def test_a_beat_that_fails_is_tried_again_and_never_loses_an_alert():
    runner = ShowRunner(
        REHEARSAL_PAIR, desk=FlakyDesk(2), poll=FakePoll(), next_pair=NEXT_PAIR, first_up=LEFT
    )
    run(runner, until=FULL.time + FULL.decision_window - 1)
    result_at = FULL.time + FULL.decision_window
    cues = runner.distress_check(result_at + 0.2, LEFT, Verdict.MAYBE)
    assert MAYBE_DISTRESSED in cues and BeatFailed("result") in cues
    later = [(t, c) for t, c in run_on(runner, result_at + 0.5, result_at + 5)]
    assert [t for t, c in later if c == Say(Moment.RESULT, text=script.RESULT_NOT_MUTUAL)] == [
        result_at + 2.5
    ]


def test_a_beat_that_keeps_failing_is_given_up_on_for_the_moderator():
    runner = ShowRunner(
        REHEARSAL_PAIR, desk=FlakyDesk(100), poll=FakePoll(), next_pair=NEXT_PAIR, first_up=LEFT
    )
    cues = [c for _, c in run(runner, until=FULL.time + FULL.decision_window + 10)]
    assert not runner.finished, "the moderator decides what happens next"
    assert cues.count(BeatFailed("result")) == 5 and BeatFailed("result", gave_up=True) in cues


def run_on(runner: ShowRunner, start: float, until: float, step: float = 0.5) -> list:
    """Ticks a date already started: every (time, cue)."""
    log, now = [], start
    while now <= until + 1e-9:
        log += [(now, cue) for cue in runner.tick(now)]
        now = round(now + step, 6)
    return log


def test_lines_waiting_for_a_dater_are_never_said_after_the_moderator_ends_the_date():
    result_at = FULL.time + FULL.decision_window
    show = Show()
    show.until(result_at - 2)
    show.at(result_at - 2, "speech_started", LEFT)
    show.until(result_at)  # the result goes out; the outro and invitation wait for Maya
    ended = show.at(result_at + 1, "moderator_end")
    assert ended == [StopSpeaking(), PlayFallbackSegment(), Finished()]
    assert show.at(result_at + 2, "speech_stopped", LEFT) == []


def test_next_up_waits_for_a_dater_and_the_date_finishes_only_once_it_is_out():
    next_up_at = FULL.time + FULL.decision_window + FULL.next_up_after_result
    show = Show()
    show.until(next_up_at - 2)
    show.at(next_up_at - 2, "speech_started", LEFT)
    show.until(next_up_at)
    assert not show.runner.finished, "Next up is waiting for Maya"
    out = show.at(next_up_at + 2, "speech_stopped", LEFT)
    assert isinstance(out[-2], Say) and out[-2].moment is Moment.NEXT_UP
    assert out[-1] == Finished()


@pytest.mark.parametrize(("method", "args"), [("dater_left", (LEFT,)), ("moderator_end", ())])
def test_an_early_end_while_deciding_closes_the_decision_window(method, args):
    show = Show()
    show.until(FULL.time + 1)
    getattr(show.runner, method)(FULL.time + 2, *args)
    with pytest.raises(DecisionError):
        show.desk.choose(LEFT, Choice.REVEAL)
    assert not show.desk.resolved and show.runner.outcome is None


def test_an_approved_question_dropped_by_a_pause_is_read_later():
    show = Show()
    show.at(1, "queue_question", approved("Mountains or sea?"))
    show.until(FULL.from_the_room - 1)
    show.at(FULL.from_the_room - 1, "speech_started", LEFT)
    show.until(FULL.from_the_room + 1)  # the question waits for Maya
    show.at(FULL.from_the_room + 1, "need_a_moment", RIGHT)
    show.at(FULL.from_the_room + 6, "back", RIGHT)
    show.at(FULL.from_the_room + 6.5, "speech_stopped", LEFT)
    show.until(600)
    asked = [c.detail for _, c in show.says(Moment.FROM_THE_ROOM)]
    assert asked.count("Mountains or sea?") == 1
    assert show.runner._questions_asked == 1


def test_with_a_dater_away_questions_and_replies_say_who_is_away():
    """So the line writer never names or addresses them (decision 0004)."""
    show = resumed_without_maya()
    show.at(141, "queue_question", approved("Mountains or sea?"))
    show.until(300)
    asked = show.says(Moment.FROM_THE_ROOM)
    assert asked and all(say.away is LEFT for _, say in asked)
    reply = show.at(301, "dater_asked_nova", RIGHT, "Nova, tea or coffee?")
    assert Say(Moment.REPLY, about=RIGHT, detail="Nova, tea or coffee?", away=LEFT) in reply


def test_with_both_daters_away_no_audience_question_is_read():
    show = resumed_without_maya()
    show.at(141, "queue_question", approved("Mountains or sea?"))
    show.at(200, "need_a_moment", RIGHT)
    show.at(230, "moderator_resume")
    show.until(400)
    assert show.says(Moment.FROM_THE_ROOM) == []


# --- Paced turns: each dater has time to answer --------------------------------------------

MAYAS_QUESTION = Say(Moment.FIRST_QUESTION, about=LEFT, detail=MAYA.prompts[0].text)


def first_question_times(show: Show) -> list[float]:
    return [t for t, _ in show.says(Moment.FIRST_QUESTION)]


def test_paced_the_second_dater_waits_until_the_first_had_time_to_answer():
    """Before, the second dater's question went by the clock alone: in the LiveKit listen run
    Nova ran late, asked Theo, then asked Maya 0.2 s later, before Theo could say a word."""
    show = Show(paced=True)
    show.until(80)  # Nova is still asking Maya at 80 s: running late
    assert first_question_times(show) == [45.0]
    show.runner.line_done(80, MAYAS_QUESTION)
    show.until(85.5)
    assert first_question_times(show) == [45.0], "the answer window is 6 s"
    show.until(86)
    assert first_question_times(show) == [45.0, 86.0]
    show.until(120)
    assert (108.5, Segment.FREE_TALK) in show.segments(), "the running order moved 18.5 s"


def test_paced_a_turn_is_on_time_when_nova_is():
    show = Show(paced=True)
    show.until(50)
    show.runner.line_done(50, MAYAS_QUESTION)
    show.until(100)
    assert first_question_times(show) == [45.0, 67.5]


def test_paced_a_turn_line_never_reported_holds_the_next_turn_at_most_twenty_seconds():
    show = Show(paced=True)
    show.until(120)
    assert first_question_times(show) == [45.0, 87.5]


def test_paced_after_the_cap_a_turn_still_never_cuts_a_dater_off():
    """Before, the 20 s cap ran out while Theo was mid-answer, and Nova spoke over him."""
    show = Show(paced=True)
    show.until(80)  # Theo's turn has waited since 67.5 s for Maya's line to be reported
    show.at(80, "speech_started", RIGHT)
    show.until(90)
    assert first_question_times(show) == [45.0], "the cap ran out at 87.5 s, but Theo is talking"
    show.at(92, "speech_stopped", RIGHT)
    show.until(93)
    assert first_question_times(show) == [45.0], "Maya may be about to answer him"
    show.until(93.5)
    assert first_question_times(show) == [45.0, 93.5], "the turn gap, even after the cap"


def test_paced_only_half_a_minute_of_speech_without_a_break_is_talked_over():
    show = Show(paced=True)
    show.at(60, "speech_started", LEFT)  # a TV the voice detector hears, from 60 s
    show.until(94.5)
    assert first_question_times(show) == [45.0], "30 s, then the usual 5 s talk-over grace"
    show.until(95)
    assert first_question_times(show) == [45.0, 95.0]


def test_unpaced_turns_go_by_the_clock():
    show = Show()
    show.until(100)
    assert first_question_times(show) == [45.0, 67.5]


def test_paced_thats_time_never_cuts_off_the_last_dater():
    """Each turn line finishes 3 s after its cue, but Nova only finishes asking Theo for his
    last word at 7:58: "That's time." waits until he has had his answer window."""
    show = Show(paced=True)
    reported: set[int] = set()
    while show.now < 490:
        show.until(show.now + 0.5)
        for i, (t, say) in enumerate(show.says()):
            if i in reported or say.moment not in (Moment.FIRST_QUESTION, Moment.LAST_WORD):
                continue
            theos_last_word = say.moment is Moment.LAST_WORD and say.about is RIGHT
            if show.now >= (478.0 if theos_last_word else t + 3.0):
                show.runner.line_done(show.now, say)
                reported.add(i)
    assert show.times(Say, moment=Moment.TIME) == [484.0]
    show.until(560)
    assert show.times(Say, moment=Moment.RESULT) == [544.0], "the full decision window"


def test_paced_a_turn_waits_while_a_dater_is_still_answering():
    """Before, the next line went out over a dater after the 5 s talk-over grace: Nova cut
    Theo off five seconds into an eight-second answer (the LiveKit run)."""
    show = Show(paced=True)
    show.until(50)
    show.runner.line_done(50, MAYAS_QUESTION)
    show.at(66, "speech_started", LEFT)  # Maya is still answering when Theo's turn is due
    show.until(74.5)
    assert first_question_times(show) == [45.0]
    show.at(75, "speech_stopped", LEFT)
    show.until(76)
    assert first_question_times(show) == [45.0], "a pause between sentences, maybe"
    show.until(76.5)
    assert first_question_times(show) == [45.0, 76.5], "1.5 s after she stops"


def test_paced_a_turn_dropped_by_a_pause_holds_nothing():
    show = Show(paced=True)
    show.until(50)
    show.runner.line_done(50, MAYAS_QUESTION)
    show.at(67, "speech_started", RIGHT)
    show.until(67.5)  # Theo's question waits for him to finish speaking
    show.at(68, "need_a_moment", RIGHT)  # and the pause drops it
    assert show.runner._turns_unfinished == 0


def paced_date(show: Show, until: float, events=(), *, finish_after: float = 3.0) -> Show:
    """Runs a paced date: Nova finishes each turn line finish_after seconds after its cue,
    and the daters start and stop speaking at the given times."""
    pending = sorted(events)
    reported: set[int] = show.__dict__.setdefault("reported", set())  # kept across calls
    while show.now < until:
        while pending and pending[0][0] <= show.now + 0.5:
            t, event, seat = pending.pop(0)
            show.at(t, event, seat)
        show.until(show.now + 0.5)
        for i, (t, say) in enumerate(show.says()):
            if i not in reported and say.moment in TURN_MOMENTS and show.now >= t + finish_after:
                show.runner.line_done(show.now, say)
                reported.add(i)
    return show


def audience_question_times(show: Show) -> list[float]:
    return show.times(Say, moment=Moment.FROM_THE_ROOM)


def two_quick_questions() -> Show:
    """The second audience question is due 8 s after the first (at 240 s)."""
    show = Show(paced=True, timing=replace_timing(second_question_after=8.0))
    show.at(10, "queue_question", approved("What's the best meal you've ever had?"))
    show.at(10, "queue_question", approved("Mountains or sea?"))
    return show


def test_paced_a_question_to_both_waits_for_the_second_dater_to_answer():
    """Before, Nova asked the next question 1.5 s after the first dater finished, just as
    the second began to answer (the LiveKit run with listening daters)."""
    show = paced_date(
        two_quick_questions(),
        300,
        [
            (244, "speech_started", LEFT),
            (249, "speech_stopped", LEFT),
            (252, "speech_started", RIGHT),  # Theo takes 3 s to start
            (256, "speech_stopped", RIGHT),
        ],
    )
    assert audience_question_times(show) == [240.0, 258.5], "after Theo, and a lull"


def test_paced_a_dater_who_never_answers_is_passed_over():
    show = paced_date(
        two_quick_questions(), 300, [(244, "speech_started", LEFT), (249, "speech_stopped", LEFT)]
    )
    assert audience_question_times(show) == [240.0, 255.0], "6 s after Maya, with no word from Theo"


def test_paced_a_question_to_one_dater_waits_only_for_them():
    show = paced_date(
        Show(paced=True, timing=replace_timing(second_prompt_after=6.0)),
        70,
        [(49, "speech_started", LEFT), (53, "speech_stopped", LEFT)],
    )
    assert first_question_times(show) == [45.0, 54.5], "Maya answered; nobody waits for Theo"


def test_paced_an_audience_question_waits_for_a_lull_not_a_gap():
    """The daters are talking to each other as the audience question comes due at 240 s:
    a 2 s gap between them isn't a lull."""
    show = Show(paced=True)
    show.at(10, "queue_question", approved("Mountains or sea?"))
    paced_date(
        show,
        260,
        [
            (236, "speech_started", LEFT),
            (238.5, "speech_stopped", LEFT),
            (240.5, "speech_started", RIGHT),
            (243, "speech_stopped", RIGHT),
        ],
    )
    assert audience_question_times(show) == [245.5]


def test_paced_the_cap_counts_from_when_nova_finished_asking():
    """Before, it counted from when the next turn came due, while Nova was still asking the
    question before: the cap ran out before Theo had answered, and Nova moved on over him."""
    show = Show(paced=True, timing=replace_timing(second_question_after=8.0))
    show.at(10, "queue_question", approved("What's the best meal you've ever had?"))
    show.at(10, "queue_question", approved("Mountains or sea?"))
    paced_date(show, 239)
    show.until(262)  # Nova is slow to finish the first question: the next has waited 14 s
    first = next(i for i, (_, say) in enumerate(show.says()) if say.moment is Moment.FROM_THE_ROOM)
    show.runner.line_done(262, show.says()[first][1])
    show.reported.add(first)
    paced_date(show, 300, [(263, "speech_started", LEFT), (270, "speech_stopped", LEFT)])
    assert audience_question_times(show) == [240.0, 276.0], "6 s after Maya, Theo's chance"
