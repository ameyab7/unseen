"""The mute rehearsal's trials and arithmetic (decision 0005, launch requirement 2): which
trials run, how one is read, the report, and the check that the run used real LiveKit audio
and the real control path. No LiveKit here, so all of it is tested natively;
nova/mute_live.py runs the trials in a real LiveKit room, in Docker.

How it measures. Nova runs as a rehearsal does, with the agent's own moderator control
(nova/agent.py connect_moderators, nova/moderator.py) and plays a fixed test line when
asked. Three participants share one machine and one clock: a dater, who publishes a
microphone and hears Nova over WebRTC; a moderator, whose token carries role=moderator and
who sends the console's commands on the unseen.moderator topic; and the dater again as an
impostor, sending the same commands without the role.

- The mute's stop: from the moderator sending mute to the last of Nova's audio the dater
  received, while Nova was playing.
- Held: no sound from Nova after the stop, through the rest of the trial, a line asked for
  meanwhile included.
- The acknowledgement: from sending a command to the moderator receiving the NovaState for
  it, with its result.

Everything recorded is a number or a code: times, levels, counts and results.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from nova.yield_matrix import LOUD, percentile, sound_runs

AGENT_NAME = "nova-mute-rehearsal"  # dispatched by name only: never joins another room
TOPIC = "nova-mute"  # the rehearsal's own requests and reports, numbers only
HELD_FOR = 3.0  # seconds of silence after a stop that show the mute held
AT = 0.4  # how far into the test line (as a share of it) a command or a cut-in comes


class Scenario(StrEnum):
    MID_LINE = "mid_line"  # mute while Nova speaks, then unmute, then a line is said again
    IDLE = "idle"  # mute while Nova is silent: the next line asked for isn't said
    IMPOSTOR = "impostor"  # the dater sends the mute: nothing changes, nothing comes back
    STALE = "stale"  # an old seq, and another session's command: nothing changes
    DUPLICATE = "duplicate"  # the same mute twice: one mute
    LOST = "lost"  # muted, the moderator's connection drops, comes back: still muted, told
    SAFETY = "safety"  # muted, the dater asks to stop: the moderator's alert still comes
    AFTER_CUT = "after_cut"  # a dater cuts in, Nova yields, mute while it waits: no resume


@dataclass(frozen=True)
class Plan:
    scenario: Scenario
    trials: int
    what: str


MATRIX: tuple[Plan, ...] = (
    Plan(Scenario.MID_LINE, 5, "mute mid-line, then unmute and a line said"),
    Plan(Scenario.IDLE, 2, "mute while silent; the line asked for isn't said"),
    Plan(Scenario.IMPOSTOR, 3, "the dater sends mute: ignored, no answer"),
    Plan(Scenario.STALE, 3, "an old seq and another session's mute: ignored, answered stale"),
    Plan(Scenario.DUPLICATE, 3, "the same mute sent twice: one mute"),
    Plan(Scenario.LOST, 2, "muted; the moderator drops and returns: still muted, and told"),
    Plan(Scenario.SAFETY, 3, "muted; the dater asks to stop: the moderator is alerted"),
    Plan(Scenario.AFTER_CUT, 3, "Nova yields to a dater; mute while it waits: never resumes"),
)


@dataclass
class Trial:
    """One trial, as recorded: numbers and codes only. nova: (time, rms) per 10 ms frame of
    Nova's audio the dater received. sent: (time, sender, type, seq) for each control
    message. acks: (time, result, muted, seq) as the moderator received them; impostor_acks
    any the dater received. agent: what Nova's side reported back."""

    index: int
    scenario: Scenario
    nova: list[tuple[float, float]] = field(default_factory=list)
    sent: list[tuple[float, str, str, int]] = field(default_factory=list)
    acks: list[tuple[float, str, bool, int]] = field(default_factory=list)
    impostor_acks: int = 0
    rejoined_state: tuple[float, bool] | None = None  # the moderator's state on returning
    clip_onsets: list[float] = field(default_factory=list)
    clip_ends: list[float] = field(default_factory=list)
    marks: dict[str, float] = field(default_factory=dict)  # named moments in the trial
    agent: dict = field(default_factory=dict)


