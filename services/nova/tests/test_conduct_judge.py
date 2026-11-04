"""The second conduct check (decision 0006, 2026-11-02): banter, self put-downs and
put-downs, the wait for the other dater's reaction, and what it is and isn't sent."""

import json

import pytest

from nova import banter_set, conduct_set, script
from nova.cards import Seat
from nova.conduct_judge import (
    JUDGE_CONTEXT,
    JUDGE_SCHEMA,
    JUDGE_SYSTEM,
    REACTION_WAIT,
    Aim,
    ConductJudge,
    Judged,
    JudgeError,
    Mutual,
    decide,
    judge_prompt,
    parse,
)
from nova.cues import AlertModerator, Conduct, ModeratorAlert, Moment, Say
from nova.date_context import DateContext
from nova.decisions import InMemoryDecisionDesk
from nova.distress import DistressCheck
from nova.evaluation import FakeClock, banter_misses, hear_on_air, run_banter
from nova.fixtures import REHEARSAL_PAIR
from nova.on_air import OnAirCues, OnAirListener
from nova.privacy import PrivacyBlocked, PrivacyPolicy
from nova.show_runner import NoVotes, ShowRunner
from nova.timing import FULL

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT
FREE_TALK = FULL.free_talk + 20.0
KIND = Say(Moment.KEEP_IT_KIND, text=script.KEEP_IT_KIND)
FLAG = AlertModerator(ModeratorAlert.CONDUCT_FLAG, LEFT, Conduct.UNKIND)
UNKIND_LABEL = '{"label": "no", "conduct": "unkind"}'
ROAST = "Your puns are a hostage situation."


def answer(aim: str, mutual: str, verdict: str) -> str:
    return json.dumps({"aim": aim, "mutual": mutual, "verdict": verdict})


class Model:
    """The safety check labels ROAST unkind; the second check answers as told, and keeps
    what it was asked."""

    name = "fake/judge"
    endpoint = "http://localhost:11434/v1"

    def __init__(self, judged: str = answer("playful", "no", "unkind")) -> None:
        self.judged = judged
        self.asked: list[str] = []

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        if system == JUDGE_SYSTEM:
            self.asked.append(prompt)
            if self.judged == "error":
                raise RuntimeError("down")
            return self.judged
        sentence = prompt.split('"sentence": "')[1].split('"')[0]
        return UNKIND_LABEL if sentence == ROAST else '{"label": "no", "conduct": "none"}'

    async def write(self, *, system: str, prompt: str) -> str:
        return "Here we go, Maya and Theo."


# --- What the answers mean -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("aim", "mutual", "verdict", "outcome"),
    [
        (Aim.SELF, Mutual.UNCLEAR, Judged.UNKIND, Judged.SELF),  # mocking themselves
        (Aim.PLAYFUL, Mutual.YES, Judged.BANTER, Judged.BANTER),
        (Aim.PLAYFUL, Mutual.YES, Judged.UNKIND, Judged.UNKIND),  # never more lenient
        (Aim.PLAYFUL, Mutual.NO, Judged.BANTER, Judged.UNKIND),  # not in on it
        (Aim.PERSONAL, Mutual.YES, Judged.BANTER, Judged.UNKIND),  # looks: never banter
        (Aim.PLAYFUL, Mutual.UNCLEAR, Judged.BANTER, Judged.BANTER),
        (Aim.PLAYFUL, Mutual.UNCLEAR, Judged.UNSURE, Judged.UNSURE),
        (Aim.PLAYFUL, Mutual.UNCLEAR, Judged.SELF, Judged.UNSURE),  # inconsistent
    ],
)
def test_what_nova_does_from_the_three_answers(aim, mutual, verdict, outcome):
    assert decide(aim, mutual, verdict) is outcome


def test_an_answer_that_isnt_the_schemas_is_no_answer():
    assert parse(answer("playful", "yes", "banter")) == (Aim.PLAYFUL, Mutual.YES, Judged.BANTER)
    for bad in ("banter", "{}", '{"aim": "rude", "mutual": "yes", "verdict": "banter"}', "[]"):
        assert parse(bad) is None


def test_the_schema_offers_exactly_the_answers_decide_reads():
    props = JUDGE_SCHEMA["properties"]
    assert props["aim"]["enum"] == [a.value for a in Aim]
    assert props["mutual"]["enum"] == [m.value for m in Mutual]
    assert props["verdict"]["enum"] == [j.value for j in Judged]


