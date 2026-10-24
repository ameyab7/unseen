"""The yield rehearsal in a real LiveKit room (decision 0004): Nova as a rehearsal runs it,
and a dater who cuts in on a schedule (nova/yield_matrix.py, MATRIX). Docker only, since
Smart App Control blocks LiveKit natively, and it costs LiveKit credit (a room for about 25
minutes, speech recognition, Nova's voice, and the dater's clips once). From the repository
root, as one command:

    docker compose -f services/nova/compose.yaml run --rm --build nova
        uv run python -m nova.yield_live

It starts Nova's side as a LiveKit worker that joins only rooms it's dispatched to by name
(AGENT_NAME), never a Playground room, then joins a new room as the dater, whose token
dispatches it. Nova's side is a rehearsal's (AgentSession, SessionSpeaker, Rehearsal) with
the date not started: it plays the test line when the dater asks, and reports back numbers
only. The report lands in services/nova/rehearsals/, with the raw numbers beside it as JSON;
the dater's clips are kept in services/nova/rehearsals/yield-audio/ (never committed). The
tests never run this: they cover nova/yield_matrix.py.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import wave
from array import array
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from livekit import api, rtc
from livekit.agents import Agent, AgentServer, JobContext, cli, inference, room_io, utils

from nova.agent import (
    REPORTS,
    START_OPTIONS,
    SessionSpeaker,
    new_session,
    redact_logs,
    time_played_words,
)
from nova.config import Settings, load_settings
from nova.cues import Moment
from nova.events import Event, EventLog
from nova.fixtures import REHEARSAL_PAIR
from nova.models import build_classifier, build_model
from nova.rehearsal import Rehearsal
from nova.timing import REHEARSAL
from nova.yield_matrix import (
    AGENT_NAME,
    CLIPS,
    ECHO_DELAY,
    ECHO_GAIN,
    FRAME_SAMPLES,
    FRAME_SECONDS,
    LINE_SECONDS,
    LOUD,
    MATRIX,
    SAMPLE_RATE,
    TEST_LINE,
    TOPIC,
    Plan,
    Proof,
    Scenario,
    Trial,
    problems,
    render,
    rms,
    silent,
    sound_runs,
)

WORKER_START = 10.0  # seconds for Nova's worker to register before the dater joins
DATER = "yield-dater"
CLIPS_DIR = REPORTS / "yield-audio"
ECHO_FRAMES = round(ECHO_DELAY / FRAME_SECONDS)


async def send(room: rtc.Room, message: dict[str, Any]) -> None:
    await room.local_participant.publish_data(
        json.dumps(message).encode(), reliable=True, topic=TOPIC
    )


# --- Nova's side ---------------------------------------------------------------------------


class Timed:
    """Nova's voice, noting when each line was handed over, first played and finished, and
    when each stop was asked for: numbers for the report, never the words."""

    def __init__(self, voice: SessionSpeaker) -> None:
        self.voice = voice
        self.calls: list[list[float | None]] = []
        self.stops: list[float] = []

    @property
    def played(self) -> float | None:
        return self.voice.played

    @property
    def played_text(self) -> str | None:
        return self.voice.played_text

    async def speak(self, text: str) -> float | None:
        called = time.time()
        started = await self.voice.speak(text)
        self.calls.append([called, started, time.time(), len(text)])
        return started

    def stop(self) -> None:
        self.stops.append(time.time())
        self.voice.stop()


class Recorded(EventLog):
    """Nova's log as ever, and when each moderator alert came (its code only)."""

    def __init__(self) -> None:
        super().__init__()
        self.alerts: list[list[float | str]] = []
        self.voice_errors = 0

    def emit(self, event: Event, **fields: object) -> None:
        super().emit(event, **fields)
        if event is Event.ALERT:
            self.alerts.append([time.time(), str(fields.get("alert"))])
        if event is Event.VOICE_ERROR:
            self.voice_errors += 1


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
        clock=time.time,  # the dater's clock: both run on one machine
        timing=REHEARSAL,
        warm_up=settings.warm_up,
        distress_timeout=settings.distress_timeout,
        utterance_grace=settings.utterance_grace,
        utterance_max_wait=settings.utterance_max_wait,
        events=events,
    )
    heard: list[list[float | bool]] = []  # the voice detector: when, and started or stopped

    def on_user_state(event: Any) -> None:
        if event.new_state == "speaking":
            heard.append([time.time(), True])
            rehearsal.speech_started()
        elif event.old_state == "speaking":
            heard.append([time.time(), False])
            rehearsal.speech_ended()

    def on_transcript(event: Any) -> None:
        if event.is_final:
            rehearsal.transcript(event.transcript)

    requests: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def on_data(packet: rtc.DataPacket) -> None:
        if packet.topic == TOPIC:
            requests.put_nowait(json.loads(packet.data))

    session.on("user_state_changed", on_user_state)
    session.on("user_input_transcribed", on_transcript)
    ctx.room.on("data_received", on_data)
    listening = asyncio.create_task(rehearsal.listen())  # the distress check, as on a date
    speaking: asyncio.Task[None] | None = None
    try:
        await rehearsal.warm_up()  # the check's model, so its first checks don't time out
        await session.start(
            agent=Agent(instructions="Nova's lines come from the yield rehearsal; no replies."),
            room=ctx.room,
            room_options=room_io.RoomOptions(text_output=False),
            **START_OPTIONS,
        )
        speaker.voice.timed = time_played_words(session)
        participant = await ctx.wait_for_participant()
        speaking = asyncio.create_task(rehearsal.speak_lines())
        ready = {"type": "ready", "linked": participant.identity, "timed": speaker.voice.timed}
        await send(ctx.room, ready)
        while (request := await requests.get())["type"] != "bye":
            if request["type"] != "play":
                continue
            marks = (len(heard), len(rehearsal.yields), len(speaker.calls))
            stops, alerts, errors = len(speaker.stops), len(events.alerts), events.voice_errors
            rehearsal.say(Moment.SILENCE, TEST_LINE)
            await rehearsal.lines_done()
            await asyncio.sleep(request.get("settle", 1.0))  # a late detector event or alert
            done = {
                "type": "done",
                "trial": request["trial"],
                "vad": heard[marks[0] :],
                "yields": [asdict(y) for y in rehearsal.yields[marks[1] :]],
                "speaks": speaker.calls[marks[2] :],
                "stops": speaker.stops[stops:],
                "alerts": events.alerts[alerts:],
                "voice_errors": events.voice_errors - errors,
            }
            await send(ctx.room, done)
    finally:
        listening.cancel()
        if speaking is not None:
            speaking.cancel()
    ctx.shutdown("yield rehearsal finished")


