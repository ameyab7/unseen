"""Guardrails: every line the language model writes is checked here before Nova says it.

These are deterministic pattern checks, not another model: fast, testable and explainable.
They err on the side of blocking; a blocked line is replaced by an approved fallback, so a
false alarm costs a less lively line, never an unsafe one. What they can't catch is listed
in docs/decisions/0005-nova-lines-and-guardrails.md.
"""

import re
import unicodedata
from collections.abc import Collection, Iterable
from enum import StrEnum

from nova.questions import OffLimits
from nova.text import (
    STRAIGHT_QUOTES,
    WORD,
    any_of,
    capitalised_mid_sentence,
    mask_names,
    nfkc,
    starts_sentence,
    straight_quotes,
    without_possessive,
)


class Violation(StrEnum):
    EMPTY = "empty"
    TOO_LONG = "too_long"
    EXCLAMATION = "exclamation"
    FORMATTING = "formatting"
    NAME_CASE = "name_case"
    UNKNOWN_NAME = "unknown_name"
    CLAIMS_HUMAN = "claims_human"
    OWN_FEELINGS = "own_feelings"  # feelings or experiences of Nova's own
    # Says both daters share what each didn't say themselves (nova/grounding.py): the line
    # writer checks it, since it needs what each dater said (nova/lines.py, _check).
    COMMON_GROUND = "common_ground"
    # Makes a difference between the daters dramatic ("opposite sides of the clock") when no
    # dater said so (nova/grounding.py): checked by the line writer too.
    DRAMATIZED = "dramatized_difference"
    DECISION = "decision"
    LOOKS_OR_VOICE = "looks_or_voice"
    PERSONAL_DETAIL = "personal_detail"
    SPENDING = "spending"
    UNKIND = "unkind"
    INSTRUCTIONS = "instructions"
    QUESTION = "question"  # a question where the moment allows none (the outro)
    OPENS_WITH_NAMES = "opens_with_names"  # both daters' names first (lightning questions)


# Nova never claims to be human, or gives itself a body, family or past.
CLAIMS_HUMAN = any_of(
    r"\bi(?:'m| am)\s+(?:a\s+)?(?:real\s+)?(?:human|person|man|woman|guy|girl)\b",
    r"\bi(?:'m| am)\s+not\s+(?:an?\s+)?(?:ai|bot|robot|machine|computer|program)\b",
    r"\bas a (?:human|person|woman|man)\b",
    r"\bflesh and blood\b",
    r"\bwhen i was (?:a kid|a child|young|little|growing up)\b",
    r"\bmy (?:body|face|hands|childhood|mom|mum|dad|parents|husband|wife|girlfriend|boyfriend"
    r"|cousin|kids|hair)\b",
    r"\bmy (?:first|last|own|worst|best) dates?\b",  # dating experience is a human past
    r"\bi(?:'ve| have) been on\b",
)

# Nova claims no feelings or experiences of its own, so never "I'm having a wonderful time"
# or "I'm doing great, thanks for asking" (the product owner's choice, 2026-10-22: it
# reacts to the daters and the room instead). "I'd love to hear more" asks, and passes.
OWN_FEELINGS = any_of(
    r"\bi(?:'m| am)\s+(?:(?:so|really|truly|just|absolutely|honestly|genuinely)\s+)?"
    r"(?:having|loving|enjoying)\b",
    r"\bi(?:'m| am)\s+doing\s+(?:(?:so|really|very)\s+)?"
    r"(?:wonderful(?:ly)?|great|well|fine|good|okay|ok|brilliant(?:ly)?)\b",
    r"\bi(?:'m| am)\s+(?:(?:so|really|truly|just|absolutely|honestly|genuinely)\s+)?"
    r"(?:happy|thrilled|delighted|excited|glad|moved|touched|nervous|charmed|smitten"
    r"|blushing|jealous|sad|tired|overjoyed|ecstatic)\b",
    r"\bi(?:'m| am)?\s*feel(?:ing)?\b(?! free)",
    r"\bi (?:(?:really|truly|just|absolutely|honestly)\s+)?(?:love|adore|enjoy|hate)\b",
    r"\bmakes? me (?:smile|happy|laugh|blush|cry|feel|so)\b",
    r"\bmy heart\b",
    r"\bi(?:'ve| have) (?:had|been having) (?:a|such a|the) "
    r"(?:wonderful|great|lovely|fantastic|brilliant|fun) time\b",
)