def test_the_prompt_carries_this_date_where_the_privacy_policy_can_see_it():
    """The daters' turns go under "this_date", so Guarded holds the request back from a
    recipient dialogue memory may not reach (decision 0010)."""
    prompt = judge_prompt(ROAST, [(False, "Here's my best pun."), (True, "Please no.")])
    blocked = PrivacyPolicy()  # no opt-in
    remote = type("Remote", (), {"name": "anthropic/claude", "endpoint": None})()
    with pytest.raises(PrivacyBlocked):
        blocked.check(remote, JUDGE_SYSTEM, prompt)
    blocked.check(Model(), JUDGE_SYSTEM, prompt)  # local: allowed
    data = json.loads(prompt.split("<data>")[1].split("</data>")[0])
    assert [turn["by"] for turn in data["this_date"]] == ["other", "speaker"]
    assert data["sentence"] == ROAST and data["reaction"] is None


def test_the_system_prompt_says_banter_is_part_of_a_good_date_and_tone_is_lost():
    assert "Banter is part of a good date" in JUDGE_SYSTEM
    assert "Speech-to-text drops laughter" in JUDGE_SYSTEM


async def test_a_failed_or_slow_check_is_no_answer_and_counts_as_unsure():
    judgement = await ConductJudge(Model("error")).judge(ROAST, [])
    assert judgement.judged is None and judgement.error is JudgeError.ERROR
    assert judgement.outcome is Judged.UNSURE
    unread = await ConductJudge(Model("not json")).judge(ROAST, [])
    assert unread.error is JudgeError.UNREAD


async def test_only_codes_are_logged(caplog):
    caplog.set_level("INFO", logger="nova")
    await ConductJudge(Model()).judge(ROAST, [(False, "Here's my best pun.")], reaction="Rude.")
    assert "conduct_judge judged=unkind" in caplog.text
    assert "hostage" not in caplog.text and "pun" not in caplog.text and "Rude" not in caplog.text


# --- On air: the reaction, the wait, what reaches the show -------------------------------


def runner() -> ShowRunner:
    show = ShowRunner(REHEARSAL_PAIR, desk=InMemoryDecisionDesk(), poll=NoVotes(), first_up=LEFT)
    show.start(0.0)
    show.tick(FREE_TALK)
    return show


async def say(listener: OnAirListener, clock: FakeClock, seat: Seat, words: str) -> None:
    listener.speech_started(seat)
    clock.now += 1.0
    listener.hear(seat, words)
    listener.speech_ended(seat)
    clock.now += 1.0  # past the grace: the utterance closes
    listener.tick()


def listening(model: Model, context: DateContext | None = None):
    clock = FakeClock(FREE_TALK)
    delivered: list[OnAirCues] = []
    listener = OnAirListener(
        runner(),
        DistressCheck(model, timeout=1.0),
        deliver=delivered.append,
        clock=clock,
        context=context,
        judge=ConductJudge(model),
    )
    return listener, clock, delivered


def cues(delivered: list[OnAirCues]) -> list:
    return [cue for d in delivered for cue in d.cues]


async def test_the_other_daters_reaction_is_waited_for_and_sent_with_it():
    model = Model(answer("playful", "yes", "banter"))
    listener, clock, delivered = listening(model)
    await say(listener, clock, RIGHT, "What do you call a fake noodle? An impasta.")
    await say(listener, clock, LEFT, ROAST)
    await listener.heard()
    assert model.asked == [], "waiting for the other dater"
    await say(listener, clock, RIGHT, "And you're my hostage. Here's number two.")
    await listener.heard()
    data = json.loads(model.asked[0].split("<data>")[1].split("</data>")[0])
    assert data["reaction"] == "And you're my hostage. Here's number two."
    assert data["this_date"] == [
        {"by": "other", "said": "What do you call a fake noodle? An impasta."}
    ]
    assert KIND not in cues(delivered) and FLAG not in cues(delivered), "banter: Nova stays out"


async def test_with_no_reaction_it_is_judged_after_the_wait_and_a_put_down_gets_the_kind_line():
    model = Model()
    listener, clock, delivered = listening(model)
    await say(listener, clock, LEFT, ROAST)
    await listener.heard()
    clock.now += REACTION_WAIT - 1.0
    listener.tick()
    await listener.heard()
    assert model.asked == []
    clock.now += 1.0
    listener.tick()
    await listener.heard()
    assert len(model.asked) == 1 and '"reaction": null' in model.asked[0]
    assert KIND in cues(delivered)