# --- The dater's side ----------------------------------------------------------------------


@dataclass
class Said:
    """One clip in the dater's microphone: when it was meant to start, and when its first and
    last frames went in."""

    intended: float
    onset: float | None = None
    end: float | None = None


class Ear:
    """Nova's audio as the dater receives it over WebRTC: (time, rms) per 10 ms frame, and,
    for the echo trials, the frames to loop back and the echo canceller's far end."""

    def __init__(self) -> None:
        self.frames: list[tuple[float, float]] = []
        self.count = 0
        self.echoing = False
        self.far_end: Callable[[rtc.AudioFrame], None] | None = None
        self._echo: deque[array] = deque(maxlen=200)

    async def listen(self, track: rtc.Track) -> None:
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1, frame_size_ms=10)
        async for event in stream:
            frame = event.frame
            samples = array("h", frame.data)
            self.frames.append((time.time(), rms(samples)))
            self.count += 1
            if self.echoing:
                self._echo.append(samples)
            if self.far_end is not None and frame.samples_per_channel == FRAME_SAMPLES:
                self.far_end(frame)

    def echo(self) -> array | None:
        """The frame Nova's audio reached the microphone with, ECHO_DELAY after it played."""
        if len(self._echo) > ECHO_FRAMES:
            return self._echo.popleft()
        return None

    def clear_echo(self) -> None:
        self._echo.clear()

    def last_sound(self) -> float | None:
        return next((t for t, level in reversed(self.frames) if level >= LOUD), None)

    async def onset(self, *, after: float, timeout: float) -> float | None:
        """When Nova's audio next starts after `after`, as received."""
        deadline, seen = time.time() + timeout, 0
        while time.time() < deadline:
            for t, level in self.frames[seen:]:
                if t > after and level >= LOUD:
                    return t
            seen = len(self.frames)
            await asyncio.sleep(0.01)
        return None

    async def quiet(self, seconds: float, *, timeout: float = 60.0) -> None:
        """Until Nova has been silent for `seconds`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            last = self.last_sound()
            if last is None or time.time() - last >= seconds:
                return
            await asyncio.sleep(0.05)


class Mic:
    """The dater's microphone: clips, Nova's echo if a trial loops it back, and WebRTC's echo
    canceller if a trial runs it, as a phone does; 10 ms at a time, in real time."""

    def __init__(self, source: rtc.AudioSource, ear: Ear) -> None:
        self._source, self._ear = source, ear
        self._playing: list[list[Any]] = []  # [samples, position, gain, Said]
        self.echo = self.cancel = False
        self._apm = rtc.AudioProcessingModule(echo_cancellation=True, high_pass_filter=True)

    def far_end(self, frame: rtc.AudioFrame) -> None:
        self._apm.process_reverse_stream(frame)

    async def say(
        self, samples: array, *, at: float, gain: float = 1.0, delay: float = 0.0
    ) -> Said:
        said = Said(intended=at)
        await asyncio.sleep(max(0.0, at + delay - time.time()))
        self._playing.append([samples, 0, gain, said])
        return said

    def clear(self) -> None:
        for clip in self._playing:
            if clip[3].end is None:
                clip[3].end = time.time()
        self._playing.clear()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        while True:
            mix = [0] * FRAME_SAMPLES
            began: list[Said] = []
            ended: list[Said] = []
            for clip in list(self._playing):
                samples, position, gain, said = clip
                for i, sample in enumerate(samples[position : position + FRAME_SAMPLES]):
                    mix[i] += int(sample * gain)
                if position == 0:
                    began.append(said)
                clip[1] = position + FRAME_SAMPLES
                if clip[1] >= len(samples):
                    self._playing.remove(clip)
                    ended.append(said)
            if self.echo and (echo := self._ear.echo()) is not None:
                for i, sample in enumerate(echo[:FRAME_SAMPLES]):
                    mix[i] += int(sample * ECHO_GAIN)
            data = array("h", (max(-32768, min(32767, s)) for s in mix))
            frame = rtc.AudioFrame(data.tobytes(), SAMPLE_RATE, 1, FRAME_SAMPLES)
            if self.cancel:
                self._apm.set_stream_delay_ms(round(ECHO_DELAY * 1000))
                self._apm.process_stream(frame)
            await self._source.capture_frame(frame)
            now = time.time()
            for said in began:
                said.onset = now
            for said in ended:
                said.end = now
            next_at += FRAME_SECONDS
            await asyncio.sleep(max(0.0, next_at - loop.time()))


def _trim(samples: array) -> array:
    """Without the voice's leading and trailing near-silence, so a clip's first frame is
    its onset."""
    quiet = 32768 * LOUD
    loud = [i for i, s in enumerate(samples) if abs(s) >= quiet]
    return samples[loud[0] : loud[-1] + 1] if loud else samples


async def _synthesize(voice: str, text: str) -> array:
    model, _, name = voice.partition(":")
    tts = inference.TTS(model, voice=name) if name else inference.TTS(model)
    samples = array("h")
    resampler: rtc.AudioResampler | None = None
    stream = tts.synthesize(text)
    try:
        async for audio in stream:
            frame = audio.frame
            if frame.sample_rate == SAMPLE_RATE:
                samples.extend(array("h", frame.data))
                continue
            resampler = resampler or rtc.AudioResampler(
                frame.sample_rate, SAMPLE_RATE, num_channels=1
            )
            for out in resampler.push(frame):
                samples.extend(array("h", out.data))
        for out in resampler.flush() if resampler else []:
            samples.extend(array("h", out.data))
    finally:
        await stream.aclose()
        await tts.aclose()
    return samples


async def clips() -> dict[str, array]:
    """The dater's clips, synthesized once and kept as WAV files."""
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    found: dict[str, array] = {}
    async with utils.http_context.open():
        for name, clip in CLIPS.items():
            path = CLIPS_DIR / f"{name}.wav"
            if not path.exists():
                joined = array("h")
                for k, sentence in enumerate(clip.sentences):
                    if k:
                        joined.extend([0] * round(clip.pause * SAMPLE_RATE))
                    joined.extend(_trim(await _synthesize(clip.voice, sentence)))
                with wave.open(str(path), "wb") as out:
                    out.setnchannels(1)
                    out.setsampwidth(2)
                    out.setframerate(SAMPLE_RATE)
                    out.writeframes(joined.tobytes())
            with wave.open(str(path), "rb") as read:
                found[name] = array("h", read.readframes(read.getnframes()))
    return found


