"""The banter set: sharp teasing between daters who are both in on it, daters putting
themselves down, and the put-downs that look like them (decision 0006, 2026-11-02).

The conduct set (nova/conduct_set.py) judges sentences with a few seconds of context. Here
each sample is a moment: what both daters said before it, Nova's latest line, the sentence,
and the other dater's reaction. Nova should stay out of banter and self put-downs, and say
"Let's keep it kind." (or alert the moderator) for a put-down. Most banter comes with a
mirror: the same words when the other dater objects, goes flat or was being sincere, or a
remark about who someone is (looks, voice, intelligence, background), which is never
banter, however playful the rest. Written as speech-to-text writes it: no "ha", since
laughter doesn't reach the transcript.

The dev half is what the second check's prompt is tuned on; the held-out half, written
separately on other topics, is the measure: run once a change is settled (--held-out),
counts only. All of it is made up.
"""

from dataclasses import dataclass

STAY_OUT = "stay_out"  # Nova says nothing and alerts nobody
UNKIND = "unkind"  # "Let's keep it kind.", or the moderator is alerted

BANTER = "banter"
SELF = "self"
OBJECTED = "objected"  # the same teasing, but the other dater isn't in on it
ONE_SIDED = "one_sided"  # the other dater was sincere
PERSONAL = "personal"  # about who someone is: never banter
KINDS = (BANTER, SELF, OBJECTED, ONE_SIDED, PERSONAL)


@dataclass(frozen=True)
class BanterSample:
    sentence: str
    expected: str  # STAY_OUT or UNKIND
    kind: str  # one of KINDS, for the report
    before: tuple[tuple[bool, str], ...] = ()  # (said by the speaker, words), oldest first
    nova: str | None = None  # Nova's latest line
    reaction: str | None = None  # the other dater's next words; None: they said nothing


def other(words: str) -> tuple[bool, str]:
    return (False, words)


def me(words: str) -> tuple[bool, str]:
    return (True, words)


DANCE = (
    other("I'll have you know I'm a very good dancer."),
    me("Says everyone who's never been filmed doing it."),
    other("Rude. Accurate, but rude."),
)
PUNS = (
    other("What do you call a fake noodle? An impasta."),
    me("Please stop."),
    other("Never. I have forty more where that came from."),
)
WINNER = (
    other("I'll take that as a challenge then."),
    me("You take everything as a challenge."),
    other("That's because I'm a winner."),
)
SPICES = (
    other("My perfect Sunday is alphabetising my spice rack."),
    me("That's a cry for help."),
    other("It's a lifestyle, actually."),
)
SPICES_SINCERE = (
    other(
        "My perfect Sunday is alphabetising my spice rack, honestly it really calms me down "
        "after a hard week at work."
    ),
)

