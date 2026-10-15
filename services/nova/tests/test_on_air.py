"""One place hears what a dater says on air (nova/on_air.py), in whole utterances. The
rehearsal, the evaluation and, later, the agent for real shows all use it, so the
evaluation measures what runs."""

import asyncio
import time

import pytest

from nova import script
from nova.cards import Seat
from nova.cues import AlertModerator, ModeratorAlert, Moment, Say
from nova.decisions import InMemoryDecisionDesk
from nova.distress import DistressCheck, Verdict
from nova.evaluation import Speech, hear_on_air, spoken
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.on_air import MATCH_CAP, OnAirCues, OnAirListener, addresses_nova
from nova.rehearsal import Rehearsal
from nova.show_runner import NoVotes, ShowRunner
from nova.timing import FULL

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT
ON_AIR_AT = FULL.free_talk + 20.0  # where hear_on_air puts the date: free talk
GRACE, MAX_WAIT = 0.6, 1.5  # the defaults
DISTRESSED = AlertModerator(ModeratorAlert.DATER_DISTRESSED, LEFT)


class Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Model:
    """Writes every line the same way and answers the distress check per utterance ("no" by
    default, "error" to fail), counting every call."""

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


# --- The rehearsal and the evaluation hear a dater the same way ----------------------------

# An unmistakable request, each verdict of the meaning layer, ordinary words to Nova, and
# sentences speech-to-text splits in two.
SENTENCES = [
    (("I want to stop.",), None),
    (("Nova, I want to stop talking to him.",), "yes"),
    (("Nova, help, I'm a bit lost.",), "maybe"),
    (("Nova, help me out here.",), "error"),
    (("Nova, tea or coffee?",), "no"),
    (("Nova,", "what's your favourite film?"), "no"),
    (("I want to stop", "eating sugar."), "no"),
    (("Nova,", "I want to stop."), None),
]


async def through_the_rehearsal(finals: tuple[str, ...], labels: dict[str, str]):
    """The cues the rehearsal queues for these finals in free talk, spoken as the evaluation
    speaks them, and whether Nova replied."""
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
    for at, speech, final in spoken(finals, at=ON_AIR_AT):
        while clock.now + 0.05 < at:  # the rehearsal ticks as time passes
            clock.now += 0.05
            r.tick()
        clock.now = at
        match speech:
            case Speech.STARTED:
                r.speech_started()
            case Speech.FINAL:
                r.transcript(final)
            case Speech.ENDED:
                r.speech_ended()
    for _ in range(40):  # two seconds: more than the most Nova waits
        clock.now += 0.05
        r.tick()
    await r.heard()
    await r.speak_queued()
    return r.cues[before:], any(s.moment is Moment.REPLY for s in r.spoken)


@pytest.mark.parametrize(("finals", "label"), SENTENCES)
async def test_the_rehearsal_and_the_evaluation_hear_a_dater_the_same_way(finals, label):
    words = " ".join(finals)
    labels = {words: label} if label else {}
    cues, replied = await through_the_rehearsal(finals, labels)
    heard = await hear_on_air(DistressCheck(Model(labels), timeout=1.0), *finals)
    assert list(heard.cues) == cues
    assert heard.reply is replied
    wants_to_stop = label == "yes" or label is None
    assert replied is (words.startswith("Nova") and not wants_to_stop)


# --- Utterances -----------------------------------------------------------------------------


def on_air(labels: dict[str, str] | None = None, *, at: float = ON_AIR_AT, started=True):
    """A date (started, beats not yet ticked, unless started is False) and a listener that
    keeps what it hands on."""
    runner = ShowRunner(REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=NoVotes(), first_up=LEFT)
    if started:
        runner.start(0.0)
    delivered: list[OnAirCues] = []
    model = Model(labels)
    clock = Clock(at)
    listener = OnAirListener(
        runner, DistressCheck(model, timeout=1.0), deliver=delivered.append, clock=clock
    )
    return listener, delivered, model, clock


