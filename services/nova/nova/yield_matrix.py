"""The yield rehearsal's trials and arithmetic (decision 0004): which trials run, how a
trial's audio is read, the report, and the check that the run used real LiveKit audio. No
LiveKit here, so all of it is tested natively; nova/yield_live.py runs the trials in a real
LiveKit room, in Docker.

How it measures. One participant plays a dater: it publishes a microphone track (speech
synthesized once with LiveKit's TTS, in a voice that isn't Nova's) and subscribes to Nova's.
The other runs Nova as a rehearsal does (AgentSession, SessionSpeaker, Rehearsal), playing a
fixed test line on the dater's request. Both run on one machine, so they share one clock.

- The dater's onset: when the first frame of a clip went into its microphone source.
- Nova stopping, as the dater heard it: the end of Nova's audio, as received over WebRTC,
  before a quiet of at least SILENCE (Nova's pauses between sentences are shorter). What
  was heard of a line counts from its first sound after the agent's voice started it.
- Nova stopping, at the agent: the voice detector's start and the voice's stop, which the
  agent reports back (Yield.began, Yield.stop_ms).
- Where the cut came: how much of what Nova was saying the dater heard before it stopped,
  against how much the agent says had played (PlaybackFinishedEvent.playback_position);
  and the sentence, counted from the pauses in Nova's received audio, against the sentence
  the agent placed the cut in (only when its voice timed the words).

Everything recorded is a number: times, levels, counts. The test line's words never leave
the agent, and the dater's speech is synthesized test audio, never a person's.
"""

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from nova.rehearsal import RESTART_WITHIN, Then

AGENT_NAME = "nova-yield-rehearsal"  # dispatched by name only: never joins another room
TOPIC = "nova-yield"  # the data messages between the two, numbers only
SAMPLE_RATE = 48_000
FRAME_SAMPLES = 480  # 10 ms
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
LOUD = 0.01  # RMS, of full scale, above which a 10 ms frame of Nova's audio is sound (-40 dBFS)
# Seconds of quiet that end what Nova was saying. Its pauses between sentences ran to 1.03 s
# (the smoke run of 2026-10-23), and after a cut it waits timing.turn_gap (1.5 s) of quiet
# before going on, so a stop is always the longer quiet.
SILENCE = 1.3
SENTENCE = 0.25  # seconds of sound before a quiet counts as a sentence ending, not a click
TOO_LATE = 3.0  # Nova still talking this long after the dater began: a missed yield
LINE_SECONDS = 11.0  # the test line's length, until the trials with no interruption measure it
ECHO_DELAY = 0.15  # seconds from Nova's audio reaching the dater's device to its microphone
ECHO_GAIN = 0.5  # how loud that echo is: -6 dB, a phone on speaker with nothing cancelling it
UPLINK_DELAY = 0.4  # the delayed trials: seconds the dater's audio is held before it's sent

# Said only in this rehearsal's own room, never on air: four sentences of about the same
# length and no commas, so the voice's pauses come between sentences only.
TEST_LINE = (
    "Here is a line we wrote just for this test. It has four sentences of about the same "
    "length. Each one gives a dater a chance to cut in. This is the last one and then we are "
    "done."
)
TEST_SENTENCES = 4

DATER_VOICE = "deepgram/aura-2:odysseus"  # not Nova's voice (nova/voices.py shortlist)
TV_VOICE = "inworld/inworld-tts-1.5-max:Ashley"


@dataclass(frozen=True)
class Clip:
    """What the dater's microphone picks up: sentences in a voice, a pause between them."""

    voice: str
    sentences: tuple[str, ...]
    pause: float = 0.0
    gain: float = 1.0


CLIPS = {
    "cut_in": Clip(DATER_VOICE, ("Oh wait, I actually have a story about that one.",)),
    "pauses": Clip(
        DATER_VOICE,
        ("I have to say something here.", "It's about the bakery I love.", "They know my order."),
        pause=0.7,
    ),
    # A television in the dater's room: another voice, a quarter as loud (-12 dB).
    "tv": Clip(
        TV_VOICE,
        (
            "And in other news tonight the river is up again.",
            "Forecasters say the rain will ease by Thursday.",
            "Now over to the sport.",
        ),
        pause=0.8,
        gain=0.25,
    ),
    "stop": Clip(DATER_VOICE, ("I want to stop now.",)),
}


