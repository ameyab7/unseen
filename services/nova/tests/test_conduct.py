"""Hearing a dater behave badly on air (decision 0006): personal details at once with no
model, and the rest from the conduct label the distress check's call returns."""

import pytest

from nova import script
from nova.cards import Seat
from nova.conduct import personal_detail
from nova.cues import (
    AlertModerator,
    Conduct,
    DateEnded,
    EndReason,
    Finished,
    ModeratorAlert,
    Moment,
    PlayFallbackSegment,
    RoomFacts,
    Say,
    Segment,
    SegmentStarted,
    Situation,
    StopSpeaking,
)
from nova.decisions import InMemoryDecisionDesk
from nova.distress import LABEL_SCHEMA, DistressCheck, Verdict, parse, parse_conduct
from nova.evaluation import FakeClock, conduct_found, render_conduct, run_conduct
from nova.fixtures import REHEARSAL_PAIR
from nova.on_air import MATCH_CAP, OnAirCues, OnAirListener
from nova.show_runner import NoVotes, ShowRunner
from nova.timing import FULL

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT
FREE_TALK = FULL.free_talk + 20.0
STREET_LINE = Say(Moment.KEEP_STREETS_OFF_AIR, text=script.KEEP_STREETS_OFF_AIR)
KIND = Say(Moment.KEEP_IT_KIND, text=script.KEEP_IT_KIND)
DETAIL_ALERT = AlertModerator(ModeratorAlert.PERSONAL_DETAIL_ON_AIR, LEFT)


# --- The instant layer: personal details, with no model -------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "i live on mercer street near the old cinema",
        "come round to 14 cardew road",
        "Harbour Street, the blue door, that's me.",
        "my address is flat two ashby crescent",
    ],
)
def test_a_street_or_address_is_heard_in_lowercase_too(said):
    slipped = personal_detail(said)
    assert slipped is not None and slipped.street


@pytest.mark.parametrize(
    "said",
    [
        "text me on oh seven seven one two three four five six",
        "my number's 07700 900123",
        "email me at tilly.k@example.com",
        "find me at @tilly_bakes",
        "i work at lumen dental",
        "i studied at kingsmoor college",
    ],
)
def test_other_personal_details_are_heard_but_are_not_a_street(said):
    slipped = personal_detail(said)
    assert slipped is not None and not slipped.street


@pytest.mark.parametrize(
    "said",
    [
        "I live in Manchester now, it rains constantly.",
        "My favourite street food is a falafel wrap.",
        "No way, that's brilliant.",
        "I hated school, honestly.",
        "We drove down the road for hours.",
        "I'm sixteen days into learning guitar.",
    ],
)
def test_ordinary_talk_is_not_a_personal_detail(said):
    assert personal_detail(said) is None


# --- What each conduct does on air ----------------------------------------------------------


def on_air_runner() -> ShowRunner:
    runner = ShowRunner(REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=NoVotes(), first_up=LEFT)
    runner.start(0.0)
    runner.tick(FREE_TALK)
    return runner


def test_a_street_slipping_gets_the_brand_book_line_and_a_moderator_alert():
    runner = on_air_runner()
    cues = runner.dater_conduct(FREE_TALK, LEFT, Conduct.PERSONAL_DETAIL, street=True)
    assert cues == [DETAIL_ALERT, STREET_LINE]


def test_the_street_line_waits_for_the_dater_to_finish():
    runner = on_air_runner()
    runner.speech_started(FREE_TALK, RIGHT)
    cues = runner.dater_conduct(FREE_TALK, LEFT, Conduct.PERSONAL_DETAIL, street=True)
    assert cues == [DETAIL_ALERT], "Theo is mid-sentence"
    assert STREET_LINE in runner.speech_stopped(FREE_TALK + 1, RIGHT)


def test_another_personal_detail_alerts_the_moderator_only():
    runner = on_air_runner()
    assert runner.dater_conduct(FREE_TALK, LEFT, Conduct.PERSONAL_DETAIL) == [DETAIL_ALERT]


def test_a_first_unkind_remark_gets_lets_keep_it_kind():
    runner = on_air_runner()
    assert runner.dater_conduct(FREE_TALK, LEFT, Conduct.UNKIND) == [KIND]
    assert runner.dater_conduct(FREE_TALK + 5, RIGHT, Conduct.UNKIND) == [KIND], "per dater"