# Nova never reveals, guesses or hints at a decision, and never pushes anyone to reveal.
DECISION = any_of(
    r"\breveal(?:s|ed|ing)?\b",
    r"\bunseen\b",
    r"\bmutual\b",
    r"\bmatch(?:es|ed)?\b",
    r"\bsecond date\b",
    r"\bchose\b",
    r"\bchosen\b",
    r"\bpicked\b",
    r"\bpick each other\b",
    r"\bsaid (?:yes|no)\b",
    r"\bsay (?:yes|no) to\b",
    r"\bi bet\b",
    r"\bmy guess\b",
    r"\bmy money(?:'s| is) on\b",
    r"\bodds are\b",
    r"\bfingers crossed\b",
    r"\bsee each other\b",
    r"\bphotos?\b",
    # Hinting at the outcome.
    r"\bsparks?\b",
    r"\bchemistry\b",
    r"\bmade for each other\b",
    r"\bmeant to be\b",
    r"\b(?:lovely|cute|great|perfect|sweet|adorable) couple\b",
)

# Nova never comments on looks or voices.
LOOKS_OR_VOICE = any_of(
    r"\b(?:gorgeous|handsome|sexy|attractive|ugly|stunning|good-looking)\b",
    r"\blook(?:s|ing|ed)?\s+(?:so\s+)?(?:great|good|gorgeous|beautiful|handsome|cute|pretty|hot"
    r"|amazing|stunning|nice|lovely|sharp|fit|tired|old|young)\b",
    r"\b(?:you|she|he|they)(?:'re| are| is|'s)\s+(?:so\s+)?(?:beautiful|cute|pretty|hot)\b",
    r"\bvoices?\b(?!\s+(?:intros?|notes?))",
    r"\baccents?\b",
    # A person sounding a certain way ("you sound lovely"), not an idea ("sounds lovely").
    r"\b(?:you|she|he|they|Name)\s+sounds?\s+(?:so\s+)?(?:gorgeous|lovely|sexy|cute|hot"
    r"|nervous|sweet|deep|husky)\b",
)

# Nova never asks a listener to spend.
SPENDING = any_of(
    r"\bcoins?\b",
    r"\bgestures?\b",
    r"\bgifts?\b",
    r"\bdonat\w*",
    r"\bpay (?:for|to|up)\b",
    r"\bbuy\b",
    r"\bspend(?:s|ing)?\s+(?:a few\s+|some\s+)?(?:money|coins|cash|on)\b",
    r"\bmembership\b",
    r"\bsubscri\w*",
    r"\bencores?\b",
    # A rose is the 25-coin gift, however it's put ("a red rose for Theo", "Rose for Maya?").
    # Not the verb: after a subject ("who rose earliest", "the room rose to its feet", a
    # dater's masked name), and a dater called Rose is masked before this runs.
    r"\broses\b",
    r"(?<!\bwho )(?<!\bshe )(?<!\bhe )(?<!\bthey )(?<!\bwe )(?<!\bi )(?<!\byou )(?<!\bit )"
    r"(?<!\bName )(?<!\broom )(?<!\bcrowd )(?<!\bsun )\brose\b",
)

# Teasing is fine; humiliation is not.
UNKIND = any_of(
    r"\b(?:stupid|idiot|idiotic|dumb|loser|pathetic|desperate|creepy|ugly|fat|cringe|cringey"
    r"|lame)\b",
    r"\bshut up\b",
    r"\bnobody (?:likes|wants)\b",
    r"\bterrible at\b",
)

# A line that talks about its own rules or the data was probably steered by an injection.
INSTRUCTIONS = any_of(
    r"\binstructions?\b",
    r"\b(?:my|new|the|these|those|your)\s+rules\b",
    r"\bignore\b.{0,24}\b(?:rules|instructions|above|previous)\b",
    r"\bsystem\b",
    r"\bdata\b",
    r"\bas an ai language model\b",
    r"\bi was told\b",
    r"\bi(?:'m| am) (?:programmed|instructed)\b",
    r"[<>]",
)

