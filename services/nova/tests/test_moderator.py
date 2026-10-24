"""The moderator's mute, the agent's side (nova/moderator.py, decision 0005 launch
requirement 2): who is obeyed, stale and duplicate commands, acknowledgements, a lost
connection, and the same message shapes as packages/shared."""

import json
import re
from pathlib import Path

import pytest

from nova.events import EventLog
from nova.moderator import (
    MODERATOR_ROLE,
    ROLE_ATTRIBUTE,
    TOPIC,
    Command,
    ModeratorControl,
    Result,
    parse_command,
)

# The repository root holds packages/shared; the Docker image has only services/nova.
_ABOVE = Path(__file__).resolve().parents
SHARED = _ABOVE[3] / "packages" / "shared" if len(_ABOVE) > 3 else Path("/no/packages/shared")
MODERATOR = {ROLE_ATTRIBUTE: MODERATOR_ROLE}
DATER = {ROLE_ATTRIBUTE: "dater"}


class Nova:
    """Stands in for the rehearsal: counts what it was told."""

    def __init__(self) -> None:
        self.muted, self.mutes, self.unmutes = False, 0, 0

    def mute(self) -> None:
        if not self.muted:
            self.muted, self.mutes = True, self.mutes + 1

    def unmute(self) -> None:
        if self.muted:
            self.muted, self.unmutes = False, self.unmutes + 1


def command(kind: str, seq: int, session: str = "s1") -> bytes:
    return json.dumps({"type": kind, "seq": seq, "session": session}).encode()


def answer(reply: bytes | None) -> dict:
    assert reply is not None
    return json.loads(reply)


@pytest.fixture
def control():
    nova = Nova()
    return nova, ModeratorControl(nova, session="s1")


# --- The same shapes as packages/shared ---------------------------------------------------------

needs_shared = pytest.mark.skipif(
    not SHARED.exists(), reason="packages/shared isn't copied into the Docker image"
)


@needs_shared
def test_the_commands_the_console_may_send_are_those_packages_shared_accepts():
    fixtures = json.loads((SHARED / "fixtures" / "moderator.json").read_text(encoding="utf-8"))
    for message in fixtures["command"]["valid"]:
        assert isinstance(parse_command(json.dumps(message)), Command), message
    for message in fixtures["command"]["invalid"]:
        assert parse_command(json.dumps(message)) is None, message


@needs_shared
def test_the_topic_role_and_results_match_packages_shared():
    source = (SHARED / "src" / "moderator.ts").read_text(encoding="utf-8")
    assert f"MODERATOR_TOPIC = '{TOPIC}'" in source
    assert f"ROLE_ATTRIBUTE = '{ROLE_ATTRIBUTE}'" in source
    assert f"MODERATOR_ROLE = '{MODERATOR_ROLE}'" in source
    listed = re.search(r"NOVA_STATE_RESULTS = \[([^\]]*)\]", source)
    assert listed is not None
    results = re.findall(r"'(\w+)'", listed.group(1))
    assert results == [r.value for r in Result if r is not Result.NOT_MODERATOR]


@needs_shared
def test_every_state_nova_sends_has_the_shape_the_console_expects(control):
    nova, ctl = control
    fixtures = json.loads((SHARED / "fixtures" / "moderator.json").read_text(encoding="utf-8"))
    shape = fixtures["state"]["valid"][0]
    for reply in (ctl.state(), ctl.handle("mod", MODERATOR, command("mute", 1))):
        sent = answer(reply)
        assert set(sent) == set(shape)
        assert {k: type(v) for k, v in sent.items()} == {k: type(v) for k, v in shape.items()}


@pytest.mark.parametrize("payload", [b"", b"\xff\xfe", b"{", b"[]", b'{"type": "mute"}'])
def test_garbage_is_no_command(payload):
    assert parse_command(payload) is None


# --- Who is obeyed --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attributes", [DATER, {}, {ROLE_ATTRIBUTE: "Moderator"}, {"moderator": "true"}]
)
def test_only_a_moderator_is_obeyed_and_anyone_else_hears_nothing_back(control, attributes):
    nova, ctl = control
    assert ctl.handle("someone", attributes, command("mute", 1)) is None
    assert not nova.muted and nova.mutes == 0


