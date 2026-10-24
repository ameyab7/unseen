"""The moderator console's control over Nova (decision 0005, launch requirement 2): the
agent's side of the messages in packages/shared/src/moderator.ts, checked here in Python
against the same shapes (both are tested on packages/shared/fixtures/moderator.json). No
LiveKit here: nova/agent.py hands each data message and each moderator who joins to it.

- Who: a participant whose token carries role=moderator. Only the server that mints tokens
  sets attributes; a command from anyone else is ignored, with no reply, and logged as a
  code.
- Path: a MuteCommand, {"type": "mute" | "unmute", "seq": n, "session": id}, on the
  unseen.moderator data topic.
- Acknowledgement: a NovaState with what is true after it, to the moderators, for every
  command a moderator sends: applied (the state changed), unchanged (it already was),
  stale (an old seq from that moderator, or another session's command: nothing changes),
  invalid (not a MuteCommand). Each moderator who joins gets the state at once, and every
  change goes to them all, so each console shows what is true.
- What a mute does is Rehearsal.mute(): Nova stops mid-line and drops the line it was
  writing and everything queued, says nothing more, safety lines included, until a
  moderator unmutes it, and never resumes by itself. The show clock, every distress and
  conduct check, and every alert go on.
- How long: the session. A moderator's connection dropping changes nothing (the product
  owner's choice, 2026-10-23): a mute stays a mute, an unmuted Nova stays unmuted, and the
  moderator who comes back is told the state. A restarted agent is a new session, unmuted,
  and the console's commands for the old one are stale.
"""

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from nova.events import Event, EventLog

TOPIC = "unseen.moderator"
ROLE_ATTRIBUTE = "role"
MODERATOR_ROLE = "moderator"
MAX_SESSION = 64


class Result(StrEnum):
    STATE = "state"  # sent on its own: a moderator joined, or the state changed
    APPLIED = "applied"
    UNCHANGED = "unchanged"
    STALE = "stale"
    INVALID = "invalid"
    NOT_MODERATOR = "not_moderator"  # logged only: nothing goes back


@dataclass(frozen=True)
class Command:
    type: Literal["mute", "unmute"]
    seq: int
    session: str


def parse_command(payload: bytes | str) -> Command | None:
    """A MuteCommand exactly as packages/shared defines it (strict: no other keys), or
    None."""
    try:
        data = json.loads(payload)
    except ValueError, UnicodeDecodeError:
        return None
    if not isinstance(data, dict) or set(data) != {"type", "seq", "session"}:
        return None
    kind, seq, session = data["type"], data["seq"], data["session"]
    if kind not in ("mute", "unmute"):
        return None
    # A JSON number: never a bool or a string, and whole (JSON can't tell 2 from 2.0).
    if isinstance(seq, bool) or not isinstance(seq, int | float) or seq != int(seq) or seq < 1:
        return None
    if not isinstance(session, str) or not 1 <= len(session) <= MAX_SESSION:
        return None
    return Command(kind, int(seq), session)


def is_moderator(attributes: Mapping[str, str]) -> bool:
    return attributes.get(ROLE_ATTRIBUTE) == MODERATOR_ROLE


class Mutable(Protocol):
    @property
    def muted(self) -> bool: ...

    def mute(self) -> None: ...

    def unmute(self) -> None: ...


class ModeratorControl:
    """The mute's control for one agent session. handle() takes a data message and says
    what to send back, if anything; joined() what a participant who joined should get."""

    def __init__(
        self, target: Mutable, *, session: str | None = None, events: EventLog | None = None
    ) -> None:
        self._target = target
        self.session = session or secrets.token_hex(6)
        self._events = events or EventLog()
        self._last: dict[str, int] = {}  # each moderator's last seq: older ones are stale

    def state(self, result: Result = Result.STATE, seq: int = 0) -> bytes:
        """A NovaState, as JSON."""
        message = {
            "type": "nova_state",
            "session": self.session,
            "muted": self._target.muted,
            "seq": seq,
            "result": result.value,
        }
        return json.dumps(message).encode()

    def handle(self, identity: str, attributes: Mapping[str, str], payload: bytes) -> bytes | None:
        """The acknowledgement for a moderator's command, or None for anyone else's."""
        if not is_moderator(attributes):
            self._events.emit(Event.MODERATOR, result=Result.NOT_MODERATOR)
            return None
        command = parse_command(payload)
        if command is None:
            return self._answer(Result.INVALID, 0)
        if command.session != self.session or command.seq <= self._last.get(identity, 0):
            return self._answer(Result.STALE, command.seq)
        self._last[identity] = command.seq
        before = self._target.muted
        if command.type == "mute":
            self._target.mute()
        else:
            self._target.unmute()
        changed = self._target.muted != before
        return self._answer(Result.APPLIED if changed else Result.UNCHANGED, command.seq)

    def joined(self, attributes: Mapping[str, str]) -> bytes | None:
        """The state for a moderator who joined, or came back; None for anyone else."""
        return self.state() if is_moderator(attributes) else None

    def _answer(self, result: Result, seq: int) -> bytes:
        self._events.emit(Event.MODERATOR, result=result, muted=self._target.muted)
        return self.state(result, seq)