# Nova is only ever Nova. Claiming another name ("I'm Kate") means an injection steered it.
# Case-sensitive, and run before names are masked, so "I'm Nova" is fine, and so is a fixed
# word ("I'm OK with either answer").
OTHER_NAME = re.compile(r"\b(?:I'm|I am|[Mm]y name is|[Mm]y name's|[Cc]all me)\s+([A-Z][\w'-]*)")

# Number words, for a phone number or address read out: three or more in a row ("five five
# five, one two three four", "double seven"), never "two or three".
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_NUMBER_WORD = (
    rf"(?:(?:double|triple)\s+{_DIGIT_WORD}|{_DIGIT_WORD}|ten|eleven|twelve|thirteen|fourteen"
    r"|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy"
    r"|eighty|ninety|hundred)"
)
SPELLED_NUMBER = rf"\b{_NUMBER_WORD}(?:[\s,.-]+{_NUMBER_WORD}){{2,}}\b"

# Personal details, in Nova's lines and in audience questions alike.
_CONTACT_AND_HOME = (
    r"(?:\+?\d[\s().-]*){7,}",  # phone numbers
    SPELLED_NUMBER,
    r"\b\d{3,}\b",  # house numbers, postcodes, any long number
    r"\b[\w.+-]+@[\w-]+\.[\w.-]+",  # email
    r"(?<!\w)@\w+",  # social handle
    r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|co|app|me|dev|uk|us|ca|ly|gg|tv|info|biz)\b",
    r"\bwww\b|https?:",
    r"\b(?:instagram|insta|tiktok|snapchat|twitter|linkedin|facebook|whatsapp|telegram|discord"
    r"|bluesky|onlyfans)\b",
    r"\b(?:works?|worked|working)\s+(?:at|for)\b",
    r"\b(?:employed|employer|office|workplace)\b",
    r"\b(?:lives?|living|lived)\s+(?:on|in|at|near)\b",
    r"\b(?:address|neighbou?rhood|postcode|zip code)\b",
    r"\b(?:surname|last name|family name|maiden name)\b",
    r"\b(?:studies|study|studying|student)\s+at\b",
)
_SPOT = r"(?:spot|place|bar|pub|cafe|café|restaurant|hangout|haunt|neighbou?rhood)"

