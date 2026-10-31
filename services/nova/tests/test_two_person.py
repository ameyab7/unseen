"""Join tokens for the two-person rehearsal, signed and checked offline: no LiveKit call."""

import base64
import json

import pytest
from livekit import api

from nova.cards import Seat
from nova.config import ConfigError, Settings
from nova.two_person import joins, render

SECRET = "a-test-secret-that-is-long-enough-1234"
ENV = {
    "LIVEKIT_URL": "wss://example.livekit.cloud",
    "LIVEKIT_API_KEY": "APIkey",
    "LIVEKIT_API_SECRET": SECRET,
    "NOVA_LLM": "ollama/gemma4:26b",
    "NOVA_LEFT_PARTICIPANT": "dater-left",
    "NOVA_RIGHT_PARTICIPANT": "dater-right",
}
ROOM = "nova-two-person-test"


def test_each_seat_joins_one_room_as_the_identity_nova_pins_it_to():
    made = joins(Settings.from_env(ENV), ROOM, minutes=30)
    verifier = api.TokenVerifier("APIkey", SECRET)
    claims = {join.seat: verifier.verify(join.token) for join in made}
    assert claims[Seat.LEFT].identity == "dater-left"
    assert claims[Seat.RIGHT].identity == "dater-right"
    for seat, claim in claims.items():
        assert claim.video.room == ROOM and claim.video.room_join
        assert not claim.video.room_admin and not claim.video.room_create
        assert claim.name.endswith(f"({seat} seat)")
        assert claim.attributes == {"role": "dater"}, "never the moderator's role"


def test_tokens_expire_after_the_minutes_asked_for():
    for join in joins(Settings.from_env(ENV), ROOM, minutes=30):
        payload = join.token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        assert claims["exp"] - claims["nbf"] == 30 * 60


def test_no_tokens_without_both_identities_or_for_no_time():
    alone = {k: v for k, v in ENV.items() if "PARTICIPANT" not in k}
    with pytest.raises(ConfigError, match="NOVA_LEFT_PARTICIPANT"):
        joins(Settings.from_env(alone), ROOM)
    with pytest.raises(ConfigError):
        joins(Settings.from_env(ENV), ROOM, minutes=0)


def test_what_is_printed_never_holds_the_api_secret_and_a_repr_never_holds_a_token():
    settings = Settings.from_env(ENV)
    made = joins(settings, ROOM)
    printed = render(settings, ROOM, made)
    assert SECRET not in printed
    assert "dater-left" in printed and "dater-right" in printed and ROOM in printed
    assert all(join.token in printed for join in made)
    assert all(join.token not in repr(join) for join in made)
