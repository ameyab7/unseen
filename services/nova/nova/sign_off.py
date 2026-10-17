"""Lines drafted for the product owner's sign-off. None of them is ever said on air: once a
line is approved, it moves to nova/script.py with the date. They live here so a test checks
every draft against the guardrails for its moment, never by eye, and each docs/sign-off/
file lists them as they are here.

Templates take the daters' first names as {left} and {right} (or {name}), built only from
structured card fields, like every fallback. None uses card text or anything a dater or
listener said.
"""

from nova.cards import Pair

# docs/sign-off/2026-10-nova-fallbacks.md: bigger fallback pools. A night that falls back
# often repeats the same lines on every date, with only 3 lightning, 3 silence and 4 outro
# fallbacks.
LIGHTNING = (
    "Quick one: sunrise or sunset?",
    "Quick one: tea or coffee?",
    "Quick one: beach holiday or city break?",
    "Quick one: sweet or savoury?",
    "Quick one: cats or dogs?",
    "Quick one: books or films?",
    "Quick one: a perfect Sunday starts with...",
    "Quick one: the last song you sang along to?",
    "Quick one: this week in one word?",
    "Quick one: dancing or karaoke?",
    "Quick one: a skill you'd love to learn overnight?",
    "Quick one: breakfast in bed or breakfast out?",
)
LIGHTNING_LAST = (  # only ever the last question of the round
    "Last quick one: the best advice you've ever had, in three words?",
    "Last quick one: a small thing you're looking forward to?",
    "Last quick one: your go-to comfort food?",
)
SILENCE = (
    "{left}, {right}, one for you both: what's a smell that takes you straight back to childhood?",
    "{left}, {right}, here's one: what's the best meal you've had this year?",
    "{left}, {right}, one for you both: what would a perfect lazy Sunday look like?",
    "{left}, {right}, here's one: what's a film you could watch again tonight?",
    "{left}, {right}, one for you both: what's something small that made you laugh this week?",
    "{left}, {right}, here's one: what's a place you'd love to see one day?",
    "{left}, {right}, one for you both: what's a hobby you'd pick up if you had the time?",
    "{left}, {right}, here's one: what's the best compliment you've ever been given?",
    "{left}, {right}, one for you both: what's a song that always lifts your mood?",
    "{left}, {right}, here's one: what's a tradition you'd never give up?",
)
OUTRO = (
    "Thank you, {left} and {right}, for a lovely conversation.",
    "{left} and {right}, thank you for sharing your evening with us.",
    "That was {left} and {right}, and we're grateful to you both.",
    "Thank you both, {left} and {right}, it's been a pleasure.",
)

# docs/sign-off/2026-10-one-dater.md: silence while one dater is away (decision 0004).
ONE_DATER_SILENCE = (
    "{name}, here's one just for you: what's the best thing that happened to you this week?",
    "{name}, one for you: what's a small thing that reliably makes your day?",
)

# docs/sign-off/2026-10-one-dater.md: an audience question, and a reply's fallback, while
# one dater is away. Today's name or address both daters, so Nova says nothing instead.
ONE_DATER_FROM_THE_ROOM = ("Room, keep the questions coming. {name}, back to you.",)
ONE_DATER_REPLY = ("Good question. I'll hand that one back to you, {name}.",)

# docs/sign-off/2026-10-room-talk.md: more talk to the room for a hold (decision 0004).
ROOM_TALK = (
    "Room, thanks for staying with us. The chat has been lovely tonight.",
    "While we wait, room, what would you ask next? Tell us in the chat.",
    "Stay with us, room. Good conversations are worth a short wait.",
    "Room, a quick thank-you for keeping the chat kind and curious.",
    "We will be right back with you, room. Thanks for your patience.",
    "Room, your questions keep this show going. Thank you for every one of them.",
)

# docs/sign-off/2026-10-conduct.md: a personal detail that isn't a street (decision 0006).
CONDUCT_DETAIL = (
    "Let's keep workplaces off air. You're more interesting than your job anyway.",
    "Let's keep numbers and handles off air. Those are for later, not for the room.",
    "Let's keep the personal details off air. The story's better without them.",
)


# docs/sign-off/2026-10-reply-fallback.md: a neutral reply fallback. Today's ("I'm the AI
# host, so I'll stay out of that one.") sounds as if the dater asked something off limits
# when the model only timed out.
REPLY_FALLBACK = (
    "Good question. I'll hand that one back to you two.",
    "I'll let you two take that one. Over to you.",
    "That one's yours to answer, not mine. Back to you both.",
)


def both(templates: tuple[str, ...], pair: Pair) -> tuple[str, ...]:
    """The templates with this pair's first names in."""
    left, right = pair.left.first_name, pair.right.first_name
    return tuple(template.format(left=left, right=right) for template in templates)


def one(templates: tuple[str, ...], name: str) -> tuple[str, ...]:
    return tuple(template.format(name=name) for template in templates)
