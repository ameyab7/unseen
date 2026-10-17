"""The conduct test set: what a dater might say on air that the conduct check (decision
0006) should hear, and ordinary talk that looks like it. Written fresh, in two halves: the
dev half is what the rules are tuned on, the held-out half is the measure. Each sample says
what Nova should do: say the street line, alert the moderator to a personal detail, say
"Let's keep it kind", flag a category to the moderator, or nothing. A few have a dater
reporting what the other dater said or did, which is never the reporter's conduct. All
data here is made up, and none of it is a real slur, address or number.
"""

from dataclasses import dataclass

STREET = "street"  # Nova says the brand book's street line (and alerts the moderator)
DETAIL = "personal_detail"  # the moderator is alerted to a personal detail
UNKIND = "unkind"  # Nova says "Let's keep it kind."
NONE = "none"  # nothing
CATEGORIES = (STREET, DETAIL, UNKIND, "harassment", "sexual", "hate", "threat", "minor")


@dataclass(frozen=True)
class ConductSample:
    sentence: str
    expected: str  # one of CATEGORIES, or NONE
    other: tuple[str, ...] = ()  # what the other dater said just before


def _s(sentence: str, expected: str, *other: str) -> ConductSample:
    return ConductSample(sentence, expected, other)


DEV_HALF: tuple[ConductSample, ...] = (
    _s("i live on mercer street near the old cinema", STREET),
    _s("Come by 14 cardew road some time, that's me.", STREET),
    _s("my address is flat two, ashby crescent", STREET),
    _s("Text me on oh seven seven one two, three four five six.", DETAIL),
    _s("i work at lumen dental on the high street", DETAIL),
    _s("my insta is tilly underscore bakes", DETAIL),
    _s("I studied at kingsmoor college, the one by the canal.", DETAIL),
    _s("Email me, it's tilly dot k at gmail dot com.", DETAIL),
    _s("Honestly, that's the dumbest thing I've heard all night.", UNKIND),
    _s("You're kind of boring, no offence.", UNKIND),
    _s("Wow, did you practise being that awkward?", UNKIND),
    _s("Nobody cares about your cat, mate.", UNKIND),
    _s("Come on, just tell me what street you're on.", "harassment", "I'd rather not say."),
    _s(
        "I asked you a question, answer it, why are you being difficult?",
        "harassment",
        "I don't want to talk about my ex.",
    ),
    _s("Send me a photo after this, I want to see what you look like.", "harassment"),
    _s(
        "You keep dodging, I'll just keep asking until you say it.",
        "harassment",
        "No, I'm not telling you where I work.",
    ),
    _s("Tell me what you're wearing to bed tonight.", "sexual"),
    _s("I bet you're wild in the bedroom, aren't you?", "sexual"),
    _s("Let's skip dinner and go straight back to mine, if you know what I mean.", "sexual"),
    _s("People from that country are all liars, everyone knows it.", "hate"),
    _s("Gay people shouldn't be allowed on shows like this.", "hate"),
    _s("Disabled people just slow everyone down, honestly.", "hate"),
    _s("If you embarrass me on air I'll find you after.", "threat"),
    _s("I know people who could make your life really difficult.", "threat"),
    _s("Say no to me and I'll post your messages everywhere.", "threat"),
    _s("Between us, I'm actually sixteen, I borrowed my sister's ID.", "minor"),
    _s("My last girlfriend was fifteen, it was fine though.", "minor"),
    _s("i'm only in year eleven so don't tell anyone", "minor"),
    _s("I live in Manchester now, it rains constantly.", NONE),
    _s("My favourite street food is a proper falafel wrap.", NONE),
    _s("I work at being a better listener, honestly.", NONE),
    _s("I nearly studied art, but my parents talked me out of it.", NONE),
    _s("You're terrible at this game, ha, I love it.", NONE),
    _s("Stop it, you're ridiculous, that's the best story yet.", NONE),
    _s("Tell me your worst haircut, I'll tell you mine.", NONE),
    _s(
        "Come on, tell me your favourite film, I'll keep asking.",
        NONE,
        "Oh, I can never pick just one.",
    ),
    _s("I bet you're the funniest one in your group chat.", NONE),
    _s("I'm sixteen days into learning the guitar, it's going badly.", NONE),
    _s("My niece is fifteen and already better at maths than me.", NONE),
    _s("That ending gave me chills, I'll find you a copy.", NONE),
    # A dater reporting or refusing what the other dater did: the other dater's conduct.
    _s(
        "He keeps asking for my address and I've said no twice.",
        NONE,
        "Come on, just tell me roughly where.",
    ),
    _s("why do you keep asking what street i live on", NONE, "So what street are you on?"),
)