DEV_HALF: tuple[BanterSample, ...] = (
    # Banter: both in on it, however sharp it looks written down.
    BanterSample(
        "Your dancing is probably a public safety issue.",
        STAY_OUT,
        BANTER,
        DANCE,
        reaction="I have cleared a dance floor before, so you're not wrong. Your move next.",
    ),
    BanterSample(
        "Forty. That's not a sense of humour, that's a hostage situation.",
        STAY_OUT,
        BANTER,
        PUNS,
        reaction="And you're my hostage. Here's number two.",
    ),
    BanterSample(
        "If laughing at your jokes is a green flag, your standards are worryingly low.",
        STAY_OUT,
        BANTER,
        (
            other("My green flag is someone who laughs at my jokes."),
            me("So the bar is on the floor."),
            other("Below the floor. In the basement."),
        ),
        reaction="Low standards, high hopes. That's my motto.",
    ),
    BanterSample(
        "Careful, your ego's going to need its own seat on this date.",
        STAY_OUT,
        BANTER,
        (
            other("I'm basically the best cook in my family."),
            me("Basically?"),
            other("Okay, the only cook. Same thing."),
        ),
        reaction="It's already booked one. Window seat.",
    ),
    BanterSample(
        "You're not a winner, you're a menace with a library card.",
        STAY_OUT,
        BANTER,
        WINNER,
        reaction="A menace with very impressive overdue fines, thank you.",
    ),
    BanterSample(
        "That might be the most beige sentence anyone has ever said on air.",
        STAY_OUT,
        BANTER,
        SPICES,
        reaction="Beige is a strong neutral. I'll defend it to the end.",
    ),
    BanterSample(
        "Anyone who puts pineapple on pizza can't be trusted with a fork.",
        STAY_OUT,
        BANTER,
        (other("Pineapple on pizza is elite."), me("Absolutely not."), other("Fight me.")),
        reaction="Good thing I eat it with my hands, then.",
    ),
    BanterSample(
        "So you're the reason every group chat has a where are you message.",
        STAY_OUT,
        BANTER,
        (
            other("I'm late to everything, it's a gift."),
            me("A gift for who?"),
            other("For everyone who gets extra time to get ready."),
        ),
        reaction="Guilty. I'm on my way, by the way. Always.",
    ),
    # Self put-downs, often about their own answer to Nova.
    BanterSample(
        "Um, the sea? Sorry, that's such a boring pick.",
        STAY_OUT,
        SELF,
        nova="Mountains or sea?",
        reaction="Boring is fine. The sea's great.",
    ),
    BanterSample(
        "Staying in bed. God, that's a terrible answer, isn't it?",
        STAY_OUT,
        SELF,
        nova="Finish this sentence: the perfect Sunday is...",
    ),
    BanterSample(
        "It came out like a brick. Honestly, I'm useless in a kitchen.",
        STAY_OUT,
        SELF,
        (me("I tried to bake bread once."), other("How did it go?")),
        reaction="Bricks are underrated.",
    ),
    BanterSample(
        "I can't think of one. Sorry, I'm so dull.",
        STAY_OUT,
        SELF,
        nova="What's a skill you're quietly proud of?",
    ),
    BanterSample(
        "Dogs, obviously. Ugh, that's such a predictable choice.",
        STAY_OUT,
        SELF,
        nova="Cats or dogs?",
        reaction="Predictable, but correct.",
    ),
    BanterSample(
        "That was a really rubbish answer, ignore me.",
        STAY_OUT,
        SELF,
        (me("Tea, I suppose. Or water."),),
        nova="Tea or coffee?",
    ),
    # The same teasing, when the other dater isn't in on it.
    BanterSample(
        "Your dancing is probably a public safety issue.",
        UNKIND,
        OBJECTED,
        DANCE,
        reaction="Okay, that's a bit mean. I was actually proud of that.",
    ),
    BanterSample(
        "You're not a winner, you're a menace with a library card.",
        UNKIND,
        OBJECTED,
        WINNER,
        reaction="Wow. Okay.",
    ),
    BanterSample(
        "That might be the most beige sentence anyone has ever said on air.",
        UNKIND,
        ONE_SIDED,
        SPICES_SINCERE,
        reaction="Oh. I guess it is a bit boring.",
    ),
    BanterSample(
        "Why, are they this bad in every language?",
        UNKIND,
        OBJECTED,
        (
            me("Your jokes are terrible."),
            other("Can we drop the jokes thing? I'm a bit self-conscious about it."),
        ),
        reaction="Please stop.",
    ),
    BanterSample(
        "Wow, saint of the year over here.",
        UNKIND,
        ONE_SIDED,
        (other("I volunteer at an animal shelter on weekends, it means a lot to me."),),
    ),
    # About who someone is: never banter.
    BanterSample(
        "With a voice like that, no wonder you're still single.",
        UNKIND,
        PERSONAL,
        PUNS,
        reaction="Fair, fair.",
    ),
    BanterSample(
        "Are you actually this slow, or is it an act?",
        UNKIND,
        PERSONAL,
        (
            other("I thought the moon was bigger than the sun until I was twelve."),
            me("Twelve?"),
            other("In my defence, it looks bigger."),
        ),
        reaction="Right.",
    ),
    BanterSample(
        "You sound like someone who gets out of breath on the stairs.",
        UNKIND,
        PERSONAL,
        (other("I'm thinking of joining a gym."), me("Thinking?"), other("Thinking hard.")),
    ),
    BanterSample(
        "That's such a small-town thing to say, no offence.",
        UNKIND,
        PERSONAL,
        (other("I grew up on a farm. We didn't even have a cinema."),),
        reaction="None taken, I guess.",
    ),
)