async def _message(
    queue: asyncio.Queue[dict[str, Any]], kind: str, *, timeout: float, **match: Any
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while True:
        left = deadline - time.time()
        if left <= 0:
            raise TimeoutError(f"no {kind} message from Nova in {timeout:g} s")
        message = await asyncio.wait_for(queue.get(), left)
        if message.get("type") == kind and all(message.get(k) == v for k, v in match.items()):
            return message


async def _trial(
    index: int,
    plan: Plan,
    room: rtc.Room,
    ear: Ear,
    mic: Mic,
    messages: asyncio.Queue[dict[str, Any]],
    audio: dict[str, array],
    line_s: float,
) -> Trial:
    mic.clear()
    mic.echo, mic.cancel = plan.echo, plan.aec
    ear.echoing, ear.far_end = plan.echo, mic.far_end if plan.aec else None
    ear.clear_echo()
    await ear.quiet(1.0)
    trial = Trial(index, plan.scenario, requested=time.time())
    first = len(ear.frames)
    await send(room, {"type": "play", "trial": index, "settle": plan.settle})
    started = await ear.onset(after=trial.requested, timeout=20.0)
    said: list[Said] = []
    if plan.clip is not None and started is not None:
        gain = CLIPS[plan.clip].gain
        if plan.scenario is Scenario.TV:  # the TV plays through the line, and after
            tv = audio["tv"] * (1 + int((line_s + 30) * SAMPLE_RATE / len(audio["tv"])))
            said.append(await mic.say(tv, at=started, gain=gain))
        else:
            at = started + plan.at * line_s + plan.offset
            said.append(await mic.say(audio[plan.clip], at=at, gain=gain, delay=plan.delay))
        if plan.again is not None:
            while said[0].end is None:
                await asyncio.sleep(0.01)
            went_on = await ear.onset(after=said[0].end + 0.1, timeout=15.0)
            if went_on is not None:
                second = await mic.say(audio[plan.clip], at=went_on + plan.again, gain=gain)
                said.append(second)
    done = await _message(messages, "done", timeout=120.0, trial=index)
    mic.clear()
    trial.nova = ear.frames[first:]
    trial.onsets = [s.onset for s in said if s.onset is not None]
    trial.ends = [s.end for s in said if s.end is not None]
    trial.intended = [s.intended for s in said]
    trial.agent = done
    return trial


async def dater(settings: Settings, *, smoke: bool = False) -> int:
    """smoke: one trial of each kind, to check the run works before paying for all of
    them; its report says so."""
    started = datetime.now()
    audio = await clips()
    name = f"nova-yield-{started:%Y%m%d-%H%M%S}"
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(DATER)
        .with_name("Yield rehearsal dater")
        .with_grants(api.VideoGrants(room_join=True, room=name))
        .with_room_config(
            api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME)])
        )
        .to_jwt()
    )
    room, ear = rtc.Room(), Ear()
    proof = Proof(room=name, harness_identity=DATER)
    messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    tasks: set[asyncio.Task[Any]] = set()

    def on_track(track: rtc.Track, publication: rtc.RemoteTrackPublication, who: Any) -> None:
        is_agent = who.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
        if track.kind == rtc.TrackKind.KIND_AUDIO and is_agent:
            proof.agent_identity, proof.agent_kind = who.identity, "agent"
            proof.agent_track_sid = publication.sid
            proof.agent_track_source = rtc.TrackSource.Name(publication.source)
            tasks.add(asyncio.ensure_future(ear.listen(track)))

    def on_data(packet: rtc.DataPacket) -> None:
        if packet.topic == TOPIC:
            messages.put_nowait(json.loads(packet.data))

    room.on("track_subscribed", on_track)
    room.on("data_received", on_data)
    await room.connect(settings.livekit_url, token)
    try:
        proof.room_sid = await room.sid
        source = rtc.AudioSource(SAMPLE_RATE, 1, queue_size_ms=50)
        microphone = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await room.local_participant.publish_track(microphone, options)
        mic = Mic(source, ear)
        tasks.add(asyncio.ensure_future(mic.run()))
        ready = await _message(messages, "ready", timeout=180.0)
        proof.agent_linked, proof.timed_words = ready["linked"], ready["timed"]
        trials: list[Trial] = []
        line_s = LINE_SECONDS
        for plan in MATRIX:
            for _ in range(1 if smoke else plan.trials):
                trial = await _trial(len(trials), plan, room, ear, mic, messages, audio, line_s)
                trials.append(trial)
                print(f"trial {trial.index} {plan.scenario}: {len(trial.agent['yields'])} yields")
                if plan.scenario is Scenario.NONE and (runs := sound_runs(trial.nova)):
                    line_s = runs[-1][1] - runs[0][0]
        await send(room, {"type": "bye"})
    finally:
        for task in tasks:
            task.cancel()
        await room.disconnect()
    proof.frames = ear.count
    proof.sound_s = sum(level >= LOUD for _, level in ear.frames) * FRAME_SECONDS
    proof.agent_heard = sum(sum(1 for _, on in t.agent.get("vad", []) if on) for t in trials)
    proof.trials = len(trials)
    proof.silent_trials = sum(silent(t) for t in trials)
    proof.voice_errors = sum(t.agent.get("voice_errors", 0) for t in trials)
    report = render(proof, trials, started=f"{started:%Y-%m-%d %H:%M}")
    REPORTS.mkdir(exist_ok=True)
    kind = "yield-smoke" if smoke else "yield"
    path = REPORTS / f"{kind}-{started:%Y-%m-%d-%H%M%S}.txt"
    path.write_text(report + "\n", encoding="utf-8")
    raw = {"proof": asdict(proof), "trials": [asdict(t) for t in trials]}
    path.with_suffix(".json").write_text(json.dumps(raw), encoding="utf-8")
    print(report)
    print(f"\nreport saved to {path}")
    return 1 if problems(proof) else 0


def main() -> None:
    settings = load_settings()
    os.environ.setdefault("LIVEKIT_URL", settings.livekit_url)
    os.environ.setdefault("LIVEKIT_API_KEY", settings.livekit_api_key)
    os.environ.setdefault("LIVEKIT_API_SECRET", settings.livekit_api_secret)
    if sys.argv[1:2] == ["agent"]:  # Nova's worker, started below in its own process
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        cli.run_app(server)
        return
    worker = subprocess.Popen([sys.executable, "-m", "nova.yield_live", "agent", "start"])
    try:
        time.sleep(WORKER_START)
        code = asyncio.run(dater(settings, smoke="--smoke" in sys.argv))
    finally:
        worker.terminate()
        try:
            worker.wait(20)
        except subprocess.TimeoutExpired:
            worker.kill()
    sys.exit(code)


if __name__ == "__main__":
    main()