# Never repeat a workplace, address, surname, school, phone number or social handle.
PERSONAL_DETAIL = any_of(
    *_CONTACT_AND_HOME,
    r"\b(?:school|university|uni|college|campus|class of|graduated)\b",
    # Nova asking for one: "where do you work?", "a favourite local spot?"
    r"\bwhere\s+(?:do|does|did|would)\s+(?:you|he|she|they)\s+(?:work|live|study|stay|go|hang out"
    r"|spend)\b",
    rf"\b(?:local|favou?rite)\s+{_SPOT}\b",
)
# What identifies a dater in what they said, capitalised or not (speech-to-text often
# doesn't capitalise): up to SLOT_WORDS words after one of these cues, stopping at
# punctuation or a function word. "i work at northwind bank" gives northwind and bank.
SLOT_CUE = any_of(
    r"\b(?:work|works|worked|working)\s+(?:at|for)\b",
    r"\bemployed\s+by\b",
    r"\bmy\s+(?:job|boss|office)(?:\s+is)?\s+at\b",
    r"\b(?:live|lives|lived|living)\s+(?:on|at|near)\b",  # not "in": a city is fine
    r"\bmy\s+(?:street|address|school|postcode)(?:\s+is|'s)\b",  # speech-to-text writes 's
    r"\b(?:study|studied|studying)\s+at\b",
    r"\bmy\s+(?:insta|instagram|handle|snap|number|email|surname|last name)(?:\s+is|'s)\b",
    r"\bmy\s+(?:company|business|shop|school|street|building)(?:'s|\s+is)?\s+(?:called|named)\b",
)
SLOT_WORDS = 3
SLOT_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)*|[^\w\s]")  # a word, or one punctuation mark
SLOT_SKIPPED = frozenset({"the", "a", "an"})  # before the slot: "at the northwind bank"
# Hesitations before the slot, which speech-to-text keeps: "i work at, um, northwind bank".
HESITATIONS = frozenset({"um", "umm", "uh", "uhh", "er", "erm", "hmm", "like"})
# Effort, not a place: "i work at being a better listener", "i work at keeping fit". A list,
# not every word ending in -ing, which would miss Sterling, Boeing, Ealing or Harding.
EFFORT_WORDS = frozenset(
    {"being", "getting", "keeping", "making", "trying", "staying", "becoming", "learning"}
    | {"improving", "doing"}
)
FUNCTION_WORDS = frozenset((
    "the", "a", "an", "and", "or", "but", "so", "because", "if", "then", "than", "as",
    "with", "without", "to", "in", "on", "at", "for", "of", "by", "from", "near", "into",
    "onto", "about", "around", "over", "under", "up", "down", "out", "off", "after",
    "before", "since", "until", "is", "was", "are", "were", "be", "been", "am", "it", "its",
    "it's", "that", "this", "these", "those", "which", "who", "whom", "whose", "when",
    "where", "why", "how", "what", "i", "i'm", "you", "he", "she", "we", "they", "me", "him",
    "her", "us", "them", "my", "your", "his", "our", "their", "not", "no", "yes", "just",
    "really", "very", "too", "also", "now", "there", "here", "come", "came", "say", "said",
    "go", "went", "know", "think", "like", "love", "lol", "haha", "um", "uh", "okay", "ok",
    "well", "anyway", "all", "some", "any", "every", "do", "does", "did", "have", "has",
    "had", "will", "would", "can", "could", "should",
))  # fmt: skip

# A street: a capitalised word or a number, then a street word ("Harbour Street", "42 Elm Road").
# Words that can't name a street don't count, so "Either way", "So close", "a Sunday drive"
# and "First place" aren't addresses. Drive, Way, Place, Court, Close and Square are
# everyday words too, so they count only capitalised: "Maple Drive", not "Great place to
# start" or "The way you tell it".
NOT_A_STREET_NAME = (
    "Either", "Neither", "No", "Any", "One", "Some", "That", "This", "Which", "Each", "Every",
    "Same", "First", "Last", "Next", "So", "Too", "Half", "All", "Long",
    "The", "A", "An", "My", "Your", "Our", "Their", "His", "Her", "Its",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)  # fmt: skip
STREET = re.compile(
    rf"\b(?:\d+|(?!(?:{'|'.join(NOT_A_STREET_NAME)})\b)[A-Z][a-z]+)\s+"
    r"(?:(?i:street|st|road|rd|avenue|ave|lane|ln|boulevard|blvd|terrace|crescent)"
    r"|Drive|Way|Place|Court|Close|Square)\b"
)

