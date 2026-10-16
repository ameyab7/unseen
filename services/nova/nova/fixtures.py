"""Rehearsal daters. Maya and Theo are the brand book's example pair; their first prompts
come from Nova's opening line there. Everything else here is made-up test data."""

from nova.cards import BlindCard, Pair, Prompt

MAYA = BlindCard(
    first_name="Maya",
    age=29,
    pronouns="she/her",
    prompts=(
        Prompt("Ask me about", "my worst haircut."),
        Prompt("My perfect Sunday", "starts with a long walk to a bakery."),
    ),
)

THEO = BlindCard(
    first_name="Theo",
    age=31,
    pronouns="he/him",
    prompts=(
        Prompt("I claim I can", "parallel park anything."),
        Prompt("The way to win me over", "is a good recommendation for a book."),
    ),
)

REHEARSAL_PAIR = Pair(left=MAYA, right=THEO)

NEXT_PAIR = Pair(
    left=BlindCard(
        first_name="Sam",
        age=29,
        pronouns="they/them",
        prompts=(
            Prompt("I'm weirdly good at", "naming a song from the first second."),
            Prompt("Ask me about", "the time I got lost in Lisbon."),
        ),
    ),
    right=BlindCard(
        first_name="Priya",
        age=31,
        pronouns="she/her",
        prompts=(
            Prompt("My hidden talent", "is folding fitted sheets."),
            Prompt("Ask me about", "my grandmother's chess club."),
        ),
    ),
)


def _card(name: str, age: int, pronouns: str, *prompts: tuple[str, str]) -> BlindCard:
    first, second = (Prompt(question, answer) for question, answer in prompts)
    return BlindCard(first_name=name, age=age, pronouns=pronouns, prompts=(first, second))


# Daters whose first names are everyday words, which Nova's lines also use ("I hope you
# will..."). Not in NIGHT, so the tracked full-night number stays comparable.
EVERYDAY_PAIR = Pair(
    _card(
        "Will", 30, "he/him",
        ("Ask me about", "the summer I worked on a lighthouse boat."),
        ("My perfect Sunday", "is a long lunch that turns into dinner."),
    ),
    _card(
        "Hope", 28, "she/her",
        ("I claim I can", "find the best seat in any cinema."),
        ("The way to win me over", "is a really good sandwich."),
    ),
)  # fmt: skip

# Daters who give no pronouns (the prototype makes them optional): Nova uses their names.
NO_PRONOUNS_PAIR = Pair(
    _card(
        "Robin", 32, "",
        ("Ask me about", "the summer I spent on a narrowboat."),
        ("My hidden talent", "is remembering every film I've seen."),
    ),
    _card(
        "Sasha", 27, "",
        ("I'm weirdly good at", "spotting a typo from across the room."),
        ("My perfect Sunday", "is a long bath and a longer book."),
    ),
)  # fmt: skip


# Six more pairs, so the evaluation's full night has eight dates with different pairs.
NIGHT = (
    REHEARSAL_PAIR,
    NEXT_PAIR,
    Pair(
        _card(
            "Jonah", 33, "he/him",
            ("I'm weirdly good at", "guessing what's for dinner from the smell on the stairs."),
            ("Ask me about", "the sourdough starter I've kept alive for six years."),
        ),
        _card(
            "Elena", 30, "she/her",
            ("My perfect Sunday", "is a long swim, then a crossword in the sun."),
            ("I claim I can", "beat anyone at table tennis."),
        ),
    ),
    Pair(
        _card(
            "Kit", 27, "they/them",
            ("My hidden talent", "is whistling any tune after hearing it once."),
            ("The way to win me over", "is a handwritten note."),
        ),
        _card(
            "Marcus", 35, "he/him",
            ("Ask me about", "the time I tried stand-up comedy."),
            ("My perfect Sunday", "involves a market, a big breakfast and no plans."),
        ),
    ),
    Pair(
        _card(
            "Nora", 34, "she/her",
            ("I claim I can", "keep any houseplant alive."),
            ("Ask me about", "learning to sail last summer."),
        ),
        _card(
            "Aisha", 28, "she/her",
            ("I'm weirdly good at", "remembering everyone's birthday."),
            ("The way to win me over", "is a terrible pun."),
        ),
    ),
    Pair(
        _card(
            "Ravi", 31, "he/him",
            ("My hidden talent", "is a perfect omelette in two minutes."),
            ("Ask me about", "my short career playing records at weddings."),
        ),
        _card(
            "Ben", 29, "he/him",
            ("I claim I can", "name any bird by its song."),
            ("My perfect Sunday", "starts with a run and ends with a film."),
        ),
    ),
    Pair(
        _card(
            "Lucia", 26, "she/her",
            ("Ask me about", "the band I was in at fifteen."),
            ("I'm weirdly good at", "packing a suitcase."),
        ),
        _card(
            "Tom", 27, "he/him",
            ("The way to win me over", "is a good playlist."),
            ("My hidden talent", "is drawing a face in under a minute."),
        ),
    ),
    Pair(
        _card(
            "Imogen", 33, "she/her",
            ("My perfect Sunday", "is a bike ride to the coast."),
            ("I claim I can", "make a decent curry from whatever's in the fridge."),
        ),
        _card(
            "Dev", 32, "he/him",
            ("Ask me about", "the year I learned to juggle."),
            ("The way to win me over", "is a long walk with no destination."),
        ),
    ),
)  # fmt: skip