async def test_nova_latest_line_goes_with_it_so_that_choice_can_be_the_daters_own():
    model = Model(answer("self", "unclear", "self"))
    context = DateContext()
    context.said("Sweet or savoury?")
    listener, clock, delivered = listening(model, context)
    await say(listener, clock, LEFT, ROAST)
    listener.flush()  # the date ends: judged as it stands
    await listener.heard()
    assert '"nova_just_said": "Sweet or savoury?"' in model.asked[0]
    assert KIND not in cues(delivered) and FLAG not in cues(delivered)


async def test_no_answer_alerts_the_moderator_and_nova_says_nothing():
    listener, clock, delivered = listening(Model("error"))
    await say(listener, clock, LEFT, ROAST)
    listener.flush()
    await listener.heard()
    assert FLAG in cues(delivered) and KIND not in cues(delivered)


async def test_a_remark_still_waiting_when_the_date_is_cut_off_reaches_the_moderator():
    model = Model()
    listener, clock, delivered = listening(model)
    await say(listener, clock, LEFT, ROAST)
    await listener.heard()  # labelled unkind: waiting for a reaction
    listener.cancel()
    assert FLAG in cues(delivered) and model.asked == []


async def test_its_memory_is_both_daters_last_minute_and_a_half_and_goes_with_the_date():
    model = Model()
    listener, clock, _ = listening(model)
    await say(listener, clock, RIGHT, "An old line from ages ago.")
    clock.now += JUDGE_CONTEXT + 1.0
    listener.tick()
    await say(listener, clock, RIGHT, "A pun, just now.")
    await say(listener, clock, LEFT, ROAST)
    listener.flush()
    await listener.heard()
    assert "ages ago" not in model.asked[0] and "A pun, just now." in model.asked[0]
    listener.cancel()
    assert len(listener._said) == 0 and listener._unkind == []


# --- The banter set and its gate ------------------------------------------------------------


def test_the_banter_halves_are_made_up_moments_with_no_laughter_and_never_overlap():
    dev = {(s.sentence, s.reaction) for s in banter_set.DEV_HALF}
    held = {(s.sentence, s.reaction) for s in banter_set.HELD_OUT_HALF}
    assert dev.isdisjoint(held)
    dev_sentences = {s.sentence for s in banter_set.DEV_HALF}
    assert dev_sentences.isdisjoint(s.sentence for s in banter_set.HELD_OUT_HALF)
    for sample in (*banter_set.DEV_HALF, *banter_set.HELD_OUT_HALF):
        assert sample.expected in (banter_set.STAY_OUT, banter_set.UNKIND)
        assert sample.kind in banter_set.KINDS
        words = " ".join([sample.sentence, sample.reaction or ""]).lower().split()
        assert "ha" not in words and "haha" not in words, "transcripts carry no laughter"
        stay = sample.kind in (banter_set.BANTER, banter_set.SELF)
        assert (sample.expected == banter_set.STAY_OUT) is stay
    for half in (banter_set.DEV_HALF, banter_set.HELD_OUT_HALF):
        assert {s.kind for s in half} == set(banter_set.KINDS), "every kind in each half"


def test_the_judges_examples_are_in_neither_half():
    for sample in (*banter_set.DEV_HALF, *banter_set.HELD_OUT_HALF):
        assert sample.sentence not in JUDGE_SYSTEM


async def test_the_banter_run_measures_what_nova_does_and_gates_on_it():
    """A second check that calls everything a put-down fails the left-alone target, through
    the real listener, both checks and the show runner."""

    class Harsh(Model):
        async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
            if system == JUDGE_SYSTEM:
                return answer("playful", "no", "unkind")
            return UNKIND_LABEL  # every sentence looks unkind to the screen

    results = await run_banter(Harsh(), timeout=1.0, samples=banter_set.DEV_HALF)
    stay = [r for r in results if r.sample.expected == banter_set.STAY_OUT]
    assert all(conduct_set.UNKIND in r.found for r in stay)
    assert any("left alone" in miss for miss in banter_misses(results))


async def test_hear_on_air_waits_for_a_judged_remark_and_its_reaction():
    model = Model(answer("playful", "yes", "banter"))
    heard = await hear_on_air(
        DistressCheck(model, timeout=1.0),
        ROAST,
        judge=ConductJudge(model),
        before=((False, "What do you call a fake noodle? An impasta."),),
        reaction="And you're my hostage.",
    )
    assert KIND not in heard.cues and len(model.asked) == 1
    assert '"reaction": "And you' in model.asked[0]


# --- A hurt reaction gets the remark before it a second look (2026-11-02) ------------------

SARCASM = "Wow, saint of the year over here."


