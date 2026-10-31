"""The startup rehearsal in real LiveKit rooms (decision 0011): Nova's first date of a session
from cold and from warm, and the doors-hold when Ollama is down or a model is missing. Each
trial (nova/startup_matrix.py, PLAN) is a new room and session: Nova's side opens the date
through a Night, as the agent does, with the real models, warms up, prepares, holds if it
must, and once the date opens says its first line; the harness plays the moderator. Docker
only, since Smart App Control blocks LiveKit natively, and it costs LiveKit credit (a room
for a minute or two a trial, one line of Nova's voice; nobody speaks). From the repository
root:

    docker compose -f services/nova/compose.yaml run --rm --build nova
        uv run python -m nova.startup_live

Cold trials unload the model from Ollama first. The outage trial points Nova at the
harness's own proxy to Ollama, on this machine's loopback, which doesn't listen for its
first OUTAGE seconds. The report (numbers and codes only) lands in
services/nova/rehearsals/startup-*.txt, with the raw numbers beside it as JSON.
"""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from livekit import api, rtc
from livekit.agents import Agent, AgentServer, JobContext, cli, room_io

from nova.agent import REPORTS, START_OPTIONS, SessionSpeaker, new_session, redact_logs
from nova.config import LlmChoice, Settings, load_settings
from nova.cues import ModeratorAlert
from nova.events import Event, EventLog
from nova.fixtures import NEXT_PAIR, REHEARSAL_PAIR
from nova.models import build_classifier, build_model
from nova.privacy import PrivacyPolicy
from nova.rehearsal import Rehearsal
from nova.safety_report import ollama_unload
from nova.show import Night
from nova.startup_matrix import AGENT_NAME, MISSING_MODEL, OUTAGE, PLAN, TOPIC, Kind, Trial, render
from nova.timing import REHEARSAL

WORKER_START = 10.0
DATER = "startup-dater"
PROXY_PORT = 11500  # the outage trial's proxy to Ollama, on this machine's loopback
TRIAL_TIMEOUT = 300.0  # the most a trial may take: 60 s bound, 120 s of tries, and room


class Reported(EventLog):
    """Nova's log, and each moderator alert sent on to the harness as its code."""

    def __init__(self, send: Any) -> None:
        super().__init__()
        self._send = send

    def emit(self, event: Event, **fields: object) -> None:
        super().emit(event, **fields)
        if event is Event.ALERT and isinstance(fields.get("alert"), ModeratorAlert):
            self._send({"type": "alert", "alert": str(fields["alert"])})


async def _send(room: rtc.Room, message: dict[str, Any]) -> None:
    await room.local_participant.publish_data(
        json.dumps(message).encode(), reliable=True, topic=TOPIC
    )


# --- Nova's side ---------------------------------------------------------------------------

server = AgentServer()


def _settings_for(kind: Kind, settings: Settings) -> Settings:
    if kind is Kind.OUTAGE:
        return replace(settings, ollama_base_url=f"http://127.0.0.1:{PROXY_PORT}")
    if kind is Kind.MISSING:
        return replace(settings, llm=LlmChoice("ollama", MISSING_MODEL), classifier=None)
    return settings


@server.rtc_session(agent_name=AGENT_NAME)
async def nova(ctx: JobContext) -> None:
    redact_logs()
    kind = Kind(json.loads(ctx.job.metadata or "{}").get("kind", "warm"))
    settings = _settings_for(kind, load_settings())
    session = new_session(settings)
    commands: asyncio.Queue[str] = asyncio.Queue()
    sending: set[asyncio.Task[None]] = set()

    def send(message: dict[str, Any]) -> None:
        task = asyncio.ensure_future(_send(ctx.room, message))
        sending.add(task)
        task.add_done_callback(sending.discard)

    def on_data(packet: rtc.DataPacket) -> None:
        if packet.topic == TOPIC and packet.participant and packet.participant.identity == DATER:
            commands.put_nowait(json.loads(packet.data)["command"])

    ctx.room.on("data_received", on_data)
    night = Night()
    with night.date(
        REHEARSAL_PAIR,
        model=build_model(settings),
        classifier=build_classifier(settings),
        speaker=SessionSpeaker(session),
        voice=settings.voice,
        stt=settings.stt,
        timing=REHEARSAL,
        next_pair=NEXT_PAIR,
        prep_timeout=settings.prep_timeout,
        prep_budget=settings.prep_budget,
        prep_parallel=settings.prep_parallel,
        warm_up=settings.warm_up,
        distress_timeout=settings.distress_timeout,
        privacy=PrivacyPolicy.from_settings(settings),
        meaning_check=settings.meaning_check,
        ready_retry=settings.ready_retry,
        events=Reported(send),
    ) as r:
        await _startup(ctx, session, r, commands, send)
    await asyncio.gather(*sending, return_exceptions=True)
    ctx.shutdown("startup trial finished")


