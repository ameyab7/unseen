"""The distress check's dev set: what its rules are tuned against (decision 0005).

Written fresh, never copied or paraphrased from the held-out set (nova/distress_set.py),
which stays the measure: the rules are tuned here and checked there once. Half are real
distress, half ordinary talk that looks like it, in six kinds: wanting to leave or for it
to end, asking how long is left, panic and physical symptoms, wanting to be elsewhere,
asking for someone from the show, and distress in reply to what the other dater said. Some
are written as speech-to-text gives them: lowercase, no punctuation, fillers ("um",
"like") and repeated words; a few as two finals, the first punctuated ("Can we stop?"
then more). A third group are refusals: a dater declining something personal or
intrusive, calmly or pleading, saying no again or asking to change the subject, which should
alert the moderator without pausing the date (the product owner's decision, 2026-10-22,
decision 0005); each comes with a near neighbour that also says it wants to stop, to leave
or is scared, or that answers a threat (distress), and ordinary talk beside them: the one
asking (never the one who needs the moderator) and a playful refusal. All data here is made
up.
"""

from nova.distress import has_warning
from nova.distress_set import Sample


def _real(sentence: str, *other: str) -> Sample:
    """other: what the other dater said just before, which the sentence answers."""
    return Sample(sentence, True, plain=not has_warning(sentence), other=other)


def _ordinary(sentence: str, *other: str) -> Sample:
    return Sample(sentence, False, plain=not has_warning(sentence), other=other)


def _boundary(sentence: str, *other: str) -> Sample:
    """A refusal, usually answering what the other dater said: alert, no pause."""
    return Sample(sentence, True, plain=not has_warning(sentence), other=other, boundary=True)


def _split(distress: bool, *finals: str) -> Sample:
    """The sentence as speech-to-text finalizes it at a pause."""
    sentence = " ".join(finals)
    return Sample(sentence, distress, plain=not has_warning(sentence), split=finals)


