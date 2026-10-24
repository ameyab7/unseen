"""The mute rehearsal in a real LiveKit room (decision 0005, launch requirement 2): Nova as
a rehearsal runs it, with the agent's own moderator control (nova/agent.py
connect_moderators), and a dater, a moderator and an impostor follow the trials in
nova/mute_matrix.py. Docker only, since Smart App Control blocks LiveKit natively, and it
costs LiveKit credit (a room for about ten minutes, speech recognition, Nova's voice). From
the repository root:

    docker compose -f services/nova/compose.yaml run --rm --build nova
        uv run python -m nova.mute_live

The report (numbers and codes only) lands in services/nova/rehearsals/mute-*.txt, with the
raw numbers beside it as JSON. The dater's clips are the yield rehearsal's
(rehearsals/yield-audio/). The tests never run this: they cover nova/mute_matrix.py.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any

from livekit import api, rtc
from livekit.agents import Agent, AgentServer, JobContext, cli, room_io

from nova.agent import (
    REPORTS,
    START_OPTIONS,
    SessionSpeaker,
    connect_moderators,
    new_session,
    redact_logs,
    time_played_words,
)
from nova.config import Settings, load_settings
from nova.cues import Moment
from nova.fixtures import REHEARSAL_PAIR
from nova.models import build_classifier, build_model
from nova.moderator import MODERATOR_ROLE, ROLE_ATTRIBUTE, ModeratorControl
from nova.moderator import TOPIC as CONTROL
from nova.mute_matrix import AGENT_NAME, AT, MATRIX, TOPIC, Proof, Scenario, Trial, render
from nova.rehearsal import Rehearsal
from nova.timing import REHEARSAL
from nova.yield_live import Ear, Mic, Recorded, Timed, clips
from nova.yield_matrix import FRAME_SECONDS, LINE_SECONDS, LOUD, SAMPLE_RATE, TEST_LINE

WORKER_START = 10.0
DATER = "mute-dater"
MODERATOR = "mute-moderator"


async def _send(room: rtc.Room, message: dict[str, Any], *, topic: str = TOPIC) -> None:
    await room.local_participant.publish_data(
        json.dumps(message).encode(), reliable=True, topic=topic
    )


# --- Nova's side ---------------------------------------------------------------------------

server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def nova(ctx: JobContext) -> None:
    redact_logs()
    settings = load_settings()
    session = new_session(settings)
    speaker = Timed(SessionSpeaker(session, clock=time.time))
    events = Recorded()
    rehearsal = Rehearsal(
        REHEARSAL_PAIR,
        model=build_model(settings),
        classifier=build_classifier(settings),
        speaker=speaker,
        voice=settings.voice,
        stt=settings.stt,
        clock=time.time,
        timing=REHEARSAL,
        warm_up=settings.warm_up,
        distress_timeout=settings.distress_timeout,
        utterance_grace=settings.utterance_grace,
        utterance_max_wait=settings.utterance_max_wait,
        events=events,
    )
    connect_moderators(ctx.room, ModeratorControl(rehearsal, events=events))  # the agent's own

    def on_user_state(event: Any) -> None:
        if event.new_state == "speaking":
            rehearsal.speech_started()
        elif event.old_state == "speaking":
            rehearsal.speech_ended()

    def on_transcript(event: Any) -> None:
        if event.is_final:
            rehearsal.transcript(event.transcript)

    requests: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def on_data(packet: rtc.DataPacket) -> None:
        who = packet.participant.identity if packet.participant else None
        if packet.topic == TOPIC and who == DATER:
            requests.put_nowait(json.loads(packet.data))

    session.on("user_state_changed", on_user_state)
    session.on("user_input_transcribed", on_transcript)
    ctx.room.on("data_received", on_data)
    listening = asyncio.create_task(rehearsal.listen())
    speaking: asyncio.Task[None] | None = None
    try:
        await rehearsal.warm_up()
        await session.start(
            agent=Agent(instructions="Nova's lines come from the mute rehearsal; no replies."),
            room=ctx.room,
            room_options=room_io.RoomOptions(text_output=False, participant_identity=DATER),
            **START_OPTIONS,
        )
        speaker.voice.timed = time_played_words(session)
        await ctx.wait_for_participant(identity=DATER)
        speaking = asyncio.create_task(rehearsal.speak_lines())
        await _send(ctx.room, {"type": "ready"})
        marks = (0, 0, 0, 0, 0)
        plays: list[list[float | bool]] = []
        while (request := await requests.get())["type"] != "bye":
            match request["type"]:
                case "begin":
                    plays.clear()
                    marks = (
                        rehearsal.unsaid,
                        rehearsal.mutes,
                        len(rehearsal.yields),
                        len(events.alerts),
                        events.voice_errors,
                    )
                case "play":
                    plays.append([time.time(), rehearsal.muted])  # when, and muted then
                    rehearsal.say(Moment.SILENCE, TEST_LINE)
                case "report":
                    await _send(
                        ctx.room,
                        {
                            "type": "report",
                            "trial": request["trial"],
                            "unsaid": rehearsal.unsaid - marks[0],
                            "mutes_delta": rehearsal.mutes - marks[1],
                            "yields_then": [y.then.value for y in rehearsal.yields[marks[2] :]],
                            "alerts": [a[1] for a in events.alerts[marks[3] :]],
                            "voice_errors": events.voice_errors - marks[4],
                            "muted": rehearsal.muted,
                            "plays": plays,
                        },
                    )
    finally:
        listening.cancel()
        if speaking is not None:
            speaking.cancel()
    ctx.shutdown("mute rehearsal finished")


# --- The other participants -----------------------------------------------------------------


class Moderator:
    """The console, simulated: a participant whose token carries role=moderator. Sends the
    commands, and keeps every NovaState it receives with when it came."""

    def __init__(self, settings: Settings, room_name: str) -> None:
        self._settings, self._room_name = settings, room_name
        self.room: rtc.Room | None = None
        self.states: list[tuple[float, dict[str, Any]]] = []
        self.session = ""

    async def join(self) -> None:
        token = (
            api.AccessToken(self._settings.livekit_api_key, self._settings.livekit_api_secret)
            .with_identity(MODERATOR)
            .with_name("Mute rehearsal moderator")
            .with_attributes({ROLE_ATTRIBUTE: MODERATOR_ROLE})
            .with_grants(api.VideoGrants(room_join=True, room=self._room_name))
            .to_jwt()
        )
        self.room = rtc.Room()
        self.room.on("data_received", self._on_data)
        await self.room.connect(self._settings.livekit_url, token)

    async def leave(self) -> None:
        if self.room is not None:
            await self.room.disconnect()
            self.room = None

    def _agent(self) -> str | None:
        assert self.room is not None
        return next(
            (
                p.identity
                for p in self.room.remote_participants.values()
                if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
            ),
            None,
        )

    def _on_data(self, packet: rtc.DataPacket) -> None:
        # As a console must: a NovaState counts only from Nova's agent. Anyone can publish
        # on the topic (the impostor's command reaches everyone in the room).
        who = packet.participant
        from_agent = who is not None and who.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
        if packet.topic != CONTROL or not from_agent:
            return
        state = json.loads(packet.data)
        if state.get("type") != "nova_state":
            return
        self.states.append((time.time(), state))
        self.session = state.get("session", self.session)

    async def command(
        self,
        kind: str,
        *,
        seq: int | None = None,
        session: str | None = None,
        payload: bytes | None = None,
    ) -> tuple[float, int, bytes]:
        assert self.room is not None
        seq = seq if seq is not None else int(time.time() * 1000)
        body = (
            payload
            or json.dumps({"type": kind, "seq": seq, "session": session or self.session}).encode()
        )
        agent = self._agent()
        sent = time.time()
        # As a console must: to Nova's agent only, so the daters never receive it.
        await self.room.local_participant.publish_data(
            body, reliable=True, topic=CONTROL, destination_identities=[agent] if agent else []
        )
        return sent, seq, body

    async def ack_for(self, seq: int, *, timeout: float = 5.0) -> dict | None:
        """The NovaState answering a command, as a console waits for it before showing the
        state: messages from different participants aren't ordered against each other, so
        nothing else is asked of Nova until the command is known to have landed."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for _, state in self.states:
                if state.get("seq") == seq and state.get("result") != "state":
                    return state
            await asyncio.sleep(0.01)
        return None

    async def state_after(self, at: float, *, timeout: float = 10.0) -> tuple[float, dict] | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for when, state in self.states:
                if when > at:
                    return when, state
            await asyncio.sleep(0.02)
        return None