def test_a_moderator_mutes_and_is_told_what_is_true(control):
    nova, ctl = control
    sent = answer(ctl.handle("mod", MODERATOR, command("mute", 10)))
    assert nova.muted and sent == {
        "type": "nova_state",
        "session": "s1",
        "muted": True,
        "seq": 10,
        "result": "applied",
    }


def test_unmuting_speaks_again_and_says_so(control):
    nova, ctl = control
    ctl.handle("mod", MODERATOR, command("mute", 1))
    sent = answer(ctl.handle("mod", MODERATOR, command("unmute", 2)))
    assert not nova.muted and (sent["muted"], sent["result"]) == (False, "applied")


# --- Duplicate and stale commands ----------------------------------------------------------------


def test_the_same_command_twice_is_one_mute(control):
    nova, ctl = control
    ctl.handle("mod", MODERATOR, command("mute", 1))
    again = answer(ctl.handle("mod", MODERATOR, command("mute", 1)))
    assert again["result"] == "stale" and nova.mutes == 1, "a resent message changes nothing"
    newer = answer(ctl.handle("mod", MODERATOR, command("mute", 2)))
    assert newer["result"] == "unchanged" and nova.mutes == 1 and newer["muted"]


def test_an_older_command_arriving_late_changes_nothing(control):
    """Unmute sent first, mute second, the unmute delivered last: the mute stands."""
    nova, ctl = control
    ctl.handle("mod", MODERATOR, command("mute", 1000))
    late = answer(ctl.handle("mod", MODERATOR, command("unmute", 999)))
    assert late["result"] == "stale" and late["muted"] and nova.muted


def test_a_command_for_an_earlier_session_is_stale(control):
    """A restarted agent is a new session: the console must hear its state first."""
    nova, ctl = control
    old = answer(ctl.handle("mod", MODERATOR, command("mute", 5, session="before-restart")))
    assert old["result"] == "stale" and not nova.muted and old["session"] == "s1"


def test_each_moderator_keeps_their_own_count(control):
    nova, ctl = control
    ctl.handle("first", MODERATOR, command("mute", 1_000_000))
    second = answer(ctl.handle("second", MODERATOR, command("unmute", 1)))
    assert second["result"] == "applied" and not nova.muted


def test_an_invalid_command_from_a_moderator_gets_the_state_and_changes_nothing(control):
    nova, ctl = control
    sent = answer(ctl.handle("mod", MODERATOR, b'{"type": "end", "seq": 1, "session": "s1"}'))
    assert sent["result"] == "invalid" and not nova.muted


# --- Joining, leaving, restarting -----------------------------------------------------------------


def test_a_moderator_who_joins_is_told_the_state_at_once(control):
    nova, ctl = control
    assert answer(ctl.joined(MODERATOR))["muted"] is False
    assert ctl.joined(DATER) is None, "nobody else hears Nova's state"


def test_a_lost_moderator_connection_changes_nothing_and_the_return_is_told(control):
    """The product owner's choice (2026-10-23): a mute stays a mute until a moderator unmutes."""
    nova, ctl = control
    ctl.handle("mod", MODERATOR, command("mute", 1))
    # The moderator's connection drops: the control isn't told, and nothing changes.
    back = answer(ctl.joined(MODERATOR))
    assert nova.muted and (back["muted"], back["result"], back["seq"]) == (True, "state", 0)
    later = answer(ctl.handle("mod", MODERATOR, command("unmute", 2)))
    assert later["result"] == "applied" and not nova.muted


def test_a_new_session_starts_unmuted_with_its_own_id():
    first, second = ModeratorControl(Nova()), ModeratorControl(Nova())
    assert first.session != second.session and len(first.session) == 12
    assert answer(second.state())["muted"] is False


def test_results_are_logged_as_codes_never_who_sent_them(caplog, control):
    import logging

    caplog.set_level(logging.INFO, logger="nova")
    nova = Nova()
    ctl = ModeratorControl(nova, session="s1", events=EventLog(logging.getLogger("nova")))
    ctl.handle("moderator-alex", MODERATOR, command("mute", 1))
    ctl.handle("dater-sam", DATER, command("mute", 2))
    assert "moderator result=applied muted=True" in caplog.text
    assert "moderator result=not_moderator" in caplog.text
    assert "alex" not in caplog.text and "sam" not in caplog.text