@dataclass
class Outcome:
    scenario: Scenario
    passed: bool
    stop_ms: int | None = None  # mute sent to Nova's audio ending, as heard
    ack_ms: int | None = None  # mute sent to its acknowledgement
    why: str = ""


def sound_after(trial: Trial, start: float, end: float | None = None) -> bool:
    """Nova was heard between start and end (the trial's end if None)."""
    return any(t > start and (end is None or t <= end) and level >= LOUD for t, level in trial.nova)


def last_sound(trial: Trial, before: float | None = None) -> float | None:
    times = [t for t, level in trial.nova if level >= LOUD and (before is None or t <= before)]
    return max(times) if times else None


def _ack(trial: Trial, seq: int) -> tuple[float, str, bool, int] | None:
    return next((a for a in trial.acks if a[3] == seq), None)


def outcome(trial: Trial) -> Outcome:
    """Reads one trial against what its scenario must show."""
    s, m, agent = trial.scenario, trial.marks, trial.agent
    mute = next((x for x in trial.sent if x[1] == "moderator" and x[2] == "mute"), None)
    got = Outcome(s, passed=False)
    if mute is not None:
        ack = _ack(trial, mute[3])
        got.ack_ms = round((ack[0] - mute[0]) * 1000) if ack else None
        # The stop: the end of what Nova was saying when an applied mute was sent (its
        # pauses between sentences are shorter than the quiet that ends a stretch).
        applied = ack is not None and ack[1] == "applied"
        playing = next((r for r in sound_runs(trial.nova) if r[0] <= mute[0] <= r[1]), None)
        if applied and playing is not None:
            got.stop_ms = round((playing[1] - mute[0]) * 1000)
    match s:
        case Scenario.MID_LINE:
            stop = (trial.marks.get("unmute") or float("inf")) - 0.01
            held = mute is not None and not sound_after(trial, mute[0] + 1.0, stop)
            again = sound_after(trial, m.get("unmute", float("inf")))
            got.passed = got.stop_ms is not None and held and again and got.ack_ms is not None
            got.why = "" if got.passed else f"stopped={got.stop_ms} held={held} again={again}"
        case Scenario.IDLE:
            quiet = not sound_after(trial, m["play"], m.get("unmute"))
            again = sound_after(trial, m.get("unmute", float("inf")))
            got.passed = quiet and again and agent.get("unsaid", 0) >= 1
            got.why = "" if got.passed else f"quiet={quiet} again={again}"
        case Scenario.IMPOSTOR:
            spoke_on = sound_after(trial, m["impostor"] + 1.0)
            got.passed = (
                spoke_on and trial.impostor_acks == 0 and not trial.acks and not agent.get("muted")
            )
            got.why = (
                ""
                if got.passed
                else f"spoke_on={spoke_on} acks={trial.impostor_acks}+{len(trial.acks)}"
            )
        case Scenario.STALE:
            spoke_on = sound_after(trial, m["stale"] + 1.0)
            results = {a[1] for a in trial.acks}
            got.passed = spoke_on and results == {"stale"} and not agent.get("muted")
            got.why = "" if got.passed else f"spoke_on={spoke_on} results={sorted(results)}"
        case Scenario.DUPLICATE:
            results = [a[1] for a in trial.acks][:2]  # the mute and its repeat; then the unmute
            got.passed = results == ["applied", "stale"] and agent.get("mutes_delta") == 1
            got.why = "" if got.passed else f"results={results} mutes={agent.get('mutes_delta')}"
        case Scenario.LOST:
            quiet = not sound_after(trial, m["play"], m.get("unmute"))
            told = trial.rejoined_state is not None and trial.rejoined_state[1] is True
            again = sound_after(trial, m.get("unmute", float("inf")))
            got.passed = quiet and told and again
            got.why = "" if got.passed else f"quiet={quiet} told={told} again={again}"
        case Scenario.SAFETY:
            alerted = "dater_distressed" in agent.get("alerts", [])
            quiet = not sound_after(trial, m["clip"], m.get("unmute"))
            got.passed = alerted and quiet
            got.why = "" if got.passed else f"alerted={alerted} quiet={quiet}"
        case Scenario.AFTER_CUT:
            yielded = agent.get("yields_then", []) == ["stopped"]
            resumed = trial.clip_ends and sound_after(trial, trial.clip_ends[-1], m.get("unmute"))
            got.passed = yielded and not resumed
            got.why = "" if got.passed else f"yields={agent.get('yields_then')} resumed={resumed}"
    return got