class Sarcasm(Model):
    """The safety check finds nothing in SARCASM, as Gemma's did; the second check calls it a
    put-down once it sees the reaction."""

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        if system == JUDGE_SYSTEM:
            self.asked.append(prompt)
            return self.judged
        return '{"label": "no", "conduct": "none"}'


@pytest.mark.parametrize(
    "hurt", ["Oh. Right.", "Okay, that's a bit mean.", "None taken, I guess.", "Please stop."]
)
async def test_a_hurt_reaction_gets_the_remark_before_it_a_second_look(hurt):
    model = Sarcasm()
    listener, clock, delivered = listening(model)
    await say(listener, clock, RIGHT, "I volunteer at an animal shelter on weekends.")
    await say(listener, clock, LEFT, SARCASM)
    await listener.heard()  # the first check finds nothing
    await say(listener, clock, RIGHT, hurt)
    await listener.heard()
    assert len(model.asked) == 1 and SARCASM in model.asked[0]
    if hurt == "Please stop.":  # their own pause comes first: the moderator hears of both
        assert FLAG in cues(delivered) and KIND not in cues(delivered)
        assert AlertModerator(ModeratorAlert.DATER_DISTRESSED, RIGHT) in cues(delivered)
    else:
        assert KIND in cues(delivered)


async def test_a_hurt_reaction_before_the_remarks_check_is_in_still_gets_a_look():
    model = Sarcasm()
    listener, clock, delivered = listening(model)
    await say(listener, clock, LEFT, SARCASM)
    await say(listener, clock, RIGHT, "Wow. Okay.")  # before the first check has answered
    await listener.heard()
    assert len(model.asked) == 1 and KIND in cues(delivered)


@pytest.mark.parametrize(
    "reply", ["Ha, guilty. What about you?", "Ouch. Fine, my turn: what's your worst habit?"]
)
async def test_an_ordinary_reply_or_a_comeback_never_triggers_a_second_look(reply):
    model = Sarcasm()
    listener, clock, delivered = listening(model)
    await say(listener, clock, LEFT, SARCASM)
    await say(listener, clock, RIGHT, reply)
    await listener.heard()
    assert model.asked == [] and KIND not in cues(delivered)


async def test_a_late_hurt_reaction_is_about_something_else():
    model = Sarcasm()
    listener, clock, _ = listening(model)
    await say(listener, clock, LEFT, SARCASM)
    await listener.heard()
    clock.now += 30.0
    await say(listener, clock, RIGHT, "Please stop.")
    await listener.heard()
    assert model.asked == []


def test_mild_words_count_as_hurt_only_in_a_short_reaction():
    from nova.conduct_judge import hurt

    assert hurt("Okay, that's a bit mean.") and hurt("That actually stung, honestly, a lot.")
    assert hurt("None taken, I guess, but that was a really long way of saying it to me.")
    mock = "Hey, that's a bit harsh, don't you think? I'll have you know my lecture was riveting."
    assert not hurt(mock), "mock offence and a comeback"
    assert not hurt("Ouch. Fine, my turn: what's your worst habit?")


TEASE_NOVA = "Nova, are you just programmed to be this boring?"


class TeasesNova(Model):
    """The safety check labels the tease of Nova unkind, as Gemma's did."""

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        if system == JUDGE_SYSTEM:
            self.asked.append(prompt)
            return self.judged
        if "programmed" in prompt:
            return UNKIND_LABEL
        return '{"label": "no", "conduct": "none"}'


@pytest.mark.parametrize(
    ("judged", "action", "kind_line"),
    [
        (answer("playful", "unclear", "banter"), "speak", False),
        (answer("personal", "no", "unkind"), "drop", True),
        ("error", "drop", False),
    ],
)
async def test_a_tease_of_nova_is_judged_at_once_and_nova_plays_along(judged, action, kind_line):
    """2026-11-02: "Nova, are you just programmed to be this boring?" went unanswered."""
    from nova.on_air import ReplyAction

    model = TeasesNova(judged)
    listener, clock, delivered = listening(model)
    await say(listener, clock, LEFT, TEASE_NOVA)
    await listener.heard()
    assert len(model.asked) == 1 and '"said_to_nova": true' in model.asked[0]
    holds = [d.hold for d in delivered if d.hold is not None]
    assert [h.result() for h in holds] == [ReplyAction(action)]
    assert (KIND in cues(delivered)) is kind_line
    assert "Nova" in JUDGE_SYSTEM and "teasing or mocking Nova itself is banter" in JUDGE_SYSTEM
