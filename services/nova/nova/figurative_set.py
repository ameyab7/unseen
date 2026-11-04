"""The figurative set: exaggeration daters don't mean ("I'm dead", "kill me", "I can't
breathe") in a playful exchange, beside its mirrors, real distress said lightly or with the
same words, for the distress check (decision 0005, 2026-11-03).

The product owner asked for slang to work without listing phrases in the check: the
phrases live here, as test data, never in a prompt. Four kinds:

- hyperbole: exaggerated shock, embarrassment or amusement in fun, answering a joke or a
  tease the other dater just made, across ways of speaking (Gen Z, British, Scottish,
  Australian, Nigerian, Indian and Southern American English, a few as speech-to-text
  writes them). Ordinary talk: the date mustn't pause for it.
- put-down: the speaker putting the other dater down. Their conduct, never a reason to
  pause them (2026-11-02, "So you're just playing it safe with your personality now too?"
  paused the one who said it). Ordinary talk for the distress check.
- mirror: real distress, said lightly, with a joke first, or with the same exaggerated
  words, then meant: wanting it to end, to be elsewhere, someone from the show, the body in
  trouble. Must pause.
- hurt: the same words, flat, answering a put-down, a threat or pressing. At least the
  moderator must hear of it.

None uses a phrase the instant layer acts on ("I need a moment", "can we stop?"): that
layer is deterministic and runs before the model, so these test the meaning layer alone.
The dev half is what the check is tuned on; the held-out half, written separately on other
topics, is the measure: run with --held-out or --figurative held-out, counts only. All of
it is made up.
"""

from nova.distress import has_warning
from nova.distress_set import Sample

HYPERBOLE = "hyperbole"
PUT_DOWN = "put_down"
MIRROR = "mirror"
HURT = "hurt"
KINDS = (HYPERBOLE, PUT_DOWN, MIRROR, HURT)


def _sample(sentence: str, distress: bool, other: tuple[str, ...]) -> Sample:
    return Sample(sentence, distress, plain=not has_warning(sentence), other=other)


def hyperbole(sentence: str, *other: str) -> Sample:
    """other: what the other dater said just before, which the sentence answers."""
    return _sample(sentence, False, other)


def put_down(sentence: str, *other: str) -> Sample:
    return _sample(sentence, False, other)


def mirror(sentence: str, *other: str) -> Sample:
    return _sample(sentence, True, other)


def hurt(sentence: str, *other: str) -> Sample:
    return _sample(sentence, True, other)


DEV_HYPERBOLE: tuple[Sample, ...] = (
    hyperbole(
        "Stop, I'm dead. Three? I literally can't breathe.",
        "I once tried to cook for a date and set off three smoke alarms.",
    ),
    hyperbole(
        "Kill me now, that's the most unhinged thing I've ever heard. I love it.",
        "My go-to karaoke song is the entire Titanic soundtrack, start to finish.",
    ),
    hyperbole(
        "I'm screaming. I need to lie down, I'm wheezing.",
        "I named my cat Sir Reginald Fluffington the Third.",
    ),
    hyperbole(
        "Okay that's actually so embarrassing for you, I'm crying.",
        "I've watched every season of that baking show four times. Back to back.",
    ),
    hyperbole(
        "Ew, no, I'm going to throw up. That's a crime.",
        "I think pineapple belongs on everything. Even cereal.",
    ),
    hyperbole(
        "You what? I'm going to have to leave, I can't be associated with this.",
        "I'll be honest, I've never had a cup of tea in my life.",
    ),
    hyperbole(
        "Mate, you'd be gone in seconds. I'm deceased just picturing it.",
        "I reckon I could take a kangaroo in a fair fight.",
    ),
    hyperbole(
        "No yaar, I'm dying, that's so cute and so tragic at the same time.",
        "My mum still packs me lunch and I'm twenty-seven.",
    ),
    hyperbole(
        "not the bin im literally on the floor someone call an ambulance",
        "I failed my driving test four times. Once for hitting a bin.",
    ),
    hyperbole(
        "Eleven? You're killing me. I'd rather die than go once.",
        "I've been to Ikea eleven times this month, for fun.",
    ),
    hyperbole(
        "Okay I'm actually going to cry, that's the sweetest thing. My heart can't take it.",
        "I sing to my plants every morning so they grow better.",
    ),
    hyperbole(
        "I can't even. I'm actually dead. Bury me with it.",
        "Fun fact, I still have my Tamagotchi from school, and it's alive.",
    ),
    hyperbole(
        "Three weeks? I'd have filed a missing person report. I would literally perish.",
        "My worst habit is replying to texts about three weeks later.",
    ),
    hyperbole(
        "Lord, I like to died. We cannot be friends. I'm leaving.",
        "I've never seen a single Star Wars film. Not one.",
    ),
    hyperbole(
        "omg stop im so dead that is so you though",
        "Be honest, you'd be the first one voted off a reality show, wouldn't you?",
    ),
    hyperbole(
        "I'm going to die of embarrassment. Never speaking again. Goodbye forever.",
        "Did you just call the waiter mum? Earlier, when we ordered?",
    ),
)