def test_a_second_unkind_remark_by_the_same_dater_pauses_the_whole_date():
    runner = on_air_runner()
    runner.dater_conduct(FREE_TALK, LEFT, Conduct.UNKIND)
    cues = runner.dater_conduct(FREE_TALK + 10, LEFT, Conduct.UNKIND)
    facts = RoomFacts(Segment.FREE_TALK, False, 0, Situation.SHOW_PAUSED)
    assert cues[-4:] == [
        AlertModerator(ModeratorAlert.CONDUCT_FLAG, LEFT, Conduct.UNKIND),
        StopSpeaking(),
        Say(Moment.SHOW_PAUSE, text=script.SHOW_PAUSE),
        Say(Moment.ROOM_TALK, room=facts, number=1, of=3),
    ]
    assert runner.tick(FREE_TALK + 200) == [] or runner.segment is Segment.FREE_TALK, "held"
    assert runner.back(FREE_TALK + 210, LEFT) == [], "not a dater's moment: back doesn't end it"
    runner.moderator_resume(FREE_TALK + 220)
    assert runner.dater_conduct(FREE_TALK + 230, LEFT, Conduct.NONE) == []


def paused_for_conduct() -> ShowRunner:
    runner = on_air_runner()
    runner.dater_conduct(FREE_TALK, LEFT, Conduct.UNKIND)
    runner.dater_conduct(FREE_TALK + 10, LEFT, Conduct.UNKIND)
    return runner


def test_the_moderator_resumes_a_paused_date_and_the_running_order_carries_on():
    runner = paused_for_conduct()
    runner.moderator_resume(FREE_TALK + 20)
    later = runner.tick(FREE_TALK + 200)
    assert SegmentStarted(Segment.FROM_THE_ROOM) in later
    assert runner.segment is Segment.FROM_THE_ROOM


def test_without_the_moderator_a_paused_date_stays_held():
    runner = paused_for_conduct()
    later = runner.tick(FREE_TALK + 200)
    assert SegmentStarted(Segment.FROM_THE_ROOM) not in later
    assert runner.segment is Segment.FREE_TALK


def test_the_moderator_ends_a_paused_date():
    runner = paused_for_conduct()
    ended = runner.moderator_end(FREE_TALK + 20)
    assert Say(Moment.MODERATOR_END, text=script.MODERATOR_END) in ended
    assert PlayFallbackSegment() in ended and Finished() in ended
    assert DateEnded(EndReason.MODERATOR_ENDED, None) in ended
    assert runner.tick(FREE_TALK + 200) == [] and runner.back(FREE_TALK + 210, LEFT) == []


@pytest.mark.parametrize(
    "conduct",
    [
        Conduct.HARASSMENT,
        Conduct.SEXUAL,
        Conduct.HATE,
        Conduct.THREAT,
        Conduct.MINOR,
        Conduct.UNSURE,
    ],
)
def test_serious_conduct_alerts_the_moderator_at_once_and_nova_says_nothing(conduct):
    cues = on_air_runner().dater_conduct(FREE_TALK, RIGHT, conduct)
    assert cues == [AlertModerator(ModeratorAlert.CONDUCT_FLAG, RIGHT, conduct)]


UNKIND_FLAG = AlertModerator(ModeratorAlert.CONDUCT_FLAG, LEFT, Conduct.UNKIND)


def test_off_air_conduct_still_reaches_the_moderator_and_nova_says_nothing():
    runner = ShowRunner(REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=NoVotes(), first_up=LEFT)
    assert runner.dater_conduct(5.0, LEFT, Conduct.PERSONAL_DETAIL, street=True) == [DETAIL_ALERT]
    assert runner.dater_conduct(6.0, LEFT, Conduct.UNKIND) == [UNKIND_FLAG]
    assert runner.dater_conduct(7.0, LEFT, Conduct.UNKIND) == [UNKIND_FLAG]
    flagged = runner.dater_conduct(8.0, LEFT, Conduct.THREAT)
    assert flagged == [AlertModerator(ModeratorAlert.CONDUCT_FLAG, LEFT, Conduct.THREAT)]