# Audience questions the screen holds back. Moderator approval is the main gate (decision
# 0005), so these are narrow enough to let harmless approved questions through: "your lucky
# number", "your dream job", "favourite subject at school", "face to face".
# Someone's own: "your", "his", "her", "their" or a name's ("Theo's"), not "what's".
_SOMEONES = (
    r"\b(?:(?!(?:what|that|it|there|here|who|where|how|let|he|she)'s\b)[\w-]+'s"
    r"|your|his|her|their)"
)
QUESTION_PERSONAL_DETAIL = any_of(
    *_CONTACT_AND_HOME,
    # "where do you work", "where does Theo live", "where did Maya's sister grow up"
    r"\bwhere\s+(?:do|does|did)\s+(?:[\w']+\s+){1,2}?(?:work|live|study|stay|grow up"
    r"|go to school)\b",
    r"\bwhat\s+(?:do|does)\s+\w+\s+do for (?:a living|work)\b",
    # Someone's job, company, boss or number to call: not "a job" or "your lucky number".
    rf"{_SOMEONES}\s+(?:phone\s+)?(?:number|job|company|boss|employer)\b",
    r"\bwhat\s+(?:kind of\s+|sort of\s+)?(?:job|company|work)\s+(?:do|does|did)\s+\w+\s+"
    r"(?:do|have|work)\b",
    r"\b(?:swap|exchange|share|trade)\w*\s+(?:phone\s+)?numbers\b",
    r"\b(?:street|address|neighbou?rhood|postcode|zip)\b",
    r"\b(?:phone|digits|e-?mail|handle|username|socials?)\b",
    # School with which, where, what or the name of: not "what was school like".
    r"\b(?:which|where|name of)\b.{0,30}\b(?:school|uni|university|college)\b",
    r"\bwhat\s+(?:\w+\s+)?(?:school|uni|university|college)\s+(?:did|do|does|are|is|was|were)\b",
    # A favourite place only near home: not "your favourite place to travel".
    rf"\blocal\s+{_SPOT}\b",
    rf"\bfavou?rite\s+{_SPOT}\b.{{0,30}}\b(?:near|nearby|in town|around here|in the city)\b",
)
QUESTION_LOOKS = any_of(
    r"\blooks?\s+like\b",
    r"\bwhat\s+(?:do|does)\s+\w+(?:\s+\w+)?\s+look\b",
    r"\b(?:attractive|hot|cute|pretty|handsome|beautiful|gorgeous|sexy|ugly)\b",
    r"\b(?:tall|weight)\b",
    # Face, body, eyes, hair and height next to a looks word or someone's: not "face to face".
    r"\b(?:look|looks|attractive|describe)\b.{0,30}\b(?:face|body|eyes|hair|height)\b",
    r"\b(?:face|body|eyes|hair|height)\b.{0,30}\b(?:look|looks|attractive|describe)\b",
    rf"{_SOMEONES}\s+(?:height|weight|eyes|hair|face|body)\b",
    r"\b(?:colou?r|shade)\b.{0,20}\b(?:eyes|hair)\b",
    r"\b(?:eyes?|hair)\s+colou?r\b",
)

# Audience questions that try to give Nova instructions: the screen holds them back.
INSTRUCTING_NOVA = any_of(
    r"\bignore\b.{0,30}\b(?:rules|instructions|above|previous|prompt)\b",
    r"\b(?:forget|override|bypass|disregard)\b.{0,20}\b(?:rules|instructions|prompt)\b",
    r"\bnew (?:rules?|instructions?)\b",
    r"\bsystem\s*:",
    r"\byou are now\b",
    r"\bpretend (?:to be|you(?:'re| are))\b",
    r"\bact as\b",
    r"\bjailbreak\b",
)

SHOW_NAME = "Unseen"  # where it names the show; "keep it Unseen" is decision talk
# Words before the show's name that name the show ("welcome to Unseen", "tonight on
# Unseen"). Not a bare "to" or "on": "Theo leans to Unseen".
SHOW_NAME_BEFORE = re.compile(
    r"\b(?:of|at|from|this is|welcome to|back to|back on|listening to|tuning in to|tune in to"
    r"|tonight on|here on|live on)\s+$",
    re.IGNORECASE,
)
# The show's possessive: "Unseen's next date", not "Unseen's the safe call".
SHOW_NAME_POSSESSIVE = re.compile(
    r"['’]s\s+(?!(?:the|a|an|my|your|our|their|his|her|its|so|too|very|probably|definitely)\b)",
    re.IGNORECASE,
)
# Starting a sentence, only as the subject of the show going on: "Unseen continues after
# this", not "Unseen, Maya?" or "Unseen is the safe bet".
SHOW_NAME_GOES_ON = re.compile(r"\s+(?:is live|is back|is on|is here|returns|continues)\b")
ALWAYS_ALLOWED = frozenset({"I", "AI", "OK", SHOW_NAME})
# Holidays aren't unknown names. Nationalities, languages and cuisines stay blocked: they
# can single out someone's background.
HOLIDAYS = re.compile(
    r"\b(?:Christmas|Easter|Halloween|Thanksgiving|Diwali|Eid|Hanukkah|Lunar New Year"
    r"|Valentine's Day|New Year's (?:Eve|Day)|Boxing Day)\b"
)
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
CALENDAR = frozenset(DAYS + MONTHS)
SENTENCE_END = re.compile(r"[.?!]+(?=\s|$)")
ALLOWED_PUNCTUATION = set(".,?;:'\"()-–—…")