@dataclass
class Proof:
    """That the run used real LiveKit audio and the real control path."""

    room_sid: str = ""
    agent_kind: str = ""
    agent_track_sid: str = ""
    frames: int = 0
    sound_s: float = 0.0
    moderator_attributes: dict = field(default_factory=dict)  # as the room saw them
    states_received: int = 0  # NovaStates the moderator received in all
    voice_errors: int = 0
    trials: int = 0


def problems(proof: Proof) -> list[str]:
    found = []
    if not proof.room_sid.startswith("RM_"):
        found.append("no LiveKit room id")
    if proof.agent_kind != "agent" or not proof.agent_track_sid.startswith("TR_"):
        found.append("no agent participant or audio track")
    if proof.frames < 1000 or proof.sound_s < 10.0:
        found.append("too little of Nova's audio received over WebRTC")
    if proof.moderator_attributes.get("role") != "moderator":
        found.append("the moderator's token attribute never reached the room")
    if proof.states_received < 1:
        found.append("the moderator never heard Nova's state")
    if proof.voice_errors:
        found.append(f"{proof.voice_errors} voice errors: run it again")
    return found


def _ms(values: Sequence[int]) -> str:
    if not values:
        return "-"
    return f"{round(statistics.median(values))}/{round(percentile(values, 0.9))}/{max(values)}"


def render(proof: Proof, trials: Sequence[Trial], *, started: str) -> str:
    results = [outcome(t) for t in trials]
    found = problems(proof)
    lines = [
        f"Nova mute rehearsal, {started}: "
        + (
            "real LiveKit audio and control path"
            if not found
            else f"NOT VALID ({'; '.join(found)})"
        ),
        f"Room {proof.room_sid}; agent ({proof.agent_kind}) track {proof.agent_track_sid}; "
        f"Nova's audio received: {proof.frames} frames, {proof.sound_s:.1f} s of sound; the "
        f"moderator's role as the room saw it: {proof.moderator_attributes.get('role')}; "
        f"NovaStates received: {proof.states_received}",
        "",
        "Times in ms as median/p90/max. Stop: the mute sent to the last of Nova's audio the "
        "dater received. Ack: the mute sent to the moderator receiving its NovaState.",
        f"  {'scenario':<12}{'n':>3}{'passed':>8}{'stop':>16}{'ack':>16}",
    ]
    for plan in MATRIX:
        got = [r for r in results if r.scenario is plan.scenario]
        if not got:
            continue
        stops = [r.stop_ms for r in got if r.stop_ms is not None]
        acks = [r.ack_ms for r in got if r.ack_ms is not None]
        lines.append(
            f"  {plan.scenario:<12}{len(got):>3}{sum(r.passed for r in got):>8}"
            f"{_ms(stops):>16}{_ms(acks):>16}"
        )
    failed = [(t.index, r) for t, r in zip(trials, results, strict=True) if not r.passed]
    lines += ["", f"Trials that failed: {len(failed)} of {len(results)}"]
    lines += [f"  trial {k} {r.scenario}: {r.why}" for k, r in failed]
    lines += ["", *[f"  {p.scenario}: {p.what}" for p in MATRIX], ""]
    lines.append(
        "Limits: one machine runs every participant and LiveKit Cloud carries the audio and the "
        "messages; the console is simulated by a participant with the moderator's token "
        "attribute; the dater's speech is synthesized."
    )
    return "\n".join(lines)
