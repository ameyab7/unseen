"""Join tokens for a two-person rehearsal (decision 0012): one room, two people, each with the
LiveKit identity Nova pins one seat to (NOVA_LEFT_PARTICIPANT, NOVA_RIGHT_PARTICIPANT).

    uv run python -m nova.two_person [--minutes 60] [--room NAME]

With the agent running (`docker compose -f services/nova/compose.yaml up --build`, from the
repository root), each person opens agents-playground.livekit.io on their own device, with
headphones, chooses Manual, and enters the server URL and their own token. Nova joins the
room with the first of them and opens the date once both are there. The run sheet is
docs/rehearsals/two-person-full-length.md.

A token lets whoever holds it into this room, as that person, until it expires: give each
one only to its person, privately, never in the room's chat. It is printed here and nowhere
else, never logged or written to a file; the Playground keeps it in the page, not in its
address. Nothing here calls LiveKit: the tokens are signed on this machine.
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from livekit import api

from nova.cards import Seat
from nova.config import ConfigError, Settings, load_settings
from nova.fixtures import REHEARSAL_PAIR
from nova.moderator import ROLE_ATTRIBUTE

# A full-length date and its setup, with time to start again once.
MINUTES = 60


@dataclass(frozen=True)
class Join:
    seat: Seat
    identity: str
    token: str = field(repr=False)  # a credential


def joins(settings: Settings, room: str, *, minutes: int = MINUTES) -> list[Join]:
    """One token a seat, for this room only, valid for `minutes`."""
    seats = {Seat.LEFT: settings.left_participant, Seat.RIGHT: settings.right_participant}
    if not all(seats.values()):
        raise ConfigError(
            "Set NOVA_LEFT_PARTICIPANT and NOVA_RIGHT_PARTICIPANT in services/nova/.env"
        )
    if minutes <= 0:
        raise ConfigError("A token must be valid for at least a minute")
    return [
        Join(
            seat,
            identity,
            api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(identity)
            .with_name(f"{REHEARSAL_PAIR.card(seat).first_name} ({seat} seat)")
            .with_attributes({ROLE_ATTRIBUTE: "dater"})
            .with_grants(api.VideoGrants(room_join=True, room=room))
            .with_ttl(timedelta(minutes=minutes))
            .to_jwt(),
        )
        for seat, identity in seats.items()
    ]


def render(settings: Settings, room: str, made: list[Join], *, minutes: int = MINUTES) -> str:
    lines = [
        f"Two-person rehearsal, room {room}, tokens valid for {minutes} minutes.",
        f"Server URL: {settings.livekit_url}",
        "Each person: agents-playground.livekit.io, Manual, the server URL and their own token.",
    ]
    for join in made:
        name = REHEARSAL_PAIR.card(join.seat).first_name
        lines += ["", f"{name}, {join.seat} seat (identity {join.identity}):", join.token]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Join tokens for a two-person rehearsal.")
    parser.add_argument("--minutes", type=int, default=MINUTES, help="how long they're valid")
    parser.add_argument("--room", help="the room's name (default: a new one, by the time)")
    args = parser.parse_args()
    settings = load_settings()
    room = args.room or f"nova-two-person-{datetime.now():%Y%m%d-%H%M%S}"
    made = joins(settings, room, minutes=args.minutes)
    print(render(settings, room, made, minutes=args.minutes))


if __name__ == "__main__":
    main()