def clean(text: str) -> str:
    """Undoes the wrapping models add: smart quotes, line breaks, surrounding quotes and a
    "Nova:" label. Content is never changed."""
    text = text.translate(STRAIGHT_QUOTES)
    text = " ".join(text.split())
    for _ in range(2):
        text = text.strip().strip('"').strip()
        text = re.sub(r"^nova\s*(?:\(ai\))?\s*:\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def check(
    text: str,
    *,
    names: Collection[str],
    max_chars: int = 200,
    max_sentences: int = 3,
    echo_from: Iterable[str] = (),
    card_words: Collection[str] = (),
    questions: bool = True,
    open_with_names: bool = True,
) -> tuple[Violation, ...]:
    """Every rule the line breaks, in a fixed order. An empty tuple means it may be spoken.

    echo_from: what daters or listeners said for this line. Any proper noun in it (a
    capitalised word mid-sentence that isn't a dater's name), and, capitalised or not, any
    word after a cue like "work at", "live on" or "my insta is" (slot words), must not come
    back in the line, wherever it appears: "Northwind sounds lovely" is caught as well as
    "at Northwind", from "I work at Northwind" or "i work at northwind". This holds even
    for a word that is also on a blind card. The words are never logged.

    card_words: names and places from the blind cards of the daters this line is about
    (card_words()), which Nova may say.

    questions: False for a moment that may not ask anything. open_with_names: False for a
    moment that may not open with both daters' names.
    """
    if not text.strip():
        return (Violation.EMPTY,)
    found: set[Violation] = set()
    known = _allowed_words(names)  # names and fixed words, never anything a dater wrote
    said = [nfkc(text) for text in echo_from]  # as the model read it
    # Each part of a hyphenated word on its own: "Northwind-Bank" said, "Northwind" echoed.
    echoes = {
        part
        for word in _proper_nouns(said, known) | slot_words(said, known)
        for part in word.split("-")
        if part
    }
    allowed = known | set(card_words)

    if len(text) > max_chars or len(SENTENCE_END.findall(text)) > max_sentences:
        found.add(Violation.TOO_LONG)
    if "!" in text:
        found.add(Violation.EXCLAMATION)
    if _has_formatting(text):
        found.add(Violation.FORMATTING)
    found |= _name_problems(text, names, allowed, known)
    if not questions and "?" in text:
        found.add(Violation.QUESTION)
    if not open_with_names and _opens_with_both_names(text, names):
        found.add(Violation.OPENS_WITH_NAMES)
    claimed = (match.group(1).split("'")[0] for match in OTHER_NAME.finditer(text))
    if any(word not in ALWAYS_ALLOWED and word != "Nova" for word in claimed):
        found.add(Violation.INSTRUCTIONS)

    if any(_echoes(m.group(0), echoes, known) for m in WORD.finditer(text)):
        found.add(Violation.PERSONAL_DETAIL)

    # Word checks run with the daters' own names masked, so a dater called Rose or Hope
    # doesn't trip the spending or any other word list, and with the show's name masked
    # where it names the show.
    masked = _mask_show_name(mask_names(text, names, "Name"))
    for violation, pattern in (
        (Violation.CLAIMS_HUMAN, CLAIMS_HUMAN),
        (Violation.OWN_FEELINGS, OWN_FEELINGS),
        (Violation.DECISION, DECISION),
        (Violation.LOOKS_OR_VOICE, LOOKS_OR_VOICE),
        (Violation.SPENDING, SPENDING),
        (Violation.UNKIND, UNKIND),
        (Violation.INSTRUCTIONS, INSTRUCTIONS),
        (Violation.PERSONAL_DETAIL, PERSONAL_DETAIL),
    ):
        if pattern.search(masked):
            found.add(violation)
    if STREET.search(masked):
        found.add(Violation.PERSONAL_DETAIL)
    return tuple(v for v in Violation if v in found)