class Scenario(StrEnum):
    NONE = "none"
    EARLY = "early"
    MIDDLE = "middle"
    LATE = "late"
    TWICE = "twice"
    PAUSES = "pauses"
    TV = "tv"
    STALE = "stale"
    DELAYED = "delayed"
    STOP = "stop"
    ECHO = "echo"
    ECHO_AEC = "echo_aec"
    ECHO_AEC_CUT = "echo_aec_cut"


@dataclass(frozen=True)
class Plan:
    """One kind of trial. The dater says `clip` at `at` of the line's length after Nova's
    audio began, plus `offset` seconds; TWICE cuts in again `again` seconds after Nova goes
    on. `delay` holds the dater's audio before it's sent; `echo` loops Nova's audio into the
    dater's microphone, `aec` through WebRTC's echo canceller as a phone runs it."""

    scenario: Scenario
    trials: int
    what: str
    clip: str | None = None
    at: float = 0.0
    offset: float = 0.0
    again: float | None = None
    delay: float = 0.0
    echo: bool = False
    aec: bool = False
    expect_yield: bool = True
    settle: float = 1.0  # seconds the agent waits after the line for late events


MATRIX: tuple[Plan, ...] = (
    Plan(Scenario.NONE, 5, "no interruption", expect_yield=False),
    Plan(Scenario.EARLY, 5, "cut in as Nova starts (0.3 s)", "cut_in", 0.0, 0.3),
    Plan(Scenario.MIDDLE, 5, "cut in halfway", "cut_in", 0.5),
    Plan(Scenario.LATE, 5, "cut in late (85%)", "cut_in", 0.85),
    Plan(
        Scenario.TWICE, 5, "cut in, then again 1.5 s after Nova goes on", "cut_in", 0.3, again=1.5
    ),
    Plan(Scenario.PAUSES, 3, "three sentences with 0.7 s pauses", "pauses", 0.4),
    Plan(Scenario.TV, 3, "a TV at -12 dB through the line", "tv", 0.0, expect_yield=False),
    Plan(Scenario.STALE, 3, "cut in as the line ends", "cut_in", 1.0, -0.15, expect_yield=False),
    Plan(Scenario.DELAYED, 3, "cut in halfway, sent 0.4 s late", "cut_in", 0.5, delay=UPLINK_DELAY),
    # The date isn't live in this rehearsal, so a stop request only alerts the moderator:
    # the kind line after a cut is the tests' (test_rehearsal.py).
    Plan(Scenario.STOP, 3, "a request to stop, halfway: the alert", "stop", 0.5, settle=4.0),
    Plan(
        Scenario.ECHO,
        3,
        "Nova's own audio in the mic, nothing cancelling it",
        echo=True,
        expect_yield=False,
    ),
    Plan(
        Scenario.ECHO_AEC,
        5,
        "the same through WebRTC's echo canceller",
        echo=True,
        aec=True,
        expect_yield=False,
    ),
    Plan(
        Scenario.ECHO_AEC_CUT,
        3,
        "echo cancelled, and a dater cutting in halfway",
        "cut_in",
        0.5,
        echo=True,
        aec=True,
    ),
)


def rms(samples: Sequence[int]) -> float:
    """Root mean square of 16-bit samples, as a share of full scale."""
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0


def sound_runs(
    frames: Iterable[tuple[float, float]], *, loud: float = LOUD, bridge: float = SILENCE
) -> list[tuple[float, float]]:
    """Stretches of Nova's audio, from (time, rms) per frame received: each from a loud
    frame to the end of the last one before a quiet of at least `bridge` seconds."""
    runs: list[tuple[float, float]] = []
    start = last = None
    for t, level in frames:
        if level < loud:
            continue
        if start is None or last is None:
            start = last = t
            continue
        if t - last >= bridge:
            runs.append((start, last + FRAME_SECONDS))
            start = t
        last = t
    if start is not None and last is not None:
        runs.append((start, last + FRAME_SECONDS))
    return runs


def pauses(
    frames: Iterable[tuple[float, float]],
    start: float,
    end: float,
    *,
    gap: float,
    loud: float = LOUD,
) -> int:
    """Quiets of at least `gap` seconds in Nova's audio between start and end, each after
    at least SENTENCE of sound: the sentences that had ended. A click before the first word
    is no sentence."""
    count, last, since = 0, None, None
    for t, level in frames:
        if t < start or t > end or level < loud:
            continue
        if last is not None and since is not None and t - last >= gap:
            count += last - since >= SENTENCE
            since = t
        if since is None:
            since = t
        last = t
    return count