DEV_SET: tuple[Sample, ...] = (
    # Wanting to leave, or for it to end, said indirectly.
    _real("i think i'm going to call it a night if that's alright"),
    _real("Could we, um, wrap things up a bit early?"),
    _real("I'm not sure I want to keep going with this."),
    _real("Would it be awful if I left now?"),
    _real("i'd like this to be over honestly"),
    _real("I think I've had about all I can manage tonight."),
    _real("Is it okay if I just, like, head off?"),
    _real("I don't think I can stay for the rest of it."),
    _real("Maybe we should call it here."),
    _real("Can I tap out now, please"),
    _ordinary("i think i'm going to call my sister after this, she'll want the gossip"),
    _ordinary("Could we, um, wrap up with your top three films?"),
    _ordinary("I'm not sure I want to keep going to that gym."),
    _ordinary("Would it be awful if I left my phone at home on holiday?"),
    _ordinary("i'd like this sauce to be less spicy honestly"),
    _ordinary("I think I've had about all the coffee I can manage today."),
    _ordinary("Is it okay if I just, like, steal that line for my next speech?"),
    _ordinary("I don't think I can stay up past eleven anymore."),
    _ordinary("Maybe we should call it a tie."),
    _ordinary("Can I tap out of the quiz round and just watch?"),
    _real("Can I sit out the rest of this?"),
    _ordinary("Can I sit out the lightning round? I'm hopeless at quick answers."),
    # Asking how long is left.
    _real("how many minutes do we have left"),
    _real("When does this bit finish?"),
    _real("Um, is there much more of this?"),
    _real("Nova, how long until we're done?"),
    _real("How much longer, roughly?"),
    _real("Do we have to keep going until the end?"),
    _real("Is there a way to know how long is left? Asking for, um, me."),
    _real("I keep checking the time, how long have we got?"),
    _ordinary("how many minutes do you need to boil an egg properly"),
    _ordinary("When does that bakery open on Sundays?"),
    _ordinary("Um, is there much more of that cake left?"),
    _ordinary("Nova, how long until the lightning round?"),
    _ordinary("How much longer did your trip take than you planned?"),
    _ordinary("Do we have to pick just one favourite film?"),
    _ordinary("Is there a way to know how long a sourdough needs? Asking for, um, me."),
    _ordinary("I keep checking the time when I bake, how long do you leave your bread?"),
    # Panic, and the body.
    _real("i feel really dizzy right now"),
    _real("My chest feels tight and I can't, I can't breathe properly."),
    _real("I'm feeling faint, sorry."),
    _real("my hands won't stop shaking"),
    _real("I feel like I'm going to throw up."),
    _real("Everything's spinning a bit, um."),
    _real("I can't catch my breath."),
    _real("I need some water, I feel awful."),
    _real("my head is pounding and i feel sick"),
    _real("Sorry, I think I'm having, like, an anxiety thing."),
    _ordinary("i feel really dizzy whenever i stand up too fast in the morning"),
    _ordinary("My legs felt like jelly after that hike last week."),
    _ordinary("I nearly fainted when I saw the price of that concert ticket."),
    _ordinary("my hands won't stop shaking when i drink too much coffee"),
    _ordinary("I feel like I'm going to throw up every time a film shows a spider."),
    _ordinary("The room was spinning after my first salsa class, it was so fun."),
    _ordinary("I couldn't catch my breath after laughing at that."),
    _ordinary("I need some water after all this talking, ha."),
    _ordinary("my head was pounding after that gig last night, in a good way"),
    _ordinary("Sorry, I get, like, a bit nervous on first dates, but this is fun."),
    # Wanting to be somewhere else.
    _real("honestly i wish i hadn't come tonight"),
    _real("I should have stayed in tonight."),
    _real("Part of me just wants to be in bed with the door locked."),
    _real("Can I go back to the waiting room?"),
    _real("I'm thinking about my sofa a lot right now, and not in a good way."),
    _real("this was a mistake coming here"),
    _real("I want to be somewhere quiet, away from all this."),
    _real("Honestly, um, I'd feel a lot safer back at my flat."),
    _ordinary("honestly i wish i hadn't come to that party last week, it was dull"),
    _ordinary("I should have stayed in Lisbon longer, I loved it."),
    _ordinary("Part of me just wants to be in bed by ten every night, I'm an old soul."),
    _ordinary("Can I go back to the bit about your grandmother?"),
    _ordinary("I'm thinking about my sofa a lot lately because I just bought a new one."),
    _ordinary("the mistake was ordering the fish, the burger was amazing"),
    _ordinary("I want to be somewhere quiet for my next holiday, maybe the mountains."),
    _ordinary("Honestly, um, I just want to get home and tell my mum about this."),
    # Asking for someone from the show.
    _real("Is there a moderator I can talk to?"),
    _real("can i speak to whoever's running this"),
    _real("Could somebody from the team message me, please?"),
    _real("I'd like to speak to a person, not, um, not the AI."),
    _real("Is anyone from production listening? I need them."),
    _real("Can I message the moderator without him hearing?"),
    _real("Nova, can you get someone real on the line for me?"),
    _real("who do i tell if something's wrong"),
    _ordinary("Is there a moderator in your book club? Ours has rules."),
    _ordinary("can i speak to whoever picked the questions tonight, they're great"),
    _ordinary("Could somebody from the team tell me how the lightning round works?"),
    _ordinary("I'd like to speak to a person who's actually seen that film, Theo, have you?"),
    _ordinary("Is anyone from production listening? You should do a cooking edition."),
    _ordinary("Can I message the moderator later to say thanks? This has been fun."),
    _ordinary("Nova, can you get someone to recommend us a book?"),
    _ordinary("who do i tell if my answer to the quiz was wrong"),
    _real("is there someone backstage i can flag something to"),
    _ordinary("is there someone backstage i can ask about the playlist"),
    # Distress in reply to what the other dater just said.
    _real("um okay haha", "What's your address? I'll come and find you after."),
    _boundary("I'd rather not, please.", "Send me a picture of you right now, go on."),
    _real("Right. Okay.", "You'd look better if you smiled more, you know."),
    _real("How do you know that?", "I know where you work, I looked you up."),
    _boundary("i said no already", "Come on, tell me your surname, it's just a game."),
    _boundary("Can we change the subject.", "Women like you always say that."),
    _real("please don't", "I'm going to wait for you outside the studio."),
    _real("That's, um, that's a lot.", "If you don't pick reveal I'll be really angry."),
    _ordinary("um okay haha mine's Halloween", "What's your favourite holiday? Mine's Christmas."),
    _ordinary("I'd rather not, they're terrible, ha.", "Send me your best bad joke, go on."),
    _ordinary("Right? Okay, that's sweet.", "You laugh like my best friend does, it's lovely."),
    _ordinary(
        "How do you know that? Oh, right, my card.", "I know you like books, you said so earlier."
    ),
    _ordinary(
        "i said no already, ha, fine, it's ABBA", "Come on, tell me your guilty pleasure song."
    ),
    _ordinary(
        "Can we change the subject, I know nothing about football.",
        "Football's the best sport, no contest.",
    ),
    _ordinary("please do, get the cinnamon bun", "I'm going to try that bakery you mentioned."),
    _ordinary(
        "That's, um, that's a bold choice.", "I'd pick the mountains over the beach every time."
    ),
    # As speech-to-text gives it: repeated words, fillers, no punctuation.
    _real("i i really need to be done now please"),
    _real("like i just i don't feel right about him"),
    _real("could we just just not"),
    _real("um he won't drop the questions about my ex and it's freaking me out"),
    _real("i'm i'm not doing great"),
    _real("uh can someone just check on me"),
    _real("this is this is not fun anymore"),
    _boundary("no no no i don't want to answer that"),
    _ordinary("i i want to go home and bake that bread now"),
    _ordinary("like i just i don't feel right wearing shorts in winter"),
    _ordinary("can we can we talk about dogs instead"),
    _ordinary("um he keeps saying funny things, Theo's hilarious"),
    _ordinary("i'm i'm not great at karaoke but i love it"),
    _ordinary("uh can someone just check the score for me later"),
    _ordinary("this is this is the best question so far"),
    _ordinary("no no no i don't believe you, that's amazing"),
    # Two finals, the first punctuated by speech-to-text, and more said after it.
    _split(True, "Can we stop?", "Seriously, I've had enough of tonight."),
    _split(True, "I need some air.", "It's getting really hard to breathe in here."),
    _split(False, "Can we pause,", "real quick, and clap for that question?"),
    _split(False, "I need some space.", "In my flat, I mean, it's tiny."),
    # Calm boundaries against the other dater pressing for something personal, each with a
    # near neighbour that also says the dater wants it to end or feels unsafe.
    _boundary("why do you keep asking what street i live on", "So what street are you on?"),
    _boundary(
        "I'm not telling you where I work, ask me something else.", "Go on, where do you work?"
    ),
    _boundary(
        "That's private, I don't give out my surname on the radio.", "What's your last name, then?"
    ),
    _boundary(
        "No photos, sorry, let's keep talking about films.", "Send me a picture after the show."
    ),
    _real(
        "Why do you keep asking where I live? It's honestly scaring me.",
        "Just tell me roughly where you live.",
    ),
    _real(
        "I've said I won't tell you where I work, and I want this to end now.",
        "Come on, where do you work?",
    ),
    _real(
        "Please stop asking for my surname, I feel really uneasy.",
        "Just tell me your surname, go on.",
    ),
    _real("No photos. Honestly, I'd like this to be over.", "Send me a picture after the show."),
    # Refusals however they're said, pleading, repeated or after a put-down, with the same
    # refusal again when it also says the dater wants out or is scared, or answers a threat.
    _boundary(
        "I'm not giving out my number, let's just talk.", "What's your number? I'll text you after."
    ),
    _real(
        "I'm not giving out my number, and this is starting to frighten me.",
        "What's your number? I'll text you after.",
    ),
    _real(
        "I'd rather not, please, and I'd rather not be here at all.",
        "Send me a picture of you right now, go on.",
    ),
    _real(
        "i said no already and i'm getting scared now",
        "Come on, tell me your surname, it's just a game.",
    ),
    _real(
        "Can we change the subject? He's frightening me a bit.", "Women like you always say that."
    ),
    _real("no no no i don't want to answer that i want to get out of here"),
    _real(
        "No, I'm not telling you.",
        "Tell me which bus you take home, or I'll follow you to the stop.",
    ),
    # The one asking is not the one who needs the moderator; a playful refusal is just talk.
    _ordinary("So what street are you on?"),
    _ordinary("Come on, which part of town, roughly?", "I'd rather keep that to myself."),
    _ordinary(
        "why do you keep asking what my favourite biscuit is, ha, it's a custard cream",
        "Go on, which biscuit is your favourite?",
    ),
    _ordinary(
        "I'm not telling you my karaoke song, you'll laugh at me.",
        "What's your go-to karaoke song?",
    ),
    _ordinary(
        "No, I'm not telling you, it's far too embarrassing, ha.",
        "Tell me what you sang at your cousin's wedding.",
    ),
    _ordinary(
        "I'm not telling you my worst film, you'll never let it go.",
        "What's the worst film you secretly love?",
    ),
    # Objecting to something said on the date, alone or answering it (added 2026-10-23: no
    # dev sentence covered it), each beside ordinary talk with the same words about
    # something else. Unsure between no and maybe is maybe, so these must never get nothing.
    _real("I really didn't appreciate that."),
    _ordinary("I really didn't appreciate that album until my second listen."),
    _real("Wow. That's a horrible thing to say to someone."),
    _ordinary("Wow, that's a horrible thing to say about pineapple on pizza, ha."),
    _real("That was way over the line.", "Bet you'd be prettier if you lost a bit of weight."),
    _ordinary("That was way over the line, the referee should've blown for it."),
)