def test_unkind_remarks_off_air_never_count_towards_the_pause():
    """Only a remark Nova answered with "Let's keep it kind." is a warning: the first one
    on air after any number off air gets the warning, not a pause."""
    runner = ShowRunner(REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=NoVotes(), first_up=LEFT)
    runner.dater_conduct(5.0, LEFT, Conduct.UNKIND)  # before the date
    runner.start(0.0)
    runner.tick(FREE_TALK)
    runner.need_a_moment(FREE_TALK, RIGHT)
    assert runner.dater_conduct(FREE_TALK + 1, LEFT, Conduct.UNKIND) == [UNKIND_FLAG], "on a hold"
    runner.back(FREE_TALK + 5, RIGHT)
    assert runner.dater_conduct(FREE_TALK + 10, LEFT, Conduct.UNKIND) == [KIND]


def test_a_dater_who_may_be_distressed_is_never_told_to_keep_it_kind():
    """They may be describing what was said to them: the moderator is alerted, and it never
    counts as a strike."""
    runner = on_air_runner()
    assert runner.dater_conduct(FREE_TALK, LEFT, Conduct.UNKIND, distressed=True) == [UNKIND_FLAG]
    assert runner.dater_conduct(FREE_TALK + 5, LEFT, Conduct.UNKIND, distressed=True) == [
        UNKIND_FLAG
    ]
    said = runner.dater_conduct(FREE_TALK + 10, LEFT, Conduct.UNKIND)
    assert KIND in said and UNKIND_FLAG not in said, "the first strike"


# --- The meaning layer: one call, two labels ------------------------------------------------


def test_the_check_asks_for_a_conduct_label_in_the_same_call():
    assert LABEL_SCHEMA["required"] == ["label", "conduct"]
    enum = LABEL_SCHEMA["properties"]["conduct"]["enum"]  # type: ignore[index]
    assert set(enum) == {conduct.value for conduct in Conduct}


@pytest.mark.parametrize(
    ("answer", "conduct"),
    [
        ('{"label": "no", "conduct": "harassment"}', Conduct.HARASSMENT),
        ('{"label": "no", "conduct": "none"}', Conduct.NONE),
        ('{"label": "no", "conduct": "rude"}', None),
        ("no", None),
    ],
)
def test_the_conduct_label_is_read_from_the_json(answer, conduct):
    assert parse_conduct(answer) is conduct


class Labels:
    """A check that labels conduct per sentence, and never distress."""

    name = "fake/labels"

    def __init__(self, conduct: dict[str, str]) -> None:
        self.conduct = conduct

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        sentence = prompt.split('"sentence": "')[1].split('"')[0]
        return f'{{"label": "no", "conduct": "{self.conduct.get(sentence, "none")}"}}'

    async def write(self, *, system: str, prompt: str) -> str:
        return "Here we go, Maya and Theo."


async def listen(said: str, conduct: dict[str, str]) -> list:
    runner = on_air_runner()
    delivered: list[OnAirCues] = []
    clock = FakeClock(FREE_TALK)
    listener = OnAirListener(
        runner, DistressCheck(Labels(conduct), timeout=1.0), deliver=delivered.append, clock=clock
    )
    listener.speech_ended(LEFT)
    listener.hear(LEFT, said)
    clock.now += 1.0
    listener.tick()
    await listener.heard()
    return [cue for d in delivered for cue in d.cues]


async def test_the_listener_hears_a_street_at_once_without_the_model():
    cues = await listen("i live on mercer street", {})
    assert DETAIL_ALERT in cues and STREET_LINE in cues


async def test_the_listener_acts_on_the_models_conduct_label():
    said = "Tell me what you're wearing to bed tonight."
    cues = await listen(said, {said: "sexual"})
    assert AlertModerator(ModeratorAlert.CONDUCT_FLAG, LEFT, Conduct.SEXUAL) in cues


async def test_a_detail_the_instant_layer_heard_is_not_flagged_twice():
    said = "i live on mercer street"
    cues = await listen(said, {said: "personal_detail"})
    assert cues.count(DETAIL_ALERT) == 1


# --- The evaluation ------------------------------------------------------------------------


def test_what_nova_did_is_read_from_the_cues():
    cues = [DETAIL_ALERT, STREET_LINE, KIND]
    assert conduct_found(cues) == {"street", "personal_detail", "unkind"}