def at(listener: OnAirListener, clock: Clock, when: float) -> None:
    """Time passes to when, the listener ticking at each moment it has something due, as
    listen() does."""
    while (due := listener.next_due) is not None and due <= when:
        clock.now = max(clock.now, due)
        listener.tick()
    clock.now = max(clock.now, when)


def cues(delivered: list[OnAirCues]) -> list:
    return [cue for on_air_cues in delivered for cue in on_air_cues.cues]


def replies(delivered: list[OnAirCues]) -> list[Say]:
    return [c for c in cues(delivered) if isinstance(c, Say) and c.moment is Moment.REPLY]


def paused(delivered: list[OnAirCues]) -> bool:
    return Say(Moment.NEED_A_MOMENT, text=script.DISTRESS_PAUSE) in cues(delivered)


async def say(
    listener: OnAirListener, clock: Clock, *finals: str, gap: float = 1.0, seat: Seat = LEFT
):
    """A dater says these finals in one breath, gap seconds apart (speech-to-text finalizes
    at each short pause while the voice detector still hears speech), stops 0.2 s after
    the last one arrives, and then is quiet until everything is acted on."""
    start = clock.now
    listener.speech_started(seat)
    for k, final in enumerate(finals):
        at(listener, clock, start + gap * (k + 1))
        listener.hear(seat, final)
    at(listener, clock, clock.now + 0.2)
    listener.speech_ended(seat)
    at(listener, clock, clock.now + MAX_WAIT + 0.1)
    await listener.heard()


@pytest.mark.parametrize(
    "fragments",
    [
        ("I want to stop", "eating sugar."),
        ("I need a break", "from work, a holiday is overdue."),
        ("Can we stop", "and appreciate that answer?"),
    ],
)
async def test_a_phrase_the_dater_carries_on_from_is_not_a_request_to_stop(fragments):
    """Each first fragment alone is a request to stop; the whole utterance isn't."""
    listener, delivered, model, clock = on_air()
    await say(listener, clock, *fragments)
    assert not paused(delivered) and DISTRESSED not in cues(delivered)
    assert [sentence for _, sentence in model.checked] == [" ".join(fragments)]


@pytest.mark.parametrize(
    "fragments",
    [
        ("I want to stop", "eating sugar."),
        ("I need a break", "from work, a holiday is overdue."),
        ("Can we stop", "and appreciate that answer?"),
    ],
)
async def test_nor_when_the_voice_detector_hears_a_short_pause_between_them(fragments):
    listener, delivered, model, clock = on_air()
    for when, speech, final in spoken(fragments, at=clock.now):
        at(listener, clock, when)
        match speech:
            case Speech.STARTED:
                listener.speech_started(LEFT)
            case Speech.FINAL:
                listener.hear(LEFT, final)
            case Speech.ENDED:
                listener.speech_ended(LEFT)
    at(listener, clock, clock.now + MAX_WAIT + 0.1)
    await listener.heard()
    assert not paused(delivered)
    assert [sentence for _, sentence in model.checked] == [" ".join(fragments)]


async def test_a_request_to_stop_followed_by_silence_pauses_once_the_grace_has_passed():
    listener, delivered, model, clock = on_air()
    listener.speech_started(LEFT)
    clock.now += 1.0
    listener.hear(LEFT, "I want to stop.")
    listener.speech_ended(LEFT)
    stopped = clock.now
    at(listener, clock, stopped + GRACE - 0.05)
    assert not paused(delivered), "the dater may still go on"
    at(listener, clock, stopped + 0.7)
    assert paused(delivered) and DISTRESSED in cues(delivered)
    assert model.checked == [], "an unmistakable request needs no model"
    kind_line = next(d for d in delivered if DISTRESSED in d.cues)
    assert kind_line.sentence_end == stopped
    assert kind_line.closed_at == pytest.approx(stopped + GRACE)