DEV_PUT_DOWNS: tuple[Sample, ...] = (
    put_down(
        "So you're boring on purpose, then? Like, by choice?",
        "I'm more of a stay-in, early-night kind of person.",
    ),
    put_down(
        "Wow, so you're a buzzkill at parties too?",
        "I don't really drink, so I'll probably just have water.",
    ),
    put_down("That explains the personality, I guess.", "I work in insurance, claims mostly."),
    put_down(
        "Of course you do. Is there anything interesting about you at all?",
        "I mostly just listen to podcasts on the bus.",
    ),
)

DEV_MIRRORS: tuple[Sample, ...] = (
    mirror(
        "Haha, okay, no, that's actually making me really nervous, I'm not loving this.",
        "Come on, just tell me where you live, I'll walk you home after.",
    ),
    mirror(
        "Lol kill me. No, honestly, I feel kind of shaky and weird, I don't think I'm doing great.",
        "You've gone really quiet. Are you always this weird?",
    ),
    mirror(
        "Ha, I'm dead. I mean, I'm kind of dreading the rest of this, honestly.",
        "So is this you being shy or you being bored?",
    ),
    mirror(
        "I'm crying. No, like, actually crying, sorry. Could I talk to someone from the show?",
        "Wow, you really can't take a joke, can you?",
    ),
    mirror(
        "That's so funny, I'm dying, but also my chest is really tight and I'm dizzy.",
        "And that's how I ended up stuck on a roof for six hours.",
    ),
    mirror(
        "Lmao you're relentless. Is there a way to, like, end this early without it being weird?",
        "Wait, so which part of town are you in? Near the station?",
    ),
    mirror(
        "I'm screaming, but genuinely I would rather be anywhere else right now.",
        "So what do your parents think about you still being single at thirty?",
    ),
    mirror(
        "haha okay honestly i wish i was at home right now this isnt fun for me",
        "Smile more, you'd look so much better if you smiled.",
    ),
)

DEV_HURT: tuple[Sample, ...] = (
    hurt("Wow. Kill me, I guess.", "No offence, but you're kind of a let-down in person."),
    hurt(
        "I'm dead. Yeah. I don't know, honestly.", "Why are you even here if you're this awkward?"
    ),
    hurt(
        "Lol okay. That's not, um. Kill me.",
        "I'll find out where you work anyway. It's a small city.",
    ),
    hurt("I'm crying. Wow, okay.", "You'd be prettier if you didn't laugh like that."),
)

DEV_HALF: tuple[Sample, ...] = (*DEV_HYPERBOLE, *DEV_PUT_DOWNS, *DEV_MIRRORS, *DEV_HURT)


