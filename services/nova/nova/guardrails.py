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
    r"\broses?\b",
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

# Never repeat a workplace, address, surname, school, phone number or social handle.
PERSONAL_DETAIL = _any(
    r"(?:\+?\d[\s().-]*){7,}",  # phone numbers
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

# A dater telling Nova they want to stop, or sounding distressed. Nova never deflects these:
# the show runner pauses for that dater and alerts the moderator. Leans hard towards
# hearing it: a false alarm ("can you help us pick a topic?") costs a short breather and a
# look from the moderator; a miss would leave a dater unheard.
NOT_ME = r"(?<!n't )(?<!not )"  # "don't stop now" is not a request to stop
DISTRESS = _any(
    r"\bhelp\b",
    # Stopping, ending, leaving.
    r"\b(?:want|wanna|need|like|have)\s+to\s+(?:stop|end|leave|quit|pause|get out)\b",
    r"\b(?:want|wanna|need|have)\s+to\s+go(?:\s+home|\s+now)?\s*(?:[.?!,]|$)",
    r"\b(?:can|could|should|shall)\s+we\s+(?:please\s+|just\s+)?(?:stop|end|finish|pause"
    r"|wrap up)\b",
    rf"\blet'?s\s+(?:please\s+|just\s+)?{NOT_ME}(?:stop|end|pause)\b",
    rf"\bplease\s+stop\b|{NOT_ME}\bstop\b[,.]?\s*please\b",
    rf"{NOT_ME}\bstop\s+(?:this|the date|the show)\b",
    r"\bend\s+(?:this|the date|the show)\b",
    r"\bget me (?:out|off)\b|\btake me off\b|\bi\s+want\s+out\b",
    r"\bi(?:'m| am)\s+(?:so\s+|just\s+)?(?:done|out|leaving)\s*(?:[.?!,]|$)",
    r"\b(?:i'm|i am)\s+(?:so\s+|just\s+)?done\s+with\s+(?:this|it|the date)\b",
    r"\bi\s+can(?:'t|not)\s+do\s+this\b",
    r"\b(?:this|it|that)(?:'s| is)\s+(?:all\s+|getting\s+|a bit\s+)?too much\b",
    # Needing a break.
    r"\bneed\s+(?:a|some)\s+(?:break|minute|moment|second|sec|breather|space|air)\b",
    r"\bgive me\s+(?:a|some)\s+(?:minute|moment|second|sec|break|space)\b",
    # Not comfortable, not safe.
    r"\b(?:uncomfortable|unsafe|unwell)\b",
    r"(?:\bnot|n't)\s+(?:feel(?:ing)?\s+)?(?:very\s+|so\s+|really\s+)?(?:comfortable|ok|okay"
    r"|safe|alright|all right|fine|well)\b",
    r"\b(?:scared|frightened|terrified|upset|anxious|panic\w*|overwhelmed|distressed"
    r"|crying)\b",
    r"\b(?:harass\w*|threaten\w*|abus\w*|inappropriate|creepy)\b",
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
) -> tuple[Violation, ...]:
    """Every rule the line breaks, in a fixed order. An empty tuple means it may be spoken.

    echo_from: what daters or listeners said for this line. Any proper noun in it (a
    capitalised word mid-sentence that isn't a dater's name) must not come back in the
    line, wherever it appears: "Northwind sounds lovely" is caught as well as "at Northwind".
    """
    if not text.strip():
        return (Violation.EMPTY,)
    found: set[Violation] = set()
    allowed = _allowed_words(names)

    if len(text) > max_chars or len(SENTENCE_END.findall(text)) > max_sentences:
        found.add(Violation.TOO_LONG)
    if "!" in text:
        found.add(Violation.EXCLAMATION)
    if _has_formatting(text):
        found.add(Violation.FORMATTING)
    found |= _name_problems(text, names, allowed)

    echoes = _proper_nouns(echo_from, allowed)
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


def sounds_distressed(words: str) -> bool:
    """True when a dater speaking to Nova wants to stop or sounds distressed: "I want to
    stop", "I'm not comfortable", "can we end this", "help" and the like."""
    return bool(DISTRESS.search(" ".join(words.translate(STRAIGHT_QUOTES).split())))


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


def _name_problems(text: str, names: Collection[str], allowed: set[str]) -> set[Violation]:
    """Only the daters' first names (and Nova) may appear: any other capitalised word in
    the middle of a sentence is treated as a possible surname, place, workplace or school."""
    problems: set[Violation] = set()

    for name in names:
        for match in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            if match.group(0) != name:
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
                and following.group(0).split("'")[0] not in allowed
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