async def test_the_conduct_run_reports_recall_and_false_alerts_per_category():
    from nova.conduct_set import ConductSample

    samples = (
        ConductSample("Tell me what you're wearing.", "sexual"),
        ConductSample("I love that film.", "none"),
    )
    results = await run_conduct(
        Labels({"Tell me what you're wearing.": "sexual"}), timeout=1.0, samples=samples
    )
    report = render_conduct(results, model_name="fake", set_name="dev half")
    assert "sexual           1 of 1, 100.0%" in report
    assert "Ordinary talk left alone: 1 of 1" in report


def test_working_at_getting_better_is_effort_not_a_workplace():
    assert personal_detail("I work at being a better listener, honestly.") is None
    assert personal_detail("i work at lumen dental") is not None, "a place still is"


# --- The street line only for a street ------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "it's a 40 minute drive",
        "we did a big road trip",
        "i live on coffee",
        "rocky road is the best ice cream",
        "i'm a fan of rocky road",
        "abbey road is my favourite album",
        "a trip down memory lane",
        "life in the fast lane",
        "wall street bankers",
        "a one way street",
        "i still live at home with my parents",
        "i live near the station",
        "i basically live at the gym",
        "My mum lives near the sea now, she loves it.",
        "i'm on my way",
        "close to the square",
        "at joe's place",
    ],
)
def test_ordinary_talk_never_gets_the_street_line(said):
    slipped = personal_detail(said)
    assert slipped is None or not slipped.street


@pytest.mark.parametrize(
    "said",
    [
        "i live on harbour street",
        "i live at 42 elm road",
        "my address is 4 elm road",
        "i live at the top of quarry road",
        "Harbour Street, the blue door, that's me.",
        "i live on maple drive",
        "we're near kings close",
        "i'm just off ashby gardens",
        "I'm at 9 willow lane, the one with the red door.",
    ],
)
def test_a_street_still_gets_the_street_line(said):
    slipped = personal_detail(said)
    assert slipped is not None and slipped.street


@pytest.mark.parametrize(
    "said",
    ["why do you keep asking what street i live on", "stop asking what road i'm on"],
)
def test_asking_about_a_street_or_refusing_to_give_one_is_not_a_street_slipping(said):
    assert personal_detail(said) is None


# --- A bare answer with both labels --------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "verdict", "conduct"),
    [
        ("no, none", Verdict.NO, Conduct.NONE),
        ("maybe, harassment", Verdict.MAYBE, Conduct.HARASSMENT),
        ("yes\nconduct: threat", Verdict.YES, Conduct.THREAT),
        ("Label: no. Conduct: threat.", Verdict.NO, Conduct.THREAT),
        ("label: yes", Verdict.YES, None),
        ('{"label": "no", "conduct": "Harassment"}', Verdict.NO, Conduct.HARASSMENT),
        ("No.", Verdict.NO, None),
    ],
)
def test_a_model_that_ignores_the_format_can_still_answer_both_labels(answer, verdict, conduct):
    """The rules ask for the label and the conduct label, so a bare answer may carry both."""
    assert (parse(answer), parse_conduct(answer)) == (verdict, conduct)


@pytest.mark.parametrize("answer", ["none", "no no", "Yes, she wants to stop.", "no, maybe"])
def test_anything_more_than_the_two_labels_is_still_no_answer(answer):
    assert parse(answer) is None


def test_a_conduct_label_alone_is_no_conduct_label():
    assert parse_conduct("harassment") is None


# --- Whose conduct it is, and what Nova says -------------------------------------------------


class Answers:
    """A check that answers each sentence with a given label and conduct label."""

    name = "fake/answers"

    def __init__(self, answers: dict[str, tuple[str, str]]) -> None:
        self.answers = answers
        self.checked: list[str] = []

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        sentence = prompt.split('"sentence": "')[1].split('"')[0]
        self.checked.append(sentence)
        label, conduct = self.answers.get(sentence, ("no", "none"))
        return f'{{"label": "{label}", "conduct": "{conduct}"}}'

    async def write(self, *, system: str, prompt: str) -> str:
        return "Here we go, Maya and Theo."