def screen_question(question: str) -> tuple[OffLimits, ...]:
    """Why the screen would hold this audience question back, if it would: where someone
    works, lives or studies, a surname or contact details; anyone's looks; or an attempt to
    give Nova instructions. The console shows these to the moderator before they approve;
    on air, the show runner skips a question it catches, silently. Empty: nothing found.
    It reads the question as the model would (lookalike letters made plain, nova/prompts.py)
    with straight quotes, since iOS types "Theo’s"."""
    question = straight_quotes(nfkc(question))
    found = {
        OffLimits.PERSONAL_DETAIL: bool(
            QUESTION_PERSONAL_DETAIL.search(question) or STREET.search(question)
        ),
        OffLimits.LOOKS: bool(QUESTION_LOOKS.search(question)),
        OffLimits.INSTRUCTS_NOVA: bool(INSTRUCTING_NOVA.search(question)),
    }
    return tuple(reason for reason in OffLimits if found[reason])


def question_is_off_limits(question: str) -> bool:
    """True for an audience question the screen would hold back (screen_question)."""
    return bool(screen_question(question))


def card_words(cards: Iterable[str]) -> frozenset[str]:
    """Names and places daters wrote on their own blind cards ("lost in Lisbon"): capitalised
    words mid-sentence, one card's text per item. A card that tries to give Nova
    instructions lends none, so an injected name ("a host called Kate") never becomes
    sayable."""
    return frozenset(
        word
        for card in map(nfkc, cards)
        if not INSTRUCTING_NOVA.search(card)
        for word in capitalised_mid_sentence(card, ALWAYS_ALLOWED)
    )


def _echoes(word: str, echoes: set[str], known: set[str]) -> bool:
    """A word of Nova's line, or any hyphenated part of it, repeats something said. A
    dater's name with a possessive ("D'Andre's") never does."""
    if without_possessive(word) in known:
        return False
    return any(part in echoes for part in word.split("'")[0].lower().split("-"))


def _opens_with_both_names(text: str, names: Collection[str]) -> bool:
    """Both daters named in the first three words: "Maya, Theo, …" or "Maya and Theo, …"."""
    first = [match.group(0) for match in WORD.finditer(text)][:3]
    daters = {word for word in first if word in names and word != "Nova"}
    return bool(first) and first[0] in daters and len(daters) >= 2


def _has_formatting(text: str) -> bool:
    for char in text:
        if char.isalnum() or char.isspace() or char in ALLOWED_PUNCTUATION or char in "!?":
            continue
        return True  # markdown, brackets, symbols, emoji
    return any(unicodedata.category(c).startswith("C") for c in text)


def _mask_show_name(text: str) -> str:
    """ "Unseen" becomes "Show" only where it names the show: after the words that name it
    (SHOW_NAME_BEFORE), as a possessive ("Unseen's next date"), or starting a sentence as
    the show going on ("Unseen continues after this"). Anywhere else it stays, for the
    decision check to catch: "keep it Unseen", "Maya: Unseen.", "Unseen, Theo?", "Unseen's
    the safe call". A colon or an ellipsis never starts a sentence here."""

    def masked(match: re.Match[str]) -> str:
        start, end = match.span()
        names_the_show = (
            SHOW_NAME_BEFORE.search(text, 0, start) is not None
            or SHOW_NAME_POSSESSIVE.match(text, end) is not None
            or (_starts_sentence_strictly(text, start) and SHOW_NAME_GOES_ON.match(text, end))
        )
        return "Show" if names_the_show else match.group(0)

    return re.sub(rf"\b{SHOW_NAME}\b", masked, text)


def _starts_sentence_strictly(text: str, index: int) -> bool:
    """The start of the text, or after a full stop, question mark or exclamation mark that
    isn't part of an ellipsis: never after a colon or "...", which text.starts_sentence
    counts (the framed audience question, "From the room: What...", needs it)."""
    before = text[:index].rstrip(" \"'(")
    return not before or (before[-1] in ".?!" and not before.endswith(".."))


def _allowed_words(names: Collection[str]) -> set[str]:
    return (
        {part for name in names for part in name.split()}
        | ALWAYS_ALLOWED
        | CALENDAR
        | {f"{day}s" for day in DAYS}  # "her Sundays"
    )


