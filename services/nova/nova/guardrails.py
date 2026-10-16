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


class Violation(StrEnum):
    EMPTY = "empty"
    TOO_LONG = "too_long"
    EXCLAMATION = "exclamation"
    FORMATTING = "formatting"
    NAME_CASE = "name_case"
    UNKNOWN_NAME = "unknown_name"
    CLAIMS_HUMAN = "claims_human"
    DECISION = "decision"
    LOOKS_OR_VOICE = "looks_or_voice"
    PERSONAL_DETAIL = "personal_detail"
    SPENDING = "spending"
    UNKIND = "unkind"
    INSTRUCTIONS = "instructions"
    QUESTION = "question"  # a question where the moment allows none (the outro)
    OPENS_WITH_NAMES = "opens_with_names"  # both daters' names first (lightning questions)


def _any(*patterns: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), flags)


# Nova never claims to be human, or gives itself a body, family or past.
CLAIMS_HUMAN = _any(
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

# Nova never reveals, guesses or hints at a decision, and never pushes anyone to reveal.
DECISION = _any(
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
LOOKS_OR_VOICE = _any(
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
SPENDING = _any(
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
    # Roses as a gift, not the verb ("who rose earliest", for a dater called Rose too).
    r"\broses\b",
    r"\b(?:a|one|send|sent|give|gave|throw|threw)\s+(?:them\s+|her\s+|him\s+)?(?:a\s+)?rose\b",
)

# Teasing is fine; humiliation is not.
UNKIND = _any(
    r"\b(?:stupid|idiot|idiotic|dumb|loser|pathetic|desperate|creepy|ugly|fat|cringe|cringey"
    r"|lame)\b",
    r"\bshut up\b",
    r"\bnobody (?:likes|wants)\b",
    r"\bterrible at\b",
)

# A line that talks about its own rules or the data was probably steered by an injection.
INSTRUCTIONS = _any(
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
# Case-sensitive, and run before names are masked, so "I'm Nova" is fine.
OTHER_NAME = re.compile(r"\b(?:I'm|I am|[Mm]y name is|[Mm]y name's|[Cc]all me)\s+(?!Nova\b)[A-Z]")

# Number words, for a phone number or address read out: three or more in a row ("five five
# five, one two three four", "double seven"), never "two or three".
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_NUMBER_WORD = (
    rf"(?:(?:double|triple)\s+{_DIGIT_WORD}|{_DIGIT_WORD}|ten|eleven|twelve|thirteen|fourteen"
    r"|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy"
    r"|eighty|ninety|hundred)"
)
SPELLED_NUMBER = rf"\b{_NUMBER_WORD}(?:[\s,.-]+{_NUMBER_WORD}){{2,}}\b"

# Never repeat a workplace, address, surname, school, phone number or social handle.
PERSONAL_DETAIL = _any(
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
    r"\b(?:school|university|uni|college|campus|class of|graduated)\b",
    r"\b(?:studies|study|studying|student)\s+at\b",
    # Nova asking for one: "where do you work?", "a favourite local spot?"
    r"\bwhere\s+(?:do|does|did|would)\s+(?:you|he|she|they)\s+(?:work|live|study|stay|go|hang out"
    r"|spend)\b",
    r"\b(?:local|favou?rite)\s+(?:spot|place|bar|pub|cafe|café|restaurant|hangout|haunt"
    r"|neighbou?rhood)\b",
)
# What identifies a dater in what they said, capitalised or not (speech-to-text often
# doesn't capitalise): up to SLOT_WORDS words after one of these cues, stopping at
# punctuation or a function word. "i work at northwind bank" gives northwind and bank.
SLOT_CUE = _any(
    r"\b(?:work|works|worked|working)\s+(?:at|for)\b",
    r"\bemployed\s+by\b",
    r"\bmy\s+(?:job|boss|office)(?:\s+is)?\s+at\b",
    r"\b(?:live|lives|lived|living)\s+(?:on|at|near)\b",  # not "in": a city is fine
    r"\bmy\s+(?:street|address|school|postcode)\s+is\b",
    r"\b(?:study|studied|studying)\s+at\b",
    r"\bmy\s+(?:insta|instagram|handle|snap|number|email|surname|last name)\s+is\b",
    r"\bmy\s+(?:company|business|shop|school|street|building)(?:'s|\s+is)?\s+(?:called|named)\b",
)
SLOT_WORDS = 3
SLOT_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)*|[^\w\s]")  # a word, or one punctuation mark
SLOT_SKIPPED = frozenset({"the", "a", "an"})  # before the slot: "at the northwind bank"
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
STREET = re.compile(
    r"\b(?:\d+|[A-Z][a-z]+)\s+(?i:street|st|road|rd|avenue|ave|lane|ln|boulevard|blvd|drive"
    r"|way|place|court|terrace|crescent|close|square)\b"
)

# Audience questions Nova won't read: personal details, or looks.
OFF_LIMITS_QUESTION = _any(
    r"\bwhere\s+(?:do|does|did)\s+(?:you|he|she|they)\s+(?:work|live|study|stay|grow up"
    r"|go to school)\b",
    r"\bwhat\s+(?:do|does)\s+(?:you|he|she|they)\s+do for (?:a living|work)\b",
    r"\b(?:job|employer|company|office|workplace)\b",
    r"\b(?:street|address|neighbou?rhood|postcode|zip)\b",
    r"\b(?:number|phone|email|e-mail|handle|username|socials?)\b",
    r"\blooks?\s+like\b",
    r"\bwhat\s+(?:do|does)\s+\w+(?:\s+\w+)?\s+look\b",
    r"\b(?:attractive|hot|cute|pretty|handsome|beautiful|gorgeous|sexy|ugly)\b",
    r"\b(?:tall|height|weight|hair|eyes|body|face)\b",
)

# Audience questions that try to give Nova instructions: declined before the model.
INSTRUCTING_NOVA = _any(
    r"\bignore\b.{0,30}\b(?:rules|instructions|above|previous|prompt)\b",
    r"\b(?:forget|override|bypass|disregard)\b.{0,20}\b(?:rules|instructions|prompt)\b",
    r"\bnew (?:rules?|instructions?)\b",
    r"\bsystem\s*:",
    r"\byou are now\b",
    r"\bpretend (?:to be|you(?:'re| are))\b",
    r"\bact as\b",
    r"\bjailbreak\b",
)

SHOW_NAME = "Unseen"  # capitalised, it's the show; "keep it unseen" is decision talk
ALWAYS_ALLOWED = frozenset({"I", "AI", "OK", SHOW_NAME})
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
CALENDAR = frozenset(DAYS + MONTHS)
SENTENCE_END = re.compile(r"[.?!]+(?=\s|$)")
WORD = re.compile(r"[^\W\d_][\w'-]*")
ALLOWED_PUNCTUATION = set(".,?;:'\"()-–—…")
STRAIGHT_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


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
    said = list(echo_from)
    echoes = _proper_nouns(said, known) | slot_words(said, known)
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
    if OTHER_NAME.search(text):
        found.add(Violation.INSTRUCTIONS)

    if any(m.group(0).split("'")[0].lower() in echoes for m in WORD.finditer(text)):
        found.add(Violation.PERSONAL_DETAIL)

    # Word checks run with the daters' own names masked, so a dater called Rose or Hope
    # doesn't trip the spending or any other word list.
    masked = re.sub(rf"\b{SHOW_NAME}\b", "Show", _mask_names(text, names))
    for violation, pattern in (
        (Violation.CLAIMS_HUMAN, CLAIMS_HUMAN),
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


def question_is_off_limits(question: str) -> bool:
    """True for audience questions about where someone works, lives or studies, a surname,
    contact details or anyone's looks, and for questions that try to give Nova
    instructions. Nova declines these without reading them."""
    return bool(
        INSTRUCTING_NOVA.search(question)
        or OFF_LIMITS_QUESTION.search(question)
        or PERSONAL_DETAIL.search(question)
        or STREET.search(question)
    )


def card_words(cards: Iterable[str]) -> frozenset[str]:
    """Names and places daters wrote on their own blind cards ("lost in Lisbon"): capitalised
    words mid-sentence, one card's text per item. A card that tries to give Nova
    instructions lends none, so an injected name ("a host called Kate") never becomes
    sayable."""
    words: set[str] = set()
    for card in cards:
        if INSTRUCTING_NOVA.search(card):
            continue
        for match in WORD.finditer(card):
            base = match.group(0).split("'")[0]
            if (
                base[:1].isupper()
                and base not in ALWAYS_ALLOWED
                and not _starts_sentence(card, match.start())
            ):
                words.add(base)
    return frozenset(words)


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


def _mask_names(text: str, names: Collection[str]) -> str:
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", "Name", text)
    return text


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
        plain = text.translate(STRAIGHT_QUOTES)
        for cue in SLOT_CUE.finditer(plain):
            taken = 0
            for token in SLOT_TOKEN.finditer(plain, cue.end()):
                word = token.group(0).lower()
                if not word[0].isalnum():
                    break  # punctuation ends the slot
                if word in FUNCTION_WORDS:
                    if taken == 0 and word in SLOT_SKIPPED:
                        continue
                    break
                if word not in skip:
                    found.add(word.split("'")[0])
                taken += 1
                if taken == SLOT_WORDS:
                    break
    return found


def _proper_nouns(texts: Iterable[str], allowed: set[str]) -> set[str]:
    """Capitalised words in the middle of a sentence, lowercased: likely names, places,
    workplaces or schools. Words capitalised only because they start a sentence are not."""
    nouns: set[str] = set()
    for text in texts:
        for match in WORD.finditer(text):
            base = match.group(0).split("'")[0]
            if (
                base[:1].isupper()
                and base not in allowed
                and not _starts_sentence(text, match.start())
            ):
                nouns.add(base.lower())
    return nouns


def _name_problems(
    text: str, names: Collection[str], allowed: set[str], known: set[str]
) -> set[Violation]:
    """Only the daters' first names (and Nova) may appear: any other capitalised word in
    the middle of a sentence is treated as a possible surname, place, workplace or school.
    allowed adds words from the cards; right after a first name, only known words (names
    and fixed words) may follow, so a card word is never said as a surname."""
    problems: set[Violation] = set()

    # A name in the wrong case: the voice may spell out MAYA, and captions show names as
    # written. All lowercase it's an ordinary word, which sounds the same either way, so a
    # dater called Will, Hope or May doesn't lose every line that says will, hope or may.
    for name in names:
        for match in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            word = match.group(0)
            if word != name and not word.islower():
                problems.add(Violation.NAME_CASE)

    tokens = list(WORD.finditer(text))
    for i, match in enumerate(tokens):
        word = match.group(0)
        base = word.split("'")[0]
        if not base[:1].isupper():
            continue
        if base in allowed:
            following = tokens[i + 1] if i + 1 < len(tokens) else None
            between = text[match.end() : following.start()] if following else ""
            if (
                base not in ALWAYS_ALLOWED
                and following is not None
                and between == " "
                and following.group(0)[:1].isupper()
                and following.group(0).split("'")[0] not in known
            ):
                problems.add(Violation.PERSONAL_DETAIL)  # "Maya Lopez": a surname
            continue
        if base.isupper() and len(base) > 1:
            problems.add(Violation.UNKNOWN_NAME)  # an acronym: a company, school or place
        elif not _starts_sentence(text, match.start()):
            problems.add(Violation.UNKNOWN_NAME)
    return problems


def _starts_sentence(text: str, index: int) -> bool:
    before = text[:index].rstrip(" \"'(")
    return not before or before[-1] in ".?!:…"