@pytest.mark.parametrize(
    "finals",
    [("I want to stop, I really can't do this.",), ("I want to stop,", "I really can't do this")],
)
async def test_a_request_to_stop_with_more_of_the_same_pauses(finals):
    listener, delivered, _, clock = on_air()
    await say(listener, clock, *finals)
    assert paused(delivered)


async def test_a_split_question_to_nova_gets_one_reply_to_the_whole_utterance():
    listener, delivered, model, clock = on_air()
    await say(listener, clock, "Nova,", "what's your favourite film?")
    assert replies(delivered) == [
        Say(Moment.REPLY, about=LEFT, detail="Nova, what's your favourite film?")
    ]
    assert [sentence for _, sentence in model.checked] == ["Nova, what's your favourite film?"]


@pytest.mark.parametrize(
    ("words", "to_nova"),
    [
        ("Did you hear what Nova said?", False),
        ("Nova's questions are the best part.", False),
        ("What do you think, Nova?", True),
        ("Nova, tea or coffee?", True),
        ("Hey Nova, tea or coffee?", True),
        ("OK so Nova, who goes first?", True),
        ("nova what's next", True),
        ("I think tea, honestly.", False),
    ],
)
def test_words_are_to_nova_only_when_nova_is_addressed(words, to_nova):
    assert addresses_nova(words) is to_nova


async def test_talking_about_nova_gets_no_reply_but_is_still_checked():
    listener, delivered, model, clock = on_air()
    await say(listener, clock, "Did you hear what Nova said?")
    assert replies(delivered) == []
    assert [sentence for _, sentence in model.checked] == ["Did you hear what Nova said?"]


async def test_naming_nova_last_gets_a_reply():
    listener, delivered, _, clock = on_air()
    await say(listener, clock, "What do you think, Nova?")
    assert [reply.detail for reply in replies(delivered)] == ["What do you think, Nova?"]


async def test_the_meaning_layer_runs_once_per_utterance_however_many_finals_it_has():
    listener, _, model, clock = on_air()
    await say(listener, clock, "So my weekend", "was mostly baking", "and a long walk.")
    await say(listener, clock, "What about yours?")
    assert [sentence for _, sentence in model.checked] == [
        "So my weekend was mostly baking and a long walk.",
        "What about yours?",
    ]
    assert model.checked[1][0] == "So my weekend was mostly baking and a long walk."


async def test_a_final_after_the_utterance_closed_starts_the_next_one():
    listener, delivered, model, clock = on_air()
    listener.speech_ended(LEFT)
    listener.hear(LEFT, "Nova,")
    at(listener, clock, clock.now + GRACE)
    listener.hear(LEFT, "what's your favourite film?")
    at(listener, clock, clock.now + GRACE)
    await listener.heard()
    assert [sentence for _, sentence in model.checked] == ["Nova,", "what's your favourite film?"]
    assert [reply.detail for reply in replies(delivered)] == ["Nova,"], (
        "the second half no longer addresses Nova"
    )


async def test_late_transcripts_never_keep_an_utterance_open_past_the_most_nova_waits():
    listener, delivered, model, clock = on_air()
    listener.speech_started(LEFT)
    clock.now += 1.0
    listener.speech_ended(LEFT)
    stopped = clock.now
    for k, final in enumerate(("Nova,", "tea", "or", "coffee,", "honestly?")):
        at(listener, clock, stopped + 0.3 * k)  # each inside the grace of the last
        listener.hear(LEFT, final)
    at(listener, clock, stopped + MAX_WAIT - 0.05)
    await listener.heard()
    assert model.checked == []
    at(listener, clock, stopped + MAX_WAIT)
    await listener.heard()
    assert [sentence for _, sentence in model.checked] == ["Nova, tea or coffee, honestly?"]
    assert next(d for d in delivered if d.hold).closed_at == pytest.approx(stopped + MAX_WAIT)


