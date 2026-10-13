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