def _from_agent(packet: rtc.DataPacket) -> bool:
    who = packet.participant
    return who is not None and who.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT


async def _message(queue: asyncio.Queue[dict[str, Any]], kind: str, *, timeout: float) -> dict:
    return await asyncio.wait_for(_until(queue, kind), timeout)


async def _until(queue: asyncio.Queue[dict[str, Any]], kind: str) -> dict:
    while (message := await queue.get()).get("type") != kind:
        pass
    return message


async def _trial(
    k: int,
    scenario: Scenario,
    dater: rtc.Room,
    ear: Ear,
    mic: Mic,
    mod: Moderator,
    reports: asyncio.Queue,
    audio: dict,
    impostor: list[int],
) -> Trial:
    trial = Trial(k, scenario)
    await ear.quiet(1.5)
    first, impostor_from = len(ear.frames), impostor[0]
    await _send(dater, {"type": "begin", "trial": k})
    m = trial.marks

    async def play() -> float | None:
        m.setdefault("play", time.time())
        asked = time.time()
        trial.marks[f"play_{len([k for k in m if k.startswith('play_')])}"] = asked
        await _send(dater, {"type": "play"})
        return await ear.onset(after=asked, timeout=8.0)

    async def command(kind: str, sender: str = "moderator", **options: Any) -> None:
        if sender == "moderator":
            sent, seq, _ = await mod.command(kind, **options)
            trial.sent.append((sent, sender, kind, seq))
            await mod.ack_for(seq)  # as a console does, before anything else happens
            return
        else:  # the dater, without the role, as an impostor would
            seq = int(time.time() * 1000)
            body = {"type": kind, "seq": seq, "session": mod.session}
            sent = time.time()
            await _send(dater, body, topic=CONTROL)
        trial.sent.append((sent, sender, kind, seq))

    async def until(t: float) -> None:
        await asyncio.sleep(max(0.0, t - time.time()))

    line = LINE_SECONDS
    match scenario:
        case Scenario.MID_LINE:
            started = await play()
            if started is not None:
                await until(started + AT * line)
                await command("mute")
                await asyncio.sleep(4.0)
            m["unmute"] = time.time()
            await command("unmute")
            await play()
            await ear.quiet(1.5)
        case Scenario.IDLE:
            await command("mute")
            await asyncio.sleep(0.5)
            await play()
            await asyncio.sleep(3.0)
            m["unmute"] = time.time()
            await command("unmute")
            await play()
            await ear.quiet(1.5)
        case Scenario.IMPOSTOR:
            started = await play()
            if started is not None:
                await until(started + AT * line)
                m["impostor"] = time.time()
                await command("mute", sender="impostor")
            await ear.quiet(1.5)
        case Scenario.STALE:
            started = await play()
            if started is not None:
                await until(started + AT * line)
                m["stale"] = time.time()
                await command("mute", seq=k + 1)  # older than anything this moderator sent
                await command("mute", session="an-earlier-session")
            await ear.quiet(1.5)
        case Scenario.DUPLICATE:
            started = await play()
            if started is not None:
                await until(started + AT * line)
                sent, seq, body = await mod.command("mute")
                trial.sent.append((sent, "moderator", "mute", seq))
                await asyncio.sleep(0.2)
                again, _, _ = await mod.command("mute", payload=body)  # the same message again
                trial.sent.append((again, "moderator", "mute", seq))
                await asyncio.sleep(3.0)
            m["unmute"] = time.time()
            await command("unmute")
            await ear.quiet(1.5)
        case Scenario.LOST:
            await command("mute")
            await asyncio.sleep(0.5)
            await mod.leave()  # the console's connection drops
            await play()
            await asyncio.sleep(3.0)
            back = time.time()
            await mod.join()  # and comes back
            state = await mod.state_after(back)
            trial.rejoined_state = (state[0], state[1]["muted"]) if state else None
            m["unmute"] = time.time()
            await command("unmute")
            await play()
            await ear.quiet(1.5)
        case Scenario.SAFETY:
            await command("mute")
            await asyncio.sleep(0.5)
            m["clip"] = time.time()
            said = await mic.say(audio["stop"], at=time.time())
            await asyncio.sleep(7.0)  # the utterance closes and the check runs
            trial.clip_onsets = [said.onset] if said.onset else []
            m["unmute"] = time.time()
            await command("unmute")
            await ear.quiet(1.0)
        case Scenario.AFTER_CUT:
            started = await play()
            if started is not None:
                said = await mic.say(audio["cut_in"], at=started + AT * line)
                await asyncio.sleep(0.9)  # the cut-in's lead-in, the voice detector and the hold
                await command("mute")
                while said.end is None:
                    await asyncio.sleep(0.02)
                trial.clip_onsets, trial.clip_ends = [said.onset or 0.0], [said.end]
                await asyncio.sleep(4.0)
            m["unmute"] = time.time()
            await command("unmute")
            await ear.quiet(1.0)
    await _send(dater, {"type": "report", "trial": k})
    trial.agent = await _message(reports, "report", timeout=20.0)
    await asyncio.sleep(0.5)  # an acknowledgement still on its way
    trial.nova = ear.frames[first:]
    # Each acknowledgement belongs to the command whose seq it answers, whenever it came.
    seqs = {x[3] for x in trial.sent if x[1] == "moderator"}
    trial.acks = [
        (when, s["result"], s["muted"], s["seq"])
        for when, s in mod.states
        if s.get("result") != "state" and s.get("seq") in seqs
    ]
    trial.impostor_acks = impostor[0] - impostor_from
    return trial