async def test_a_pause_inside_the_grace_keeps_one_utterance():
    listener, _, model, clock = on_air()
    listener.speech_started(LEFT)
    at(listener, clock, clock.now + 1.0)
    listener.hear(LEFT, "Nova,")
    listener.speech_ended(LEFT)
    at(listener, clock, clock.now + GRACE - 0.1)
    listener.speech_started(LEFT)  # the dater goes on before the grace is up
    at(listener, clock, clock.now + 1.0)
    listener.hear(LEFT, "tea or coffee?")
    listener.speech_ended(LEFT)
    at(listener, clock, clock.now + GRACE)
    await listener.heard()
    assert [sentence for _, sentence in model.checked] == ["Nova, tea or coffee?"]


async def test_a_request_to_stop_is_acted_on_after_the_cap_if_the_dater_keeps_talking():
    """The voice detector never hears them stop: the words so far still end with the
    request, so Nova acts MATCH_CAP seconds after hearing it, while they speak."""
    listener, delivered, model, clock = on_air()
    listener.speech_started(LEFT)
    at(listener, clock, clock.now + 1.0)
    listener.hear(LEFT, "I want to stop.")
    heard_at = clock.now
    at(listener, clock, heard_at + 1.0)
    listener.hear(LEFT, "Sorry, it's just a lot.")
    at(listener, clock, heard_at + MATCH_CAP - 0.05)
    assert not paused(delivered)
    at(listener, clock, heard_at + MATCH_CAP)
    assert paused(delivered) and model.checked == []
    kind_line = next(d for d in delivered if DISTRESSED in d.cues)
    assert kind_line.sentence_end == heard_at, "measured from when the request was heard"
    listener.hear(LEFT, "Anyway.")
    listener.speech_ended(LEFT)
    at(listener, clock, clock.now + GRACE)
    await listener.heard()
    assert [sentence for _, sentence in model.checked] == ["Anyway."], "a new utterance"
    assert [c.instant for c in listener.closed] == [True, False]


async def test_a_dater_who_carries_on_past_the_cap_is_not_paused():
    listener, delivered, model, clock = on_air()
    listener.speech_started(LEFT)
    at(listener, clock, clock.now + 1.0)
    listener.hear(LEFT, "I want to stop")
    at(listener, clock, clock.now + 2.0)
    listener.hear(LEFT, "eating so much sugar, so I've been")
    at(listener, clock, clock.now + MATCH_CAP)
    listener.hear(LEFT, "baking with dates instead.")
    listener.speech_ended(LEFT)
    at(listener, clock, clock.now + GRACE)
    await listener.heard()
    assert not paused(delivered)
    assert len(model.checked) == 1


async def test_a_request_heard_with_the_last_few_seconds_still_counts():
    """A phrase split across two utterances: the instant layer joins them, as before."""
    listener, delivered, _, clock = on_air()
    await say(listener, clock, "I'm not")
    await say(listener, clock, "comfortable.")
    assert paused(delivered)


async def test_each_dater_has_their_own_utterance():
    listener, delivered, model, clock = on_air()
    listener.speech_started(LEFT)
    listener.speech_started(RIGHT)
    at(listener, clock, clock.now + 1.0)
    listener.hear(LEFT, "Nova,")
    listener.hear(RIGHT, "I love that question.")
    listener.speech_ended(RIGHT)
    at(listener, clock, clock.now + GRACE)
    await listener.heard()
    assert [sentence for _, sentence in model.checked] == ["I love that question."]
    listener.hear(LEFT, "what's your favourite film?")
    listener.speech_ended(LEFT)
    at(listener, clock, clock.now + GRACE)
    await listener.heard()
    assert [r.detail for r in replies(delivered)] == ["Nova, what's your favourite film?"]


