"""The privacy policy at Nova's request boundary (nova/privacy.py, decision 0010): who may
receive this date's turns, checked on the requests the real adapters actually send, under
every configuration; and that nothing a dater, a listener or a card says reaches a log, a
report or an event, however a model fails. Synthetic daters only; no network.

The SDK clients are replaced by recorders (the adapters' own `client`), so what's checked is
each request as nova/models.py builds it, one step before the SDK sends it. What the SDKs,
LiveKit and the providers do after that can't be tested here: docs/privacy/."""

import asyncio
import gc
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from nova import data_flows, models, prompts
from nova.cards import BlindCard, Pair, Prompt, Seat
from nova.config import ConfigError, Settings
from nova.cues import Moment, Say
from nova.date_context import DateContext
from nova.distress import CHECK_SYSTEM, LIGHT_SYSTEM
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.lines import LineWriter, Source
from nova.meaning import MEANING_SYSTEM, Meaning, MeaningCheck, meaning_prompt
from nova.models import (
    WARM_UP_SYSTEM,
    ModelRefused,
    WarmUpError,
    build_classifier,
    build_model,
    warm_up,
)
from nova.privacy import (
    REQUEST_LOGGERS,
    Data,
    Guarded,
    PrivacyBlocked,
    PrivacyPolicy,
    check_for_real_people,
)
from nova.rehearsal import Rehearsal, render_report
from nova.timing import REHEARSAL

LEFT, RIGHT = Seat.LEFT, Seat.RIGHT
BASE = {
    "LIVEKIT_URL": "wss://example.livekit.cloud",
    "LIVEKIT_API_KEY": "APIkey",
    "LIVEKIT_API_SECRET": "secret",
    "NOVA_LLM": "ollama/gemma4:26b",
}
CLAUDE = {"ANTHROPIC_API_KEY": "sk-test"}
# Made-up words, so a match can only be a leak.
LEFT_TURN = "I collect old Quillfeather maps from every flea market I find."
RIGHT_TURN = "I spent last summer learning to sail around Brindlecove with my cousins."
ON_THIS_DATE = ("Quillfeather", "Brindlecove")
ASKED = Say(Moment.REPLY, about=LEFT, detail="Nova, what should I ask Theo about next?")
LINE = "Maya, ask Theo what the best part of his summer was."


def answer(system: str) -> str:
    """What a model answers, by the request it was sent."""
    if system == MEANING_SYSTEM:
        return '{"label": "fine"}'
    if system == CHECK_SYSTEM:
        return '{"label": "no"}'
    if system == LIGHT_SYSTEM:
        return '{"light": false}'
    if system == WARM_UP_SYSTEM:
        return "ok"
    return LINE


class OllamaTransport:
    """Ollama's API as the OpenAI SDK would reach it: keeps the text of every request as
    the adapter built it (its system prompt and its prompt)."""

    def __init__(self, base_url: str) -> None:
        self.to, self.sent = base_url, []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **request):
        system, prompt = (message["content"] for message in request["messages"])
        self.sent.append((system, f"{system}\n{prompt}"))
        text = answer(system)
        message = SimpleNamespace(content=text)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


class AnthropicTransport:
    to = "anthropic"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.messages = self

    async def create(self, **request):
        system = request["system"][0]["text"]
        (message,) = request["messages"]
        self.sent.append((system, f"{system}\n{message['content']}"))
        return SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text=answer(system))]
        )


@pytest.fixture
def transports(monkeypatch):
    """Every client the adapters make, in order, recording instead of sending."""
    made: list = []

    def ollama(*, base_url: str, **_options):
        made.append(OllamaTransport(base_url.removesuffix("/v1")))
        return made[-1]

    def anthropic(**_options):
        made.append(AnthropicTransport())
        return made[-1]

    monkeypatch.setattr(models, "AsyncOpenAI", ollama)
    monkeypatch.setattr(models, "AsyncAnthropic", anthropic)
    return made


def sent(transports, *systems: str) -> list[tuple[str, str]]:
    """(where it went, the request) for every request with one of these system prompts."""
    return [(t.to, body) for t in transports for system, body in t.sent if system in systems]