def calibrate_gap(lines: Sequence[Sequence[tuple[float, float]]]) -> float | None:
    """The shortest quiet that splits every whole test line heard into exactly its
    TEST_SENTENCES sentences, from the trials with no interruption; None if none does, and
    then sentences aren't compared."""
    for tenths in range(10, 80, 2):
        gap = tenths / 100
        counts = []
        for frames in lines:
            runs = sound_runs(frames)
            if not runs:
                break
            start, end = runs[0][0], runs[-1][1]
            counts.append(pauses(frames, start, end, gap=gap))
        if lines and len(counts) == len(lines) and all(c == TEST_SENTENCES - 1 for c in counts):
            return gap
    return None


def percentile(values: Sequence[float], share: float) -> float:
    """Nearest rank."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


@dataclass
class Trial:
    """One trial, as recorded: numbers only. `nova` is (time, rms) per 10 ms frame of Nova's
    audio received; `onsets` and `ends` when each clip's first and last frame went into the
    dater's microphone, `intended` when the schedule meant it to; `agent` what the agent
    reported (voice detector, lines handed over, yields, stops, alerts)."""

    index: int
    scenario: Scenario
    requested: float
    nova: list[tuple[float, float]] = field(default_factory=list)
    onsets: list[float] = field(default_factory=list)
    ends: list[float] = field(default_factory=list)
    intended: list[float] = field(default_factory=list)
    agent: dict = field(default_factory=dict)


@dataclass
class Outcome:
    """What one trial showed."""

    scenario: Scenario
    yields: int = 0
    heard_ms: list[int] = field(default_factory=list)  # onset to Nova stopped, as heard
    agent_ms: list[int] = field(default_factory=list)  # onset to the agent's voice stopped
    detector_ms: list[int] = field(default_factory=list)  # onset to the voice detector's start
    missed: int = 0  # a dater cut in and Nova didn't stop
    false_yields: int = 0  # Nova stopped with no dater speaking
    then: list[Then] = field(default_factory=list)
    duplicate: bool = False  # the room heard the whole line twice
    heard_s: float = 0.0  # all of Nova's audio heard in the trial
    cut_error_ms: list[int] = field(default_factory=list)  # heard at the cut - agent's played
    sentence_checked: int = 0
    sentence_mismatch: int = 0
    went_on_mid_speech: int = 0  # Nova went on while the dater was between sentences
    alert_ms: list[int] = field(default_factory=list)  # STOP: onset to the distress alert


def _began_playing(trial: Trial, cut: dict, playing: tuple[float, float]) -> float:
    """When the dater first heard what Nova was saying when cut: the first sound received
    after the agent's voice started it (`speaks`: called, started, finished, characters),
    not the start of the stretch heard, which a long pause between sentences would move.
    The stretch's start when the agent didn't say."""
    for _, started, finished, _ in trial.agent.get("speaks", []):
        if started is None or finished is None or not started <= cut["cut"] <= finished:
            continue
        return next(
            (t for t, level in trial.nova if started <= t <= playing[1] and level >= LOUD),
            playing[0],
        )
    return playing[0]


def outcome(trial: Trial, plan: Plan, *, line_s: float, gap: float | None) -> Outcome:
    """Reads one trial: each dater onset matched to the yield it caused (the voice detector
    heard them start within TOO_LATE), and Nova's received audio around it."""
    got = Outcome(trial.scenario)
    runs = sound_runs(trial.nova)
    got.heard_s = sum(end - start for start, end in runs)
    yields = trial.agent.get("yields", [])
    got.yields = len(yields)
    got.then = [Then(y["then"]) for y in yields]
    matched: set[int] = set()
    for onset in trial.onsets if plan.clip != "tv" else []:
        k = next(
            (
                k
                for k, y in enumerate(yields)
                if k not in matched and onset - 0.2 <= y["began"] <= onset + TOO_LATE
            ),
            None,
        )
        playing = next((run for run in runs if run[0] <= onset <= run[1]), None)
        if k is None:
            if plan.expect_yield and playing is not None and playing[1] - onset > TOO_LATE:
                got.missed += 1
            continue
        matched.add(k)
        y = yields[k]
        got.detector_ms.append(round((y["began"] - onset) * 1000))
        got.agent_ms.append(round((y["began"] - onset) * 1000 + y["stop_ms"]))
        if playing is None:
            continue
        got.heard_ms.append(round((playing[1] - onset) * 1000))
        began = _began_playing(trial, y, playing)
        got.cut_error_ms.append(round((playing[1] - began) * 1000) - y["played_ms"])
        if gap is not None and y["sentence"] is not None:
            got.sentence_checked += 1
            heard = pauses(trial.nova, began, playing[1], gap=gap)
            got.sentence_mismatch += heard != y["sentence"]
    if plan.clip in (None, "tv"):
        got.false_yields = 0 if plan.clip == "tv" else len(yields)
    else:
        got.false_yields = len(yields) - len(matched)
    # Restarts are allowed RESTART_WITHIN of audio heard twice each; anything near a second
    # whole line is a duplicate.
    allowed = line_s + RESTART_WITHIN * got.then.count(Then.RESTARTED) + 0.5
    got.duplicate = got.heard_s > max(allowed, 1.5 * line_s)
    if plan.clip == "pauses" and trial.onsets and trial.ends:
        began, finished = trial.onsets[0], trial.ends[-1]
        got.went_on_mid_speech = sum(began + 0.5 < start < finished for start, _ in runs[1:])
    alerts = [t for t, code in trial.agent.get("alerts", []) if code == "dater_distressed"]
    if plan.scenario is Scenario.STOP and trial.onsets and alerts:
        got.alert_ms.append(round((alerts[0] - trial.onsets[0]) * 1000))
    return got