async def harness(settings: Settings) -> int:
    started = datetime.now()
    audio = await clips()
    name = f"nova-mute-{started:%Y%m%d-%H%M%S}"
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(DATER)
        .with_name("Mute rehearsal dater")
        .with_attributes({ROLE_ATTRIBUTE: "dater"})
        .with_grants(api.VideoGrants(room_join=True, room=name))
        .with_room_config(
            api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME)])
        )
        .to_jwt()
    )
    dater, ear, proof = rtc.Room(), Ear(), Proof()
    reports: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    impostor = [0]  # NovaStates the dater received: there must be none
    tasks: set[asyncio.Task[Any]] = set()

    def on_track(track: rtc.Track, publication: rtc.RemoteTrackPublication, who: Any) -> None:
        if (
            track.kind == rtc.TrackKind.KIND_AUDIO
            and who.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
        ):
            proof.agent_kind, proof.agent_track_sid = "agent", publication.sid
            tasks.add(asyncio.ensure_future(ear.listen(track)))

    def on_data(packet: rtc.DataPacket) -> None:
        if packet.topic == TOPIC:
            reports.put_nowait(json.loads(packet.data))
        elif packet.topic == CONTROL and _from_agent(packet):
            impostor[0] += 1  # Nova told the dater its state: it never may

    dater.on("track_subscribed", on_track)
    dater.on("data_received", on_data)
    await dater.connect(settings.livekit_url, token)
    mod = Moderator(settings, name)
    trials: list[Trial] = []
    try:
        proof.room_sid = await dater.sid
        source = rtc.AudioSource(SAMPLE_RATE, 1, queue_size_ms=50)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await dater.local_participant.publish_track(track, options)
        mic = Mic(source, ear)
        tasks.add(asyncio.ensure_future(mic.run()))
        await _message(reports, "ready", timeout=180.0)
        await mod.join()
        joined = await mod.state_after(0.0)
        if joined is None:
            raise RuntimeError("the moderator never heard Nova's state on joining")
        seen = dater.remote_participants.get(MODERATOR)
        proof.moderator_attributes = dict(seen.attributes) if seen else {}
        for plan in MATRIX:
            for _ in range(plan.trials):
                trial = await _trial(
                    len(trials), plan.scenario, dater, ear, mic, mod, reports, audio, impostor
                )
                trials.append(trial)
                print(f"trial {trial.index} {plan.scenario}: done")
        await _send(dater, {"type": "bye"})
    finally:
        for task in tasks:
            task.cancel()
        await mod.leave()
        await dater.disconnect()
    proof.frames = ear.count
    proof.sound_s = sum(level >= LOUD for _, level in ear.frames) * FRAME_SECONDS
    proof.states_received = len(mod.states)
    proof.voice_errors = sum(t.agent.get("voice_errors", 0) for t in trials)
    proof.trials = len(trials)
    report = render(proof, trials, started=f"{started:%Y-%m-%d %H:%M}")
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"mute-{started:%Y-%m-%d-%H%M%S}.txt"
    path.write_text(report + "\n", encoding="utf-8")
    raw = {"proof": asdict(proof), "trials": [asdict(t) for t in trials]}
    path.with_suffix(".json").write_text(json.dumps(raw), encoding="utf-8")
    print(report)
    print(f"\nreport saved to {path}")
    from nova.mute_matrix import outcome, problems

    return 1 if problems(proof) or not all(outcome(t).passed for t in trials) else 0


def main() -> None:
    settings = load_settings()
    os.environ.setdefault("LIVEKIT_URL", settings.livekit_url)
    os.environ.setdefault("LIVEKIT_API_KEY", settings.livekit_api_key)
    os.environ.setdefault("LIVEKIT_API_SECRET", settings.livekit_api_secret)
    if sys.argv[1:2] == ["agent"]:
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        cli.run_app(server)
        return
    worker = subprocess.Popen([sys.executable, "-m", "nova.mute_live", "agent", "start"])
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