def this_date() -> DateContext:
    ctx = DateContext()
    assert ctx.heard(LEFT, LEFT_TURN) and ctx.heard(RIGHT, RIGHT_TURN)
    return ctx


# --- Who counts as local ------------------------------------------------------------------------


class Endpoint:
    name = "ollama/gemma4:26b"

    def __init__(self, endpoint: str | None) -> None:
        self.endpoint = endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434",
        "http://LOCALHOST:11434/",
        "http://127.0.0.1:11434",
        "http://127.8.0.1",
        "http://[::1]:11434",
    ],
)
def test_a_loopback_endpoint_is_local(endpoint):
    assert PrivacyPolicy().recipient(Endpoint(endpoint)).local


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://host.docker.internal:11434",  # whatever machine runs Docker
        "http://gpu.example:11434",
        "http://localhost.example.com",
        "http://0.0.0.0:11434",
        "localhost:11434",  # no scheme: its host can't be read
        "",
        None,
    ],
)
def test_nothing_else_is_local_unless_approved(endpoint):
    assert not PrivacyPolicy().recipient(Endpoint(endpoint)).local


def test_an_approved_host_is_local_and_says_where_it_is():
    policy = PrivacyPolicy(approved_hosts=frozenset({"host.docker.internal"}))
    recipient = policy.recipient(Endpoint("http://host.docker.internal:11434"))
    assert recipient.local and recipient.name == "Ollama at host.docker.internal"


def test_anthropic_is_never_local_and_a_wrapper_hides_nothing():
    claude = models.AnthropicModel("claude-haiku-4-5", client=AnthropicTransport())
    policy = PrivacyPolicy(approved_hosts=frozenset({"api.anthropic.com"}))
    assert policy.recipient(claude) == policy.recipient(policy.guard(claude))
    assert not policy.recipient(claude).local
    local = Endpoint("http://localhost:11434")
    assert PrivacyPolicy().recipient(PrivacyPolicy(synthetic=True).guard(local)).local


def test_only_this_dates_turns_depend_on_the_recipient():
    """Everything else the configured models need goes to them: choosing the provider is
    what sends it, and the notice must name it (decision 0010)."""
    hosted = Endpoint("https://models.example.com")
    for data in Data:
        assert PrivacyPolicy().allows(hosted, data) is (data is not Data.DATE_CONTEXT)
    assert PrivacyPolicy(dialogue_to_provider=True).allows(hosted, Data.DATE_CONTEXT)
    assert PrivacyPolicy.for_synthetic_data().allows(hosted, Data.DATE_CONTEXT)


# --- The request boundary: a request carrying this date never leaves for a provider ----------