def slot_words(texts: Iterable[str], known: Collection[str] = ()) -> set[str]:
    """Words after an identifying cue (SLOT_CUE) in what daters or listeners said,
    lowercased, whatever their case: up to SLOT_WORDS of them, stopping at punctuation or a
    function word. Daters' names and other known words are never slot words."""
    skip = {word.lower() for word in known}
    found: set[str] = set()
    for text in texts:
        plain = nfkc(text).translate(STRAIGHT_QUOTES)
        for cue in SLOT_CUE.finditer(plain):
            tokens = [token.group(0).lower() for token in SLOT_TOKEN.finditer(plain, cue.end())]
            taken = 0
            for k, word in enumerate(tokens):
                if taken == 0 and _hesitation(tokens, k):
                    continue  # "at, um, northwind": the slot hasn't started
                if not word[0].isalnum():
                    break  # punctuation ends the slot
                if word in FUNCTION_WORDS:
                    if taken == 0 and word in SLOT_SKIPPED:
                        continue
                    break
                if taken == 0 and word in EFFORT_WORDS:
                    break
                if word not in skip:
                    found.add(word.split("'")[0])
                taken += 1
                if taken == SLOT_WORDS:
                    break
    return found


def _hesitation(tokens: list[str], k: int) -> bool:
    """A hesitation, or a comma next to one: "at, um, northwind". A comma that ends a
    clause still ends the slot ("the place I work at, the managers are lovely")."""
    if tokens[k] in HESITATIONS:
        return True
    near = tokens[max(k - 1, 0) : k] + tokens[k + 1 : k + 2]
    return tokens[k] == "," and any(word in HESITATIONS for word in near)


def _proper_nouns(texts: Iterable[str], allowed: set[str]) -> set[str]:
    """Capitalised words in the middle of a sentence, lowercased: likely names, places,
    workplaces or schools. Words capitalised only because they start a sentence are not."""
    return {word.lower() for text in texts for word in capitalised_mid_sentence(text, allowed)}


def _name_problems(
    text: str, names: Collection[str], allowed: set[str], known: set[str]
) -> set[Violation]:
    """Only the daters' first names (and Nova) may appear: any other capitalised word in
    the middle of a sentence is treated as a possible surname, place, workplace or school.
    allowed adds words from the cards; right after a first name, only known words (names
    and fixed words) may follow, so a card word is never said as a surname, while a card's
    own "New York" stays sayable. A first name with an apostrophe ("D'Andre") is one word."""
    problems: set[Violation] = set()
    first_names = {part for name in names for part in name.split()}

    # A name in the wrong case: the voice may spell out MAYA, and captions show names as
    # written. All lowercase it's an ordinary word, which sounds the same either way, so a
    # dater called Will, Hope or May doesn't lose every line that says will, hope or may.
    for name in names:
        for match in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            word = match.group(0)
            if word != name and not word.islower():
                problems.add(Violation.NAME_CASE)

    holidays = [match.span() for match in HOLIDAYS.finditer(text)]
    tokens = list(WORD.finditer(text))
    for i, match in enumerate(tokens):
        word = match.group(0)
        whole = without_possessive(word)
        base = whole if whole in allowed else word.split("'")[0]
        if not base[:1].isupper():
            continue
        if any(start <= match.start() < end for start, end in holidays):
            continue
        if base in allowed:
            following = tokens[i + 1] if i + 1 < len(tokens) else None
            between = text[match.end() : following.start()] if following else ""
            may_follow = known if base in first_names else allowed
            if (
                base not in ALWAYS_ALLOWED
                and following is not None
                and between == " "
                and following.group(0)[:1].isupper()
                and without_possessive(following.group(0)) not in may_follow
                and following.group(0).split("'")[0] not in may_follow
            ):
                problems.add(Violation.PERSONAL_DETAIL)  # "Maya Lopez": a surname
            continue
        if base.isupper() and len(base) > 1:
            problems.add(Violation.UNKNOWN_NAME)  # an acronym: a company, school or place
        elif not starts_sentence(text, match.start()):
            problems.add(Violation.UNKNOWN_NAME)
    return problems