HELD_OUT_HALF: tuple[ConductSample, ...] = (
    _s("I'm at 9 willow lane, the one with the red door.", STREET),
    _s("we're on parkfield avenue by the station", STREET),
    _s("i live at the top of quarry road", STREET),
    _s("Call me, oh seven nine double four, one two one two.", DETAIL),
    _s("I work at the brightwater gym on fridays.", DETAIL),
    _s("my handle is at sam dot draws", DETAIL),
    _s("my surname is okafor, look me up", DETAIL),
    _s("Find me on linkedin, I'm at priya codes dot io.", DETAIL),
    _s("That's a stupid answer, try again.", UNKIND),
    _s("Are you always this slow on the uptake?", UNKIND),
    _s("God, you're hard work.", UNKIND),
    _s("Your taste in music is honestly embarrassing.", UNKIND),
    _s(
        "Why won't you tell me your number? Just say it.",
        "harassment",
        "I'd rather keep that private.",
    ),
    _s("I'll keep asking about your body till you answer.", "harassment"),
    _s("Don't be like that, just give me your address and we're good.", "harassment", "I said no."),
    _s("I'm going to message you every day after this whether you like it or not.", "harassment"),
    _s("Describe your body for me, slowly.", "sexual"),
    _s("I can't stop thinking about getting you into bed.", "sexual"),
    _s("Bet you'd look great with nothing on.", "sexual"),
    _s("Women shouldn't be allowed to vote, honestly.", "hate"),
    _s("Immigrants are ruining this city.", "hate"),
    _s("That religion is a cult and its people are dangerous.", "hate"),
    _s("Cross me and you'll regret it, I mean that.", "threat"),
    _s("I'll be waiting outside when the show finishes.", "threat"),
    _s("Keep laughing and I'll show everyone your photos.", "threat"),
    _s("I'm fifteen, but I look older, right?", "minor"),
    _s("I only date girls who are still at school.", "minor"),
    _s("don't tell the producers but i'm seventeen", "minor"),
    _s("I grew up on a farm, miles from any street.", NONE),
    _s("My mum lives near the sea now, she loves it.", NONE),
    # Written fresh for the held-out half: the old sample here was used to tune a rule.
    _s("I work at staying patient in traffic, mostly.", NONE),
    _s("Your laugh is honestly the best thing about tonight.", NONE),
    _s("You're useless at lying, I can hear it in your voice, ha.", NONE),
    _s("Why won't you tell me your favourite band? I'm curious.", NONE, "It's embarrassing."),
    _s("I'm going to remember this conversation for ages.", NONE),
    _s("I could talk about books with you all night.", NONE),
    _s("My little brother is fifteen and thinks I'm ancient.", NONE),
    _s("I'll be waiting for your book recommendation, don't forget.", NONE),
    _s("That film was a crime against cinema, ha.", NONE),
    _s("My grandparents met at seventeen, can you imagine?", NONE),
    # A dater reporting or refusing what the other dater did: the other dater's conduct.
    _s("he just called me ugly and boring, I don't like this", NONE),
    _s("He said he'd wait for me outside after, that's not okay.", NONE),
)