@dataclass
class Proof:
    """That the run used real LiveKit audio both ways, not text or a fake: the room and
    tracks LiveKit gave, Nova's audio received over WebRTC, and the agent hearing the
    dater's track through its voice detector."""

    room: str = ""
    room_sid: str = ""
    agent_identity: str = ""
    agent_kind: str = ""  # "agent" for an agent participant
    agent_track_sid: str = ""
    agent_track_source: str = ""
    harness_identity: str = ""
    agent_linked: str = ""  # the participant the agent's session listened to
    frames: int = 0  # 10 ms frames of Nova's audio received over WebRTC
    sound_s: float = 0.0  # of which loud
    agent_heard: int = 0  # voice-detector starts the agent reported, from the dater's track
    timed_words: bool = False  # the agent's voice timed its words
    trials: int = 0
    silent_trials: int = 0  # trials in which the dater never heard Nova's line
    voice_errors: int = 0  # lines Nova's voice failed on (a voice service refusing, say)


def problems(proof: Proof) -> list[str]:
    """Why a run can't count as a real LiveKit rehearsal; none means it can."""
    found = []
    if not proof.room_sid.startswith("RM_"):
        found.append("no LiveKit room id")
    if proof.agent_kind != "agent":
        found.append("Nova wasn't an agent participant in the room")
    if not proof.agent_track_sid.startswith("TR_"):
        found.append("no audio track from Nova")
    if proof.frames < 1000 or proof.sound_s < 10.0:
        found.append("too little of Nova's audio received over WebRTC")
    if proof.agent_heard < 1:
        found.append("the agent's voice detector never heard the dater's track")
    if not proof.agent_linked or proof.agent_linked != proof.harness_identity:
        found.append("the agent didn't listen to the dater's track")
    # A trial in which Nova never spoke measures nothing, and reads as "nothing missed".
    if proof.silent_trials or proof.voice_errors:
        found.append(
            f"Nova's line never reached the dater in {proof.silent_trials} of {proof.trials} "
            f"trials ({proof.voice_errors} voice errors): run it again"
        )
    return found


def silent(trial: Trial) -> bool:
    """The dater heard less than a second of Nova in the trial: its voice failed."""
    return sum(end - start for start, end in sound_runs(trial.nova)) < 1.0


def _ms(values: Sequence[int]) -> str:
    if not values:
        return "-"
    return f"{round(statistics.median(values))}/{round(percentile(values, 0.95))}/{max(values)}"