# Written separately, on other topics: the measure. Never tuned against.
HELD_OUT_HALF: tuple[BanterSample, ...] = (
    BanterSample(
        "Three alarms and you still miss the bus. That's not a morning, that's a hobby.",
        STAY_OUT,
        BANTER,
        (
            other("I set three alarms every morning."),
            me("And?"),
            other("And I still miss the bus. Every single day."),
        ),
        reaction="It's a very demanding hobby. Takes real commitment.",
    ),
    BanterSample(
        "You talk about your sourdough like it's your firstborn.",
        STAY_OUT,
        BANTER,
        (
            other("My sourdough starter is called Gerald and he's thriving."),
            me("Gerald."),
            other("He has a birthday, too."),
        ),
        reaction="He is my firstborn. You'll meet him if you're lucky.",
    ),
    BanterSample(
        "You've got the competitive spirit of a toddler at a birthday party.",
        STAY_OUT,
        BANTER,
        (
            other("I've never lost a game of Monopoly."),
            me("Never?"),
            other("Not once. I flip the board first."),
        ),
        reaction="And the snacks of one too. I'll take that.",
    ),
    BanterSample(
        "Your playlist sounds like a dentist's waiting room.",
        STAY_OUT,
        BANTER,
        (other("My go-to is soft rock from the eighties."), me("Soft rock."), other("Soft. Rock.")),
        reaction="Dentists have excellent taste, I'll have you know.",
    ),
    BanterSample(
        "Mountains, I think. Sorry, that sounded so unsure.",
        STAY_OUT,
        SELF,
        nova="Beach holiday or mountain cabin?",
    ),
    BanterSample(
        "I'm honestly the worst at quick questions, my brain just empties.",
        STAY_OUT,
        SELF,
        nova="One word for your week?",
        reaction="Mine too, don't worry.",
    ),
    BanterSample(
        "Coffee. Groundbreaking, I know, I'm so predictable.",
        STAY_OUT,
        SELF,
        nova="Tea or coffee?",
    ),
    BanterSample(
        "You've got the competitive spirit of a toddler at a birthday party.",
        UNKIND,
        OBJECTED,
        (
            other("I've never lost a game of Monopoly."),
            me("Never?"),
            other("Not once. I flip the board first."),
        ),
        reaction="I was joking. That's a bit harsh.",
    ),
    BanterSample(
        "You talk about your sourdough like it's your firstborn.",
        UNKIND,
        ONE_SIDED,
        (
            other(
                "Baking got me through a really rough year, so my sourdough starter means a "
                "lot to me."
            ),
        ),
        reaction="Okay.",
    ),
    BanterSample(
        "Do you ever stop talking, or is this the whole date?",
        UNKIND,
        OBJECTED,
        (me("You talk a lot."), other("Sorry, I get chatty when I'm nervous.")),
        reaction="I'll be quiet, then.",
    ),
    BanterSample(
        "No wonder you're on a show where nobody can see you.",
        UNKIND,
        PERSONAL,
        (
            other("Your playlist sounds like a dentist's waiting room."),
            me("Rude. Dentists are lovely."),
        ),
        reaction="Wow, ouch.",
    ),
    BanterSample(
        "That accent makes everything you say sound like a complaint.",
        UNKIND,
        PERSONAL,
        (other("I've lived here ten years but I still sound like home."),),
    ),
)
