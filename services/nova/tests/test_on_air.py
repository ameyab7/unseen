"""One place hears what a dater says on air (nova/on_air.py). The rehearsal, the evaluation
and, later, the agent for real shows all use it, so the evaluation measures what runs."""

import pytest

from nova import script
from nova.cards import Seat
from nova.cues import AlertModerator, ModeratorAlert, Moment, Say
from nova.decisions import InMemoryDecisionDesk
from nova.distress import DistressCheck, Verdict
from nova.evaluation import hear_on_air
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.on_air import OnAirCues, OnAirListener
from nova.rehearsal import Rehearsal
from nova.show_runner import NoVotes, ShowRunner
from nova.timing import FULL

LEFT = Seat.LEFT
ON_AIR_AT = FULL.free_talk + 20.0  # where hear_on_air puts the date: free talk
DISTRESSED = AlertModerator(ModeratorAlert.DATER_DISTRESSED, LEFT)


class Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Model:
    """Writes every line the same way and answers the distress check per sentence ("no" by
    default, "error" to fail)."""

    name = "fake/model"

    def __init__(self, labels: dict[str, str] | None = None) -> None:
        self.labels = labels or {}
        self.checked: list[tuple[str, str]] = []  # (earlier, sentence) as the check saw them

    async def write(self, *, system: str, prompt: str) -> str:
        return "Here we go, Maya and Theo."

    async def label(self, *, system: str, prompt: str) -> str:
        data = prompt.split("<data>")[1]
        sentence = data.split('"sentence": "')[1].split('"')[0]
        self.checked.append((data.split('"earlier": "')[1].split('"')[0], sentence))
        answer = self.labels.get(sentence, "no")
        if answer == "error":
            raise RuntimeError("the model is down")
        return answer


class Speaker:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def speak(self, text: str) -> float | None:
        self.texts.append(text)
        return 0.0

    def stop(self) -> None:
        pass


# An unmistakable request, each verdict of the meaning layer, and ordinary words to Nova.
SENTENCES = [
    ("I want to stop.", None),
    ("Nova, I want to stop talking to him.", "yes"),
    ("Nova, help, I'm a bit lost.", "maybe"),
    ("Nova, help me out here.", "error"),
    ("Nova, tea or coffee?", "no"),
]


async def through_the_rehearsal(words: str, labels: dict[str, str]) -> tuple[list, bool]:
    """The cues the rehearsal queues for these words in free talk, and whether Nova replied."""
    clock = Clock()
    r = Rehearsal(
        REHEARSAL_PAIR,
        model=Model(labels),
        speaker=Speaker(),
        voice="fake/voice",
        stt="deepgram/nova-3",
        clock=clock,
        timing=FULL,
        next_pair=NEXT_PAIR,
        first_up=LEFT,
    )
    r.start()
    clock.now = ON_AIR_AT
    r.tick()
    await r.speak_queued()
    before = len(r.cues)
    r.transcript(words)
    await r.heard()
    await r.speak_queued()
    return r.cues[before:], any(s.moment is Moment.REPLY for s in r.spoken)


@pytest.mark.parametrize(("words", "label"), SENTENCES)
async def test_the_rehearsal_and_the_evaluation_hear_a_dater_the_same_way(words, label):
    labels = {words: label} if label else {}
    cues, replied = await through_the_rehearsal(words, labels)
    heard = await hear_on_air(DistressCheck(Model(labels), timeout=1.0), words)
    assert list(heard.cues) == cues
    assert heard.reply is replied
    assert replied is (words.startswith("Nova") and label != "yes")


# --- What the listener hands on, and when -------------------------------------------------


def on_air(labels: dict[str, str] | None = None, *, at: float = ON_AIR_AT):
    """A started date (beats not yet ticked) and a listener that keeps what it hands on."""
    runner = ShowRunner(REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=NoVotes(), first_up=LEFT)
    runner.start(0.0)
    delivered: list[OnAirCues] = []
    model = Model(labels)
    check = DistressCheck(model, timeout=1.0)
    listener = OnAirListener(runner, check, deliver=delivered.append, clock=Clock(at))
    return listener, delivered, model


async def test_beats_already_due_come_first_and_are_never_the_reply():
    listener, delivered, _ = on_air()
    listener.speech_ended(LEFT)
    listener.hear(LEFT, "Nova, tea or coffee?")
    await listener.heard()
    beats, reply = delivered
    assert beats.sentence_end is None and beats.hold is None
    assert Moment.FIRST_QUESTION in [c.moment for c in beats.cues if isinstance(c, Say)]
    assert reply.cues == [Say(Moment.REPLY, about=LEFT, detail="Nova, tea or coffee?")]
    assert reply.sentence_end == ON_AIR_AT
    assert reply.hold is not None and reply.hold.result() is Verdict.NO


async def test_the_reply_is_held_until_the_verdicts_cues_have_been_handed_on():
    words = "Nova, I want to stop talking to him."
    listener, delivered, _ = on_air({words: "yes"})
    listener.hear(LEFT, words)
    reply = delivered[-1]
    assert reply.cues[0].moment is Moment.REPLY and not reply.hold.done(), "held"
    await listener.heard()
    verdict = delivered[-1]
    assert verdict.cues[:2] == [DISTRESSED, verdict.cues[1]]
    assert Say(Moment.NEED_A_MOMENT, text=script.DISTRESS_PAUSE) in verdict.cues
    assert reply.hold.result() is Verdict.YES, "the reply is dropped"
    assert [c.verdict for c in listener.checks] == [Verdict.YES]


async def test_an_unmistakable_request_needs_no_check_and_is_never_answered():
    listener, delivered, model = on_air()
    hearing = listener.hear(LEFT, "Nova, I want to stop.")
    await listener.heard()
    assert hearing.unmistakable == "Nova, I want to stop." and model.checked == []
    stop = delivered[-1]
    assert stop.cues[0] == DISTRESSED and stop.hold is None
    assert Moment.REPLY not in [c.moment for c in stop.cues if isinstance(c, Say)]


async def test_the_evaluation_can_set_up_what_a_dater_said_before_without_checking_it():
    listener, _, model = on_air()
    listener.remember(LEFT, "He keeps commenting on my body.", at=ON_AIR_AT - 2)
    listener.hear(LEFT, "Can we not do this?")
    await listener.heard()
    assert model.checked == [("He keeps commenting on my body.", "Can we not do this?")]