def render(proof: Proof, trials: Sequence[Trial], *, started: str) -> str:
    """The report: codes and numbers only."""
    plans = {plan.scenario: plan for plan in MATRIX}
    whole = [t.nova for t in trials if t.scenario is Scenario.NONE]
    lengths = [sum(e - s for s, e in sound_runs(frames)) for frames in whole]
    line_s = statistics.median(lengths) if lengths else LINE_SECONDS
    gap = calibrate_gap(whole)
    results = [outcome(t, plans[t.scenario], line_s=line_s, gap=gap) for t in trials]
    found = problems(proof)
    linked = proof.agent_linked == proof.harness_identity
    lines = [
        f"Nova yield rehearsal, {started}: real LiveKit audio both ways"
        if not found
        else f"Nova yield rehearsal, {started}: NOT VALID ({'; '.join(found)})",
        f"Room {proof.room} ({proof.room_sid}); Nova {proof.agent_identity} ({proof.agent_kind}), "
        f"track {proof.agent_track_sid} ({proof.agent_track_source}); the dater "
        f"{proof.harness_identity}, listened to by the agent: {linked}",
        f"Nova's audio received over WebRTC: {proof.frames} frames, {proof.sound_s:.1f} s of "
        f"sound. The agent's voice detector heard the dater start {proof.agent_heard} times.",
        f"Words timed by the voice (resume from the sentence played): {proof.timed_words}. "
        f"The test line: {line_s:.1f} s whole (median of {len(lengths)}); sentence pauses "
        + (
            f"of {gap:.2f} s or more split it in {TEST_SENTENCES}"
            if gap
            else "not found: sentences not compared"
        ),
        "",
        "Times in ms as median/p95/max. Heard: from the dater's first frame into their mic to "
        "the last of Nova's audio they received. Agent: to Nova's voice stopped, at the agent. "
        "Detector: to the agent's voice detector hearing them.",
        f"  {'scenario':<14}{'n':>3}{'yields':>8}{'missed':>8}{'false':>7}{'dup':>5}"
        f"{'heard':>16}{'agent':>16}{'detector':>16}  then",
    ]
    total_heard: list[int] = []
    for plan in MATRIX:
        got = [r for r in results if r.scenario is plan.scenario]
        if not got:
            continue
        heard = [v for r in got for v in r.heard_ms]
        if plan.scenario in (Scenario.EARLY, Scenario.MIDDLE, Scenario.LATE, Scenario.TWICE):
            total_heard += heard
        then: dict[str, int] = {}
        for r in got:
            for t in r.then:
                then[t] = then.get(t, 0) + 1
        lines.append(
            f"  {plan.scenario:<14}{len(got):>3}{sum(r.yields for r in got):>8}"
            f"{sum(r.missed for r in got):>8}{sum(r.false_yields for r in got):>7}"
            f"{sum(r.duplicate for r in got):>5}{_ms(heard):>16}"
            f"{_ms([v for r in got for v in r.agent_ms]):>16}"
            f"{_ms([v for r in got for v in r.detector_ms]):>16}  "
            + (", ".join(f"{k} {v}" for k, v in then.items()) or "-")
        )
    errors = [v for r in results for v in r.cut_error_ms]
    # An uninterrupted line, the same way: the agent's playout against the sound the dater
    # heard. The voice's quiet lead-in and tail count at the agent, never as sound, so a cut
    # placed exactly reads about this much below zero.
    baseline = [
        round((sum(e - s for s, e in sound_runs(t.nova)) - (call[2] - call[1])) * 1000)
        for t in trials
        if t.scenario is Scenario.NONE
        for call in t.agent.get("speaks", [])[:1]
        if call[1] is not None
    ]
    checked = sum(r.sentence_checked for r in results)
    mismatched = sum(r.sentence_mismatch for r in results)
    alerts = [v for r in results for v in r.alert_ms]
    lines += [
        "",
        f"Dater cutting in (early, middle, late, twice): {len(total_heard)} stops heard, "
        f"{_ms(total_heard)} ms (median/p95/max).",
        "Where the cut came: the sound the dater heard of what Nova was saying minus what the "
        f"agent says had played, {_ms(errors)} ms over {len(errors)} cuts (an uninterrupted "
        f"line, sound heard minus the agent's playout: {_ms(baseline)} ms, the voice's quiet "
        f"lead-in and tail); the sentence the agent placed it in against the pauses heard: "
        f"{mismatched} of {checked} differ.",
        f"Nova went on while the dater was between sentences: "
        f"{sum(r.went_on_mid_speech for r in results)} times.",
        f"A request to stop, to the moderator's distress alert: {_ms(alerts)} ms.",
        "",
        *[f"  {plan.scenario}: {plan.what}" for plan in MATRIX],
        "",
        "Limits: one machine runs both sides and LiveKit Cloud carries the audio, so the network "
        "is real but short; the echo is simulated (Nova's received audio, delayed and quieter, "
        "into the microphone), not a room's acoustics; the TV and the dater are synthesized "
        "speech; a stop heard is the last loud frame received, so Nova's trailing quiet sounds "
        "and the receiver's jitter buffer move it by tens of ms; the sentence heard comes from "
        "pauses in Nova's audio, which a voice may also make inside a sentence.",
    ]
    return "\n".join(lines)