async def _startup(
    ctx: JobContext,
    session: Any,
    r: Rehearsal,
    commands: asyncio.Queue[str],
    send: Any,
) -> None:
    """As the agent does: get ready from the session's start, hold if not, open, and say the
    first line; the harness's moderator commands go to the rehearsal's chat."""

    async def get_ready() -> None:
        await r.warm_up()
        await r.prepare()

    async def moderator() -> None:
        while True:
            r.command(await commands.get())

    preparing = asyncio.create_task(get_ready())
    commanding = asyncio.create_task(moderator())
    try:
        await session.start(
            agent=Agent(instructions="Nova's lines come from the startup rehearsal."),
            room=ctx.room,
            room_options=room_io.RoomOptions(text_output=False, participant_identity=DATER),
            **START_OPTIONS,
        )
        await preparing
        opens = await r.hold_until_ready()
        first_line = ""
        if opens:
            r.start()
            await r.speak_queued()  # the doors intro
            first_line = r.spoken[0].source.value if r.spoken else ""
        warmed = dict(r.warmed).get("model")
        fallbacks, pieces = r._writer.fallbacks
        send(
            {
                "type": "report",
                "opened": r.opened.value if r.opened else "",
                "startup_ms": r.startup_ms,
                "warm_up_ms": r.warm_up_ms,
                "warm_up_error": None
                if warmed is None or warmed.error is None
                else str(warmed.error),
                "prep_ms": r.prep_ms,
                "held_ms": None if r.held_for is None else round(r.held_for * 1000),
                "retries": r.hold_retries,
                "reprepared": r.reprepared,
                "fallbacks": fallbacks,
                "set_pieces": pieces,
                "first_line": first_line,
            }
        )
    finally:
        preparing.cancel()
        commanding.cancel()


# --- The harness ------------------------------------------------------------------------------


class Proxy:
    """A plain TCP relay to Ollama on this machine's loopback, which listens only once
    opened: until then, Nova's requests are refused, as by an Ollama not up yet."""

    def __init__(self, target: str) -> None:
        parts = urlsplit(target)
        self._host, self._port = parts.hostname or "localhost", parts.port or 11434
        self._server: asyncio.Server | None = None

    async def open(self) -> None:
        self._server = await asyncio.start_server(self._relay, "127.0.0.1", PROXY_PORT)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 5.0)
            self._server = None

    async def _relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(self._host, self._port)
        except OSError:
            writer.close()
            return

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except ConnectionError, OSError:
                pass
            finally:
                dst.close()

        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


async def _trial(settings: Settings, index: int, kind: Kind, proxy: Proxy) -> Trial:
    trial = Trial(index, kind)
    if kind is Kind.COLD:  # the next warm-up waits for a load, the classifier's too
        for choice in {settings.llm, settings.classifier or settings.llm}:
            if choice.provider == "ollama":
                await ollama_unload(choice.model, settings.ollama_base_url)
    name = f"nova-startup-{datetime.now():%Y%m%d-%H%M%S}-{index}"
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(DATER)
        .with_name("Startup rehearsal moderator")
        .with_grants(api.VideoGrants(room_join=True, room=name))
        .with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=AGENT_NAME, metadata=json.dumps({"kind": kind.value})
                    )
                ]
            )
        )
        .to_jwt()
    )
    room = rtc.Room()
    messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    room.on(
        "data_received",
        lambda packet: (
            messages.put_nowait(json.loads(packet.data)) if packet.topic == TOPIC else None
        ),
    )
    opening: asyncio.Task[None] | None = None
    if kind is Kind.OUTAGE:
        opening = asyncio.ensure_future(_open_later(proxy))
    joined = time.monotonic()
    await room.connect(settings.livekit_url, token)
    try:
        async with asyncio.timeout(TRIAL_TIMEOUT):
            while (message := await messages.get())["type"] != "report":
                trial.alerts.append(message["alert"])
                if message["alert"] == "nova_still_not_ready":  # the moderator ends it
                    await room.local_participant.publish_data(
                        json.dumps({"command": "end"}).encode(), reliable=True, topic=TOPIC
                    )
        trial.joined_to_report_ms = round((time.monotonic() - joined) * 1000)
        for key in (
            "opened", "startup_ms", "warm_up_ms", "warm_up_error", "prep_ms", "held_ms",
            "retries", "reprepared", "fallbacks", "set_pieces", "first_line",
        ):  # fmt: skip
            setattr(trial, key, message[key])
    finally:
        await room.disconnect()
        if opening is not None:
            opening.cancel()
        await proxy.close()
    return trial


async def _open_later(proxy: Proxy) -> None:
    await asyncio.sleep(OUTAGE)
    await proxy.open()


async def harness(settings: Settings) -> int:
    started = datetime.now()
    proxy = Proxy(settings.ollama_base_url)
    trials: list[Trial] = []
    for index, kind in enumerate(PLAN):
        trial = await _trial(settings, index, kind, proxy)
        trials.append(trial)
        print(f"trial {index} {kind}: opened {trial.opened or 'never'}")
        await asyncio.sleep(3.0)  # the worker lets go of the last job
    report = render(trials, started=f"{started:%Y-%m-%d %H:%M}", model=settings.llm.model)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"startup-{started:%Y-%m-%d-%H%M%S}.txt"
    path.write_text(report + "\n", encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps([asdict(t) for t in trials]), encoding="utf-8")
    print(report)
    print(f"\nreport saved to {path}")
    from nova.startup_matrix import outcome

    return 0 if all(outcome(t).passed for t in trials) else 1


def main() -> None:
    settings = load_settings()
    os.environ.setdefault("LIVEKIT_URL", settings.livekit_url)
    os.environ.setdefault("LIVEKIT_API_KEY", settings.livekit_api_key)
    os.environ.setdefault("LIVEKIT_API_SECRET", settings.livekit_api_secret)
    if sys.argv[1:2] == ["agent"]:
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        cli.run_app(server)
        return
    worker = subprocess.Popen([sys.executable, "-m", "nova.startup_live", "agent", "start"])
    try:
        time.sleep(WORKER_START)
        code = asyncio.run(harness(settings))
    finally:
        worker.terminate()
        try:
            worker.wait(20)
        except subprocess.TimeoutExpired:
            worker.kill()
    sys.exit(code)


if __name__ == "__main__":
    main()
