"""Nova's approved lines: spoken exactly as written, never generated.

Safety-critical moments (the result, a dater leaving, a moderator ending the date) use
these, never the language model. Each line names its source; a new line needs the
product owner's sign-off before it goes on air.
"""

from nova.cards import BlindCard, Pair, Prompt
from nova.decisions import Outcome
from nova.text import plain

# Brand book, "Nova on air".
RESULT_MUTUAL = "It's mutual. What happens next is theirs."
KEEP_STREETS_OFF_AIR = "Let's keep street names off air. The story's better without them anyway."
KEEP_IT_KIND = "Let's keep it kind."
SHOW_PAUSE = "We're taking a short pause."  # if an unkind remark happens again
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
# When a dater tells Nova they want to stop or sounds distressed: never a deflection.
DISTRESS_PAUSE = "Of course. Let's take a breather."


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
# Not in the brand book or the prototype; signed off by the product owner on 2026-10-13.

LIGHTNING_FALLBACKS = (
    "Quick one: mountains or sea?",
    "Quick one: early bird or night owl?",
)
LIGHTNING_LAST_FALLBACKS = (  # only ever for the last question of the round
    "Last quick one: the best thing you ate this week?",
)
ROOM_TALK_FALLBACK = "Thanks for keeping the chat kind tonight, room."
# Never for a dater who clearly wants to stop: they never get a reply cue at all.
REPLY_FALLBACK = "I'm the AI host, so I'll stay out of that one. Back to you two."
# Instead of the line above when what the dater said might be distress ("help").
TAKE_YOUR_TIME = "Take all the time you need."


# Proposed, not signed off (docs/sign-off/2026-10-card-fallbacks.md): when the model fails,
# the doors and Next up fallbacks quote the daters' own cards, in their words, once a person
# at casting has approved both cards and the line passes the guardrails. Off until signed off.
CARD_FALLBACKS_ON = False


def card_says(prompt: Prompt) -> str:
    """The card as the dater wrote it: the app's fixed question with its first letter
    lowercased (not the pronoun I: "I claim I can"), then the answer as written, ending a
    sentence. No grammar is rewritten; curly quotes are straightened and line breaks made
    spaces, as typed on a phone."""
    question = plain(prompt.question)
    if question.split(" ", 1)[0].split("'", 1)[0] != "I":
        question = question[:1].lower() + question[1:]
    answer = plain(prompt.answer)
    return f"{question} {answer}" + ("" if answer[-1:] in ".?!" else ".")


def doors_from_cards(pair: Pair) -> str:
    left, right = pair.left, pair.right
    return (
        f"Good evening, room. On my left, {left.label}, whose card says: "
        f"{card_says(left.prompts[0])} On my right, {right.label}, whose card says: "
        f"{card_says(right.prompts[0])}"
    )


def next_up_from_cards(pair: Pair) -> str:
    left, right = pair.left, pair.right
    return (
        f"Up next, {left.label}, whose card says: {card_says(left.prompts[0])} And "
        f"{right.label}, whose card says: {card_says(right.prompts[0])}"
    )


def doors_fallback(pair: Pair) -> str:
    """The brand book's opening, without the card details."""
    return f"Good evening, room. On my left, {pair.left.label}. On my right, {pair.right.label}."


def first_question_fallback(card: BlindCard) -> str:
    return f"{card.first_name}, let's start with your card. Tell us the story."


def last_word_fallback(card: BlindCard) -> str:
    return f"{card.first_name}, the last word is yours."


def outro_fallbacks(pair: Pair) -> tuple[str, str, str, str]:
    """Taken in turn across the night (signed off by the product owner on 2026-10-13). Two
    are two short sentences; the one-sentence rule is for the model's own outros."""
    left, right = pair.left.first_name, pair.right.first_name
    return (
        f"{left} and {right}, thank you both.",
        f"Thank you, {left}. Thank you, {right}.",
        f"That's our date with {left} and {right}. Thank you both.",
        f"{left}, {right}, thank you for being here tonight.",
    )


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


# Proposed, not signed off (docs/sign-off/2026-10-audience-questions.md): when the model
# fails on a question a moderator approved, Nova reads it framed, if the framed line passes
# the guardrails for the moment. The one fallback built from free text, so it stays off
# until the product owner signs it off.
READ_APPROVED_QUESTION_ON_FAILURE = False


def from_the_room_framed(question: str) -> str:
    return f"From the room: {question}"


def from_the_room_fallback(pair: Pair) -> str:
    return (
        f"Room, keep the questions coming. "
        f"{pair.left.first_name}, {pair.right.first_name}, back to you."
    )