async def test_next_due_is_when_the_listener_next_has_something_to_do():
    listener, _, _, clock = on_air()
    assert listener.next_due is None
    listener.speech_started(LEFT)
    listener.hear(LEFT, "Nova, tea or")
    assert listener.next_due is None, "still speaking"
    listener.hear(LEFT, "I need a moment.")
    assert listener.next_due == clock.now + MATCH_CAP
    clock.now += 0.5
    listener.speech_ended(LEFT)
    assert listener.next_due == clock.now + GRACE


async def test_listening_closes_utterances_on_time_without_anyone_else_ticking():
    runner = ShowRunner(REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=NoVotes(), first_up=LEFT)
    delivered: list[OnAirCues] = []
    listener = OnAirListener(
        runner,
        DistressCheck(Model(), timeout=1.0),
        deliver=delivered.append,
        clock=time.monotonic,
        grace=0.05,
        max_wait=0.1,
    )
    listening = asyncio.create_task(listener.listen(every=0.01))
    try:
        listener.hear(LEFT, "I want to stop.")
        listener.speech_ended(LEFT)
        for _ in range(100):
            if delivered:
                break
            await asyncio.sleep(0.01)
    finally:
        listening.cancel()
    assert cues(delivered) == [DISTRESSED], "before the date starts: the moderator only"


# --- What the listener hands on, and when -------------------------------------------------


async def test_beats_already_due_come_first_and_are_never_the_reply():
    listener, delivered, _, clock = on_air()
    listener.speech_ended(LEFT)
    listener.hear(LEFT, "Nova, tea or coffee?")
    at(listener, clock, clock.now + GRACE)
    await listener.heard()
    beats, reply = delivered[0], delivered[-1]
    assert beats.sentence_end is None and beats.hold is None
    assert Moment.FIRST_QUESTION in [c.moment for c in beats.cues if isinstance(c, Say)]
    assert reply.cues == [Say(Moment.REPLY, about=LEFT, detail="Nova, tea or coffee?")]
    assert reply.sentence_end == ON_AIR_AT
    assert reply.closed_at == pytest.approx(ON_AIR_AT + GRACE)
    assert reply.hold is not None and reply.hold.result() is Verdict.NO


async def test_the_reply_is_held_until_the_verdicts_cues_have_been_handed_on():
    words = "Nova, I want to stop talking to him."
    listener, delivered, _, clock = on_air({words: "yes"})
    listener.speech_ended(LEFT)
    listener.hear(LEFT, words)
    at(listener, clock, clock.now + GRACE)
    reply = delivered[-1]
    assert reply.cues[0].moment is Moment.REPLY and not reply.hold.done(), "held"
    await listener.heard()
    verdict = delivered[-1]
    assert verdict.cues[:2] == [DISTRESSED, verdict.cues[1]]
    assert Say(Moment.NEED_A_MOMENT, text=script.DISTRESS_PAUSE) in verdict.cues
    assert reply.hold.result() is Verdict.YES, "the reply is dropped"
    assert [c.verdict for c in listener.checks] == [Verdict.YES]


async def test_an_unmistakable_request_needs_no_check_and_is_never_answered():
    listener, delivered, model, clock = on_air()
    await say(listener, clock, "Nova, I want to stop.")
    assert model.checked == []
    stop = delivered[-1]
    assert stop.cues[0] == DISTRESSED and stop.hold is None
    assert Moment.REPLY not in [c.moment for c in stop.cues if isinstance(c, Say)]


async def test_what_a_dater_says_before_the_date_starts_reaches_the_moderator():
    listener, delivered, _, clock = on_air(started=False)
    await say(listener, clock, "I want to stop.")
    assert cues(delivered) == [DISTRESSED]


async def test_the_evaluation_can_set_up_what_a_dater_said_before_without_checking_it():
    listener, _, model, clock = on_air()
    listener.remember(LEFT, "He keeps commenting on my body.", at=ON_AIR_AT - 2)
    await say(listener, clock, "Can we not do this?")
    assert model.checked == [("He keeps commenting on my body.", "Can we not do this?")]
