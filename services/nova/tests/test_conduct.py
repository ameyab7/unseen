"""Hearing a dater behave badly on air (decision 0006): personal details at once with no
model, and the rest from the conduct label the distress check's call returns."""

import pytest

from nova import script
from nova.cards import Seat
from nova.conduct import personal_detail
from nova.cues import (
    AlertModerator,
    Conduct,
    ModeratorAlert,
    Moment,
    RoomFacts,
    Say,
    Segment,
    Situation,
    StopSpeaking,
)
from nova.decisions import InMemoryDecisionDesk
from nova.distress import LABEL_SCHEMA, DistressCheck, parse_conduct
from nova.evaluation import FakeClock, conduct_found, render_conduct, run_conduct
from nova.fixtures import REHEARSAL_PAIR
from nova.on_air import OnAirCues, OnAirListener
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


def test_the_moderator_resumes_or_ends_a_paused_date():
    runner = on_air_runner()
    runner.dater_conduct(FREE_TALK, LEFT, Conduct.UNKIND)
    runner.dater_conduct(FREE_TALK + 10, LEFT, Conduct.UNKIND)
    runner.moderator_resume(FREE_TALK + 20)
    later = runner.tick(FREE_TALK + 200)
    assert any(isinstance(c, Say) and c.moment is Moment.FROM_THE_ROOM for c in later) or (
        runner.segment is not None
    ), "the running order carries on, 10 s late"


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


def test_off_air_conduct_still_reaches_the_moderator_and_nova_says_nothing():
    runner = ShowRunner(REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=NoVotes(), first_up=LEFT)
    assert runner.dater_conduct(5.0, LEFT, Conduct.PERSONAL_DETAIL, street=True) == [DETAIL_ALERT]
    assert runner.dater_conduct(6.0, LEFT, Conduct.UNKIND) == []
    runner.dater_conduct(7.0, LEFT, Conduct.UNKIND)
    flagged = runner.dater_conduct(8.0, LEFT, Conduct.THREAT)
    assert flagged == [AlertModerator(ModeratorAlert.CONDUCT_FLAG, LEFT, Conduct.THREAT)]


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