async def hear(*said: tuple[Seat, str], answers: dict[str, tuple[str, str]]) -> list:
    """Each utterance said in turn, five seconds apart, in free talk: everything Nova did."""
    runner = on_air_runner()
    delivered: list[OnAirCues] = []
    clock = FakeClock(FREE_TALK)
    model = Answers(answers)
    listener = OnAirListener(
        runner, DistressCheck(model, timeout=1.0), deliver=delivered.append, clock=clock
    )
    for seat, words in said:
        listener.speech_ended(seat)
        listener.hear(seat, words)
        clock.now += 1.0
        listener.tick()
        await listener.heard()
        clock.now += 4.0
    return [cue for d in delivered for cue in d.cues]


async def test_a_dater_reporting_what_was_said_to_them_is_never_told_to_keep_it_kind():
    """If the model labels the report itself unkind, the dater who may be distressed is not
    the one it's held against: the moderator hears of it, with no line and no strike."""
    first, second = (
        "he just called me ugly and boring",
        "and now he says i'm stupid for being upset",
    )
    cues = await hear(
        (LEFT, first),
        (LEFT, second),
        answers={first: ("maybe", "unkind"), second: ("maybe", "unkind")},
    )
    assert KIND not in cues
    assert not any(isinstance(c, Say) and c.moment is Moment.SHOW_PAUSE for c in cues)
    assert cues.count(UNKIND_FLAG) == 2


async def test_a_first_unkind_remark_to_nova_gets_no_answer_only_lets_keep_it_kind():
    words = "Nova, isn't Theo the most boring man alive?"
    runner_said = await hear((LEFT, words), answers={words: ("no", "unkind")})
    assert KIND in runner_said


async def test_a_threat_in_the_same_breath_as_a_request_to_stop_is_still_flagged():
    words = "I need to go. Give me your number or I'll find you after the show."
    cues = await hear((RIGHT, words), answers={words: ("no", "threat")})
    assert AlertModerator(ModeratorAlert.DATER_DISTRESSED, RIGHT) in cues
    assert AlertModerator(ModeratorAlert.CONDUCT_FLAG, RIGHT, Conduct.THREAT) in cues


async def test_a_bare_request_to_stop_still_needs_no_model():
    model_checked: list[str] = []
    words = "Nova, I want to stop. Can we end this?"

    class Never(Answers):
        async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
            model_checked.append(prompt)
            return await super().label(system=system, prompt=prompt, schema=schema)

    runner = on_air_runner()
    listener = OnAirListener(
        runner, DistressCheck(Never({}), timeout=1.0), deliver=lambda _: None, clock=lambda: 0.0
    )
    listener.hear(LEFT, words)
    listener.speech_ended(LEFT)
    listener.flush()
    await listener.heard()
    assert model_checked == []


async def test_a_detail_in_a_request_acted_on_while_the_dater_keeps_talking_is_still_heard():
    """After MATCH_CAP the request is acted on mid-speech; the street said in the same words
    still alerts the moderator."""
    runner = on_air_runner()
    delivered: list[OnAirCues] = []
    clock = FakeClock(FREE_TALK)
    listener = OnAirListener(
        runner, DistressCheck(Answers({}), timeout=1.0), deliver=delivered.append, clock=clock
    )
    listener.speech_started(LEFT)
    clock.now += 1.0
    listener.hear(LEFT, "I live on mercer street and I want to stop.")
    clock.now += MATCH_CAP
    listener.tick()
    await listener.heard()
    cues = [cue for d in delivered for cue in d.cues]
    assert AlertModerator(ModeratorAlert.DATER_DISTRESSED, LEFT) in cues
    assert DETAIL_ALERT in cues


def test_a_date_held_for_conduct_reminds_the_moderator_if_it_runs_long():
    """Only the moderator can lift it, so the console hears again at the 30 s mark, as for a
    dater who isn't back."""
    runner = on_air_runner()
    runner.dater_conduct(FREE_TALK, LEFT, Conduct.UNKIND)
    runner.dater_conduct(FREE_TALK + 10, LEFT, Conduct.UNKIND)
    later = runner.tick(FREE_TALK + 10 + FULL.moment_alert_after)
    assert AlertModerator(ModeratorAlert.SHOW_HELD, LEFT) in later
