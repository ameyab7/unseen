"""Nova's approved lines: spoken exactly as written, never generated.

Safety-critical moments (the result, a dater leaving, a moderator ending the date) use
these, never the language model. Each line names its source. Lines marked ASSUMPTION are
not in the brand book or the prototype and need sign-off before a real show.
"""

from nova.cards import BlindCard, Pair
from nova.decisions import Outcome

# Brand book, "Nova on air".
RESULT_MUTUAL = "It's mutual. What happens next is theirs."
RESULT_NOT_MUTUAL = "They're keeping it unseen tonight. Two brave people and a good conversation."
INVITATION = "If that sounded like your kind of night, there are seats this week."

# Prototype, live room.
LIGHTNING_UNLOCKED = "The lightning round is unlocked. Three quick ones, coming up."
POLL_QUESTION = "Second date?"

# ASSUMPTION: the brand book has no line for these.
NEED_A_MOMENT = "Let's take a short breather. Room, stay with us."
WELCOME_BACK = "And we're back."
MODERATOR_END = "We're ending this date here. Thank you both, and thank you, room."


def time_line(pair: Pair) -> str:
    """Prototype, live room: Nova as the decision window opens."""
    left, right = pair.left.first_name, pair.right.first_name
    return f"That's time. {left} and {right} are deciding, privately."


def dater_left(leaver: BlindCard, other: BlindCard) -> str:
    """Brand book, "Someone leaves". No reason, no blame (prototype: "Nobody hears a
    reason unless you choose to report")."""
    return (
        f"{leaver.first_name}'s had to step away, and that's always allowed. "
        f"{other.first_name}, thank you."
    )


def result_line(outcome: Outcome) -> str:
    """The same words for every not-a-match, so nobody learns who chose what."""
    return RESULT_MUTUAL if outcome is Outcome.MUTUAL else RESULT_NOT_MUTUAL