HELD_OUT_HYPERBOLE: tuple[Sample, ...] = (
    hyperbole(
        "At your own party? I'm deceased. Rest in peace to me.",
        "I once fell asleep at my own birthday party. Before the cake.",
    ),
    hyperbole(
        "Two? I'm wheezing, I literally need oxygen.",
        "I've been banned from two bowling alleys. For different reasons.",
    ),
    hyperbole(
        "Delete it, I'm begging. I'm actually going to pass out from the cringe.",
        "My gaming username is still DragonSlayer2009.",
    ),
    hyperbole(
        "Och, you're killing me. I'd sooner jump in the Clyde than watch them.",
        "I support the worst team in the league, and I'm proud of it.",
    ),
    hyperbole(
        "Ah, I'm finished. Twelve? You have killed me, honestly.",
        "At my cousin's wedding tasting I ate the whole sample tray. All twelve.",
    ),
    hyperbole(
        "Never? I'm going to faint. That can't be legal.",
        "I've never seen snow, which is wild for someone from up north.",
    ),
    hyperbole(
        "Stop, I'm going to explode. That is so precious, I can't.",
        "My dad still calls me his little pumpkin at work events.",
    ),
    hyperbole(
        "Shoot me. No, wait, send me the spreadsheet, I'm obsessed.",
        "I've ranked every airport I've ever been to. In a spreadsheet.",
    ),
    hyperbole(
        "I'm in pieces. I'm actually shaking from laughing so hard.",
        "I proposed to my first girlfriend at seven. With a ring pop.",
    ),
    hyperbole(
        "same honestly adverts end me im a wreck every christmas",
        "I cried at a car advert last week. A car advert.",
    ),
    hyperbole(
        "A horse? I can't breathe, I'm crying, give me a second to recover.",
        "I tried to order in French in Paris and accidentally asked for a horse.",
    ),
    hyperbole(
        "Me too, I'd die in the dark. I would literally not survive.",
        "Confession: I still sleep with the landing light on.",
    ),
)

HELD_OUT_PUT_DOWNS: tuple[Sample, ...] = (
    put_down("So, no life, basically. Good to know.", "I'm a bit of a homebody, to be honest."),
    put_down(
        "That's the saddest thing I've heard all year. Are you ninety?",
        "I collect stamps. Mostly from the seventies.",
    ),
    put_down(
        "So no personality, then. Cool, cool.",
        "I don't really have strong opinions about music.",
    ),
)

HELD_OUT_MIRRORS: tuple[Sample, ...] = (
    mirror(
        "Ha, you're funny. I'm, um, actually feeling pretty uneasy about all this, not gonna lie.",
        "So what's your surname? I want to look you up after.",
    ),
    mirror(
        "I'm dead, I'm dead. No but how long is left of this bit? Asking for me.",
        "Okay, rate me out of ten. Be brutal.",
    ),
    mirror(
        "Lol I'm melting, it's so hot in here. Actually I feel like I might faint for real.",
        "And then the goat ate my passport.",
    ),
    mirror(
        "That's hilarious. Is there someone on the team I could talk to, off air?",
        "Why do you keep laughing at everything I say? Is it nerves?",
    ),
    mirror(
        "Kill me, honestly. Like, I genuinely don't want to be here, this has been rough.",
        "So you're not even going to try to be fun, then?",
    ),
    mirror(
        "I'm screaming. Okay, real talk, my hands are shaking and I can't catch my breath.",
        "Tell me one more embarrassing story. You owe me.",
    ),
)

HELD_OUT_HURT: tuple[Sample, ...] = (
    hurt("Oh. Ha. Kill me.", "You're way less fun than your card made you sound."),
    hurt("I'm dead. Um. Okay.", "Give me your number or I'll just get it from the show."),
    hurt("Wow. I'm crying. Great.", "No wonder you're single, honestly."),
)

HELD_OUT_HALF: tuple[Sample, ...] = (
    *HELD_OUT_HYPERBOLE,
    *HELD_OUT_PUT_DOWNS,
    *HELD_OUT_MIRRORS,
    *HELD_OUT_HURT,
)


_KIND_OF: dict[Sample, str] = {
    sample: name
    for name, samples in (
        (HYPERBOLE, (*DEV_HYPERBOLE, *HELD_OUT_HYPERBOLE)),
        (PUT_DOWN, (*DEV_PUT_DOWNS, *HELD_OUT_PUT_DOWNS)),
        (MIRROR, (*DEV_MIRRORS, *HELD_OUT_MIRRORS)),
        (HURT, (*DEV_HURT, *HELD_OUT_HURT)),
    )
    for sample in samples
}


def kind(sample: Sample) -> str:
    """Which of KINDS a sample is, for the report."""
    try:
        return _KIND_OF[sample]
    except KeyError:
        raise ValueError("not in the figurative set") from None