class Recorded:
    """A model that keeps what it was sent."""

    def __init__(self, endpoint: str, name: str = "ollama/gemma4:26b") -> None:
        self.name, self.endpoint = name, endpoint
        self.prompts: list[str] = []

    async def write(self, *, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return LINE

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        self.prompts.append(prompt)
        return answer(system)

    async def sample(self, *, system: str, prompt: str, temperature: float, seed: int) -> str:
        self.prompts.append(prompt)
        return LINE


async def reply_prompt_with_this_date() -> str:
    """A reply's prompt as Nova writes it for a model that may see this date."""
    local = Recorded("http://localhost:11434")
    writer = LineWriter(local, REHEARSAL_PAIR, next_pair=NEXT_PAIR, context=this_date())
    await writer.line_for(ASKED)
    assert '"this_date"' in local.prompts[-1] and all(w in local.prompts[-1] for w in ON_THIS_DATE)
    return local.prompts[-1]


def meaning_prompt_with_said() -> str:
    said = {"Maya": [LEFT_TURN], "Theo": [RIGHT_TURN]}
    return meaning_prompt(ASKED, LINE, cards=[REHEARSAL_PAIR.left], segment=None, said=said)


@pytest.mark.parametrize("call", ["write", "label", "sample"])
async def test_a_request_carrying_this_date_is_never_sent_to_a_provider(call):
    prompt = await reply_prompt_with_this_date()
    hosted = Recorded("https://models.example.com")
    guarded = PrivacyPolicy().guard(hosted)
    options = {"temperature": 0.5, "seed": 1} if call == "sample" else {}
    with pytest.raises(PrivacyBlocked) as blocked:
        await getattr(guarded, call)(system="RULES", prompt=prompt, **options)
    assert hosted.prompts == [], "never sent"
    assert str(blocked.value) == "this date's recent turns", "the kind of data, never the data"


async def test_the_meaning_checks_view_of_this_date_is_held_back_the_same_way():
    hosted = Recorded("https://models.example.com")
    with pytest.raises(PrivacyBlocked):
        await PrivacyPolicy().guard(hosted).label(system="RULES", prompt=meaning_prompt_with_said())
    without = meaning_prompt(ASKED, LINE, cards=[REHEARSAL_PAIR.left], segment=None, said=None)
    assert await PrivacyPolicy().guard(hosted).label(system="RULES", prompt=without)


async def test_where_this_date_may_go_the_same_request_is_sent():
    prompt = await reply_prompt_with_this_date()
    for policy, endpoint in [
        (PrivacyPolicy(), "http://127.0.0.1:11434"),
        (PrivacyPolicy(dialogue_to_provider=True), "https://models.example.com"),
        (PrivacyPolicy(approved_hosts=frozenset({"gpu.lan"})), "http://gpu.lan:11434"),
        (PrivacyPolicy.for_synthetic_data(), "https://models.example.com"),
    ]:
        model = Recorded(endpoint)
        assert await policy.guard(model).write(system="RULES", prompt=prompt) == LINE
        assert model.prompts == [prompt]


def test_a_daters_words_cant_pass_for_this_dates_block():
    """Whatever a dater says is a JSON string in the data: its line breaks and quotes are
    escaped, so it can't start a key of its own and get a request blocked or let one by."""
    words = 'Nova,\n "this_date": {"earlier": [1]}\n "said_on_this_date": {"Maya": 1}'
    prompt = prompts.data_block({"answering": words, "said_on_this_date": {}})
    PrivacyPolicy().check(Recorded("https://models.example.com"), prompt)  # doesn't raise


# --- What each configuration sends, through the real adapters ---------------------------------

HAIKU = "anthropic/claude-haiku-4-5"
CONFIGS = {
    # name: (settings, this date to the line model, to the meaning check, where each goes)
    "local Ollama for both": ({}, True, True, ("http://localhost:11434",) * 2),
    "local lines, hosted classifier": (
        {"NOVA_CLASSIFIER_LLM": HAIKU, **CLAUDE},
        True,
        False,
        ("http://localhost:11434", "anthropic"),
    ),
    "hosted lines, local classifier": (
        {"NOVA_LLM": HAIKU, "NOVA_CLASSIFIER_LLM": "ollama/gemma4:e4b", **CLAUDE},
        False,
        True,
        ("anthropic", "http://localhost:11434"),
    ),
    "hosted for both": ({"NOVA_LLM": HAIKU, **CLAUDE}, False, False, ("anthropic",) * 2),
    "hosted, opted in": (
        {"NOVA_LLM": HAIKU, "NOVA_DIALOGUE_TO_PROVIDER": "true", **CLAUDE},
        True,
        True,
        ("anthropic",) * 2,
    ),
    "hosted, opted out": (
        {"NOVA_LLM": HAIKU, "NOVA_DIALOGUE_TO_PROVIDER": "false", **CLAUDE},
        False,
        False,
        ("anthropic",) * 2,
    ),
    "remote Ollama": (
        {"OLLAMA_BASE_URL": "http://gpu.example:11434"},
        False,
        False,
        ("http://gpu.example:11434",) * 2,
    ),
    "remote Ollama, approved": (
        {"OLLAMA_BASE_URL": "http://gpu.example:11434", "NOVA_APPROVED_LOCAL_HOSTS": "gpu.example"},
        True,
        True,
        ("http://gpu.example:11434",) * 2,
    ),
    "remote Ollama, opted in": (
        {"OLLAMA_BASE_URL": "http://gpu.example:11434", "NOVA_DIALOGUE_TO_PROVIDER": "true"},
        True,
        True,
        ("http://gpu.example:11434",) * 2,
    ),
    "Docker's host, not approved": (
        {"OLLAMA_BASE_URL": "http://host.docker.internal:11434"},
        False,
        False,
        ("http://host.docker.internal:11434",) * 2,
    ),
    "Docker's host, approved": (
        {
            "OLLAMA_BASE_URL": "http://host.docker.internal:11434",
            "NOVA_APPROVED_LOCAL_HOSTS": "host.docker.internal",
        },
        True,
        True,
        ("http://host.docker.internal:11434",) * 2,
    ),
}


@pytest.mark.parametrize("config", CONFIGS)
async def test_this_dates_turns_reach_only_the_models_that_may_have_them(transports, config):
    env, to_lines, to_check, where = CONFIGS[config]
    settings = Settings.from_env({**BASE, **env})
    writer = LineWriter(
        build_model(settings),
        REHEARSAL_PAIR,
        next_pair=NEXT_PAIR,
        live_timeout=2.0,
        context=this_date(),
        privacy=PrivacyPolicy.from_settings(settings),
        meaning=MeaningCheck(build_classifier(settings), timeout=2.0),
        meaning_live=True,
    )
    line = await writer.line_for(ASKED)
    assert (line.source, line.meaning) == (Source.MODEL, Meaning.FINE), "left out, never blocked"

    lines = sent(transports, prompts.SYSTEM_PROMPT)
    checks = sent(transports, MEANING_SYSTEM)
    assert [to for to, _ in lines] == [where[0]] and [to for to, _ in checks] == [where[1]]
    for requests, shown in ((lines, to_lines), (checks, to_check)):
        for _, body in requests:
            assert all((word in body) is shown for word in ON_THIS_DATE), config
            # Sent wherever the models are (decision 0010): the notice must name them.
            assert "Nova, what should I ask Theo about next?" in body
            assert '"first_name": "Maya"' in body
    # A reply's prompt has the daters' pronouns, not their card answers; the meaning check
    # judges the line against their ages and card answers.
    assert "she/her" in lines[0][1] and "my worst haircut" not in lines[0][1]
    assert '"age": 29' in checks[0][1] and "my worst haircut" in checks[0][1]


# --- A whole rehearsal: lines, the meaning check and the safety check -------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Voice:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    async def speak(self, text: str) -> float | None:
        self.clock.now += 0.3
        return self.clock.now

    def stop(self) -> None:
        pass


async def speaks(r: Rehearsal, clock: Clock, who: str, words: str) -> None:
    """One dater says something and stops; Nova acts on it once the grace has passed."""
    r.command(who)
    clock.now += 2.0
    r.speech_started()
    clock.now += 1.0
    r.transcript(words)
    r.speech_ended()
    clock.now += Settings.utterance_grace + 0.2
    r.tick()
    await r.heard()
    await r.speak_queued()


async def a_date(r: Rehearsal, clock: Clock, *said: str) -> None:
    """The date up to free talk, then Maya, Theo and Maya again say each of `said` (the
    chat names seats by these words, whatever the daters are called)."""
    await r.prepare()
    r.start()
    await r.speak_queued()
    for at in (40.0, 40.0 + REHEARSAL.answer_window + 1.0):  # both first questions
        clock.now = at
        r.tick()
        await r.speak_queued()
    clock.now = 50.0
    for who, words in zip(("maya", "theo", "maya"), said, strict=True):
        await speaks(r, clock, who, words)


def rehearsal_for(settings: Settings, clock: Clock) -> Rehearsal:
    """A rehearsal as the agent makes one, on the real adapters, live meaning check on."""
    return Rehearsal(
        REHEARSAL_PAIR,
        model=build_model(settings),
        classifier=build_classifier(settings),
        speaker=Voice(clock),
        clock=clock,
        timing=REHEARSAL,
        next_pair=NEXT_PAIR,
        first_up=LEFT,
        voice="fake/voice",
        stt="deepgram/nova-3",
        privacy=PrivacyPolicy.from_settings(settings),
        meaning_live=True,
    )


async def test_a_hosted_classifier_keeps_the_safety_window_but_not_this_date(transports):
    """The safety check still reads each utterance with both daters' 15 seconds before it,
    as frozen (decision 0005); the meaning check on the same model gets no recent turns;
    the local line model gets them for the reply."""
    settings = Settings.from_env({**BASE, "NOVA_CLASSIFIER_LLM": HAIKU, **CLAUDE})
    clock = Clock()
    r = rehearsal_for(settings, clock)
    await a_date(r, clock, LEFT_TURN, RIGHT_TURN, ASKED.detail or "")

    safety = sent(transports, CHECK_SYSTEM)
    assert {to for to, _ in safety} == {"anthropic"}
    assert any(all(w in body for w in ON_THIS_DATE) for _, body in safety), "the window"
    meaning = sent(transports, MEANING_SYSTEM)
    assert meaning and {to for to, _ in meaning} == {"anthropic"}
    assert not any(w in body for _, body in meaning for w in ON_THIS_DATE)
    replies = [body for to, body in sent(transports, prompts.SYSTEM_PROMPT) if "this_date" in body]
    assert replies and all(w in replies[-1] for w in ON_THIS_DATE)
    assert {to for to, _ in sent(transports, prompts.SYSTEM_PROMPT)} == {"http://localhost:11434"}
    assert "they stay out of live meaning checks (Anthropic)" in render_report(r)


CLASSIFIERS = {
    "local Ollama for both": {},
    "local lines, hosted classifier": {"NOVA_CLASSIFIER_LLM": HAIKU, **CLAUDE},
    "hosted lines, local classifier": {
        "NOVA_LLM": HAIKU,
        "NOVA_CLASSIFIER_LLM": "ollama/gemma4:e4b",
        **CLAUDE,
    },
    "hosted for both": {"NOVA_LLM": HAIKU, **CLAUDE},
}


async def test_the_safety_context_goes_where_the_inventory_says_and_is_the_same_anywhere(
    transports,
):
    """Conversation-derived safety context (decision 0010): every safety request goes to
    the recipient nova/data_flows.py names for these settings, and what's sent is the same
    whichever classifier gets it, both daters' words from before each utterance included.
    Switching classifiers moves the safety context; it never changes it."""
    sent_by: dict[str, list[str]] = {}
    for config, env in CLASSIFIERS.items():
        transports.clear()
        settings = Settings.from_env({**BASE, **env})
        clock = Clock()
        await a_date(rehearsal_for(settings, clock), clock, LEFT_TURN, RIGHT_TURN, "Okay then.")
        safety = sent(transports, CHECK_SYSTEM)
        policy = PrivacyPolicy()
        to = {
            policy.recipient_of("anthropic", None).name
            if where == "anthropic"
            else policy.recipient_of("ollama", where).name
            for where, _ in safety
        }
        inventory = data_flows.received(settings)
        assert to == {who for who, data in inventory.items() if Data.SAFETY_CONTEXT in data}
        assert any(all(w in body for w in ON_THIS_DATE) for _, body in safety), config
        sent_by[config] = sorted(body for _, body in safety)
    first, *rest = sent_by.values()
    assert all(bodies == first for bodies in rest), "the same requests, wherever they go"


async def test_a_request_the_policy_blocks_is_a_failed_request_never_a_sent_one(
    transports, monkeypatch
):
    """Were a prompt ever written with this date for a model that may not have it (here,
    the writer is told anything goes), the boundary still stops it: a fallback line, and
    nothing reaches the provider."""
    monkeypatch.setattr(PrivacyPolicy, "allows", lambda self, model, data: True)
    settings = Settings.from_env({**BASE, "NOVA_LLM": HAIKU, **CLAUDE})
    writer = LineWriter(
        build_model(settings),
        REHEARSAL_PAIR,
        next_pair=NEXT_PAIR,
        context=this_date(),
        privacy=PrivacyPolicy.from_settings(settings),
        meaning=MeaningCheck(build_classifier(settings), timeout=1.0),
        meaning_live=True,
    )
    line = await writer.line_for(ASKED)
    assert line.source is Source.FALLBACK
    assert sent(transports, prompts.SYSTEM_PROMPT, MEANING_SYSTEM) == []


# --- Nothing anyone said reaches a log, a report or an event ----------------------------------

CARD_WORD, ROOM_WORD, CHAT_WORD = "Larkspurren", "Thimbleford", "Wrenhollow"
ERROR_WORD = "Corvidale"  # in every error a failing model raises
MARKED = Pair(
    left=BlindCard(
        first_name="Ottoline",
        age=29,
        pronouns="she/her",
        prompts=(
            Prompt("Ask me about", f"my {CARD_WORD} teapot."),
            Prompt("My perfect Sunday", "starts with a long walk to a bakery."),
        ),
    ),
    right=BlindCard(
        first_name="Bramwell",
        age=31,
        pronouns="he/him",
        prompts=(
            Prompt("I claim I can", "parallel park anything."),
            Prompt("The way to win me over", "is a good recommendation for a book."),
        ),
    ),
)
MARKERS = (*ON_THIS_DATE, CARD_WORD, ROOM_WORD, CHAT_WORD, ERROR_WORD, "Ottoline", "Bramwell")


class Failing:
    """A model at a provider that fails in the worst way: every error carries the whole
    request, or it never answers."""

    name = "fake/failing"
    endpoint = "https://models.example.com"

    def __init__(self, how: str) -> None:
        self.how = how
        self.sent: list[str] = []  # every request it was asked, to show the words got here

    async def write(self, *, system: str, prompt: str) -> str:
        return await self._answer(system, prompt)

    async def label(self, *, system: str, prompt: str, schema: object = None) -> str:
        return await self._answer(system, prompt)

    async def _answer(self, system: str, prompt: str) -> str:
        self.sent.append(prompt)
        carried = f"{ERROR_WORD}: {system} {prompt}"
        match self.how:
            case "raises":
                raise RuntimeError(carried)
            case "refuses":
                raise ModelRefused(carried)
            case "connection":  # may pass: the warm-up tries again
                request = httpx.Request("POST", self.endpoint)
                raise openai.APIConnectionError(message=carried, request=request)
            case "hangs":
                await asyncio.sleep(30)
        return answer(system).replace("Maya", "Ottoline").replace("Theo", "Bramwell")


@pytest.mark.parametrize("how", ["answers", "raises", "refuses", "connection", "hangs"])
async def test_no_one_s_words_reach_a_log_a_report_or_an_event(how, caplog, capsys):
    caplog.set_level(logging.DEBUG)  # every logger, every level
    clock = Clock()
    model = Failing(how)
    r = Rehearsal(
        MARKED,
        model=model,
        speaker=Voice(clock),
        clock=clock,
        timing=REHEARSAL,
        next_pair=NEXT_PAIR,
        first_up=LEFT,
        voice="fake/voice",
        stt="deepgram/nova-3",
        live_timeout=0.05,
        prep_timeout=0.05,
        prep_budget=0.3,
        distress_timeout=0.05,
        meaning_timeout=0.05,
        meaning_live=True,
    )
    await r.warm_up(deadline=0.3)
    r.command(f"ask Ottoline, would you ever visit {ROOM_WORD}?")
    r.command(f"{CHAT_WORD} is where I grew up")  # not a command
    await a_date(r, clock, LEFT_TURN, RIGHT_TURN, f"Nova, have you heard of {CARD_WORD} teapots?")
    r.command("mute")
    r.command("unmute")
    while clock.now < 200.0 and not r.finished:  # the room's turn, and the rest of the date
        clock.now += 2.0
        r.tick()
        await r.speak_queued()
    report = render_report(r)
    gc.collect()  # any "exception never retrieved" is logged now

    out = capsys.readouterr()
    records = "".join(repr(vars(record)) for record in caplog.records)
    for where, text in [
        ("log", caplog.text),
        ("log records", records),
        ("report", report),
        ("output", out.out + out.err),
    ]:
        leaked = [m for m in MARKERS if m.lower() in text.lower()]
        assert not leaked, f"{leaked} in the {where}"
    assert any(record.name == "nova" for record in caplog.records), "events were logged"
    asked = " ".join(model.sent)
    reached = {*ON_THIS_DATE, CARD_WORD, "Ottoline", "Bramwell"}
    if how == "answers":
        reached.add(ROOM_WORD)  # the audience question, once the room's turn came
    missing = sorted(word for word in reached if word not in asked)
    assert not missing, f"never reached a model: {missing}"


async def test_a_retried_failure_logs_nothing_it_carried(caplog):
    caplog.set_level(logging.DEBUG)
    model = PrivacyPolicy().guard(Failing("connection"))
    warmed = await warm_up(model, deadline=2.0, backoff=(0.01,), attempts=3)
    assert (warmed.attempts, warmed.error) == (3, WarmUpError.ERROR)
    assert ERROR_WORD not in caplog.text + "".join(repr(vars(r)) for r in caplog.records)


# --- Logs that would write prompts ---------------------------------------------------------------


@pytest.fixture
def levels():
    """Each logger's level put back after the test."""
    names = ["", "anthropic", "openai", *REQUEST_LOGGERS]
    saved = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def settings(**env: str) -> Settings:
    return Settings.from_env({**BASE, **env})


def test_the_agent_starts_with_the_sdk_loggers_as_livekit_sets_them(levels):
    """LiveKit's dev mode: the root logger at DEBUG, the SDKs' quietened to WARN."""
    logging.getLogger().setLevel(logging.DEBUG)
    for name in ("anthropic", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    check_for_real_people(settings())


@pytest.mark.parametrize("sdk", ["anthropic", "openai"])
def test_the_agent_refuses_an_sdk_logger_that_writes_prompts(levels, sdk):
    logging.getLogger(sdk).setLevel(logging.DEBUG)  # what ANTHROPIC_LOG=debug does, say
    with pytest.raises(ConfigError, match=rf"{sdk}\._base_client would log every prompt"):
        check_for_real_people(settings())


def test_an_sdk_logger_left_unset_under_a_debug_root_is_refused_too(levels):
    """Had LiveKit not quietened them, they'd inherit DEBUG: caught all the same."""
    logging.getLogger().setLevel(logging.DEBUG)
    for name in ("anthropic", "openai"):
        logging.getLogger(name).setLevel(logging.NOTSET)
    with pytest.raises(ConfigError, match="would log every prompt"):
        check_for_real_people(settings())


def test_the_agent_refuses_the_diagnostics_meant_for_synthetic_runs():
    allowed = settings(ANTHROPIC_LOG="debug", NOVA_SYNTHETIC_DIAGNOSTICS="true")
    with pytest.raises(ConfigError, match="never the agent: it hears real people"):
        check_for_real_people(allowed)


# --- Where each policy is used -------------------------------------------------------------------

NOVA = Path(__file__).parents[1] / "nova"


def test_only_runs_on_made_up_daters_use_the_synthetic_policy():
    users = {p.name for p in NOVA.glob("*.py") if "for_synthetic_data()" in p.read_text("utf-8")}
    assert users == {"evaluation.py", "benchmark.py"}


def test_the_agent_uses_the_policy_its_settings_give_and_checks_its_logs():
    agent = (NOVA / "agent.py").read_text(encoding="utf-8")
    assert "privacy=PrivacyPolicy.from_settings(settings)" in agent
    assert agent.count("check_for_real_people(settings)") == 2  # at start, and in each job


def test_a_guarded_model_is_still_a_line_model_with_its_name():
    model = Recorded("http://localhost:11434")
    guarded = PrivacyPolicy().guard(model)
    assert isinstance(guarded, Guarded) and guarded.name == model.name
    assert PrivacyPolicy().guard(guarded) is guarded, "never wrapped twice by one policy"
