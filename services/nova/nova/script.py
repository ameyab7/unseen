"""Nova's approved lines: spoken exactly as written, never generated.

Safety-critical moments (the result, a dater leaving, a moderator ending the date) use
these, never the language model. Each line names its source; a new line needs the
product owner's sign-off before it goes on air.
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

# Not in the brand book; signed off by the product owner on 2026-10-13.
NEED_A_MOMENT = "Let's take a short breather. Room, stay with us."
WELCOME_BACK = "And we're back."  # only when the dater who paused actually returns
BEAR_WITH_US = "Bear with us a little longer, room."  # once, if they're not back in time
MODERATOR_END = "We're ending this date here. Thanks for listening, everyone."


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


# --- Fallback lines ---------------------------------------------------------------------
# Used when the model is too slow, fails, or writes a line a guardrail rejects. Built only
# from structured card fields (first name, age), never from free text a dater or listener
# wrote, so an injected card or question can't reach the room through a fallback.
# PENDING SIGN-OFF: none of these are in the brand book or the prototype.

LIGHTNING_FALLBACKS = (
    "Quick one: mountains or sea?",
    "Quick one: early bird or night owl?",
    "Last quick one: the best thing you ate this week?",
)
DECLINE_QUESTION = "Let's leave that one for another night. Back to you two."
ROOM_TALK_FALLBACK = "Thanks for keeping the chat kind tonight, room."
REPLY_FALLBACK = "I'm the AI host, so I'll stay out of that one. Back to you two."


def doors_fallback(pair: Pair) -> str:
    """The brand book's opening, without the card details."""
    return f"Good evening, room. On my left, {pair.left.label}. On my right, {pair.right.label}."


def first_question_fallback(card: BlindCard) -> str:
    return f"{card.first_name}, let's start with your card. Tell us the story."


def last_word_fallback(card: BlindCard) -> str:
    return f"{card.first_name}, the last word is yours."


def outro_fallback(pair: Pair) -> str:
    return f"{pair.left.first_name} and {pair.right.first_name}, thank you both."


def next_up_fallback(pair: Pair) -> str:
    """Prototype: "Up next: Sam, 29 and someone new"."""
    return f"Up next, {pair.left.label} and {pair.right.label}."


def silence_fallbacks(pair: Pair) -> tuple[str, str, str]:
    """Open prompts to both daters, taken in turn."""
    both = f"{pair.left.first_name}, {pair.right.first_name}"
    return (
        f"{both}, here's one for you both: what's the best thing that happened to you this week?",
        f"{both}, one for you both: what's a small thing that reliably makes your day?",
        f"{both}, here's another: what would you do with a free day tomorrow?",
    )


def from_the_room_verbatim(question: str) -> str:
    """A moderator-approved question read as approved. Still checked by the guardrails."""
    return f"A question from the room. {question}"


def from_the_room_fallback(pair: Pair) -> str:
    return (
        f"Room, keep the questions coming. "
        f"{pair.left.first_name}, {pair.right.first_name}, back to you."
    )
