# Sign-off: fallbacks that quote the daters' cards

Status: proposed. Waiting for the product owner, and for casting to approve cards. Until
both, the switch `CARD_FALLBACKS_ON` in `services/nova/nova/script.py` stays `False`.

## Why

Fallbacks never use card text (decision 0005), so when the model fails the room hears a
bare opening: "Good evening, room. On my left, Maya, 29. On my right, Theo, 31." The brand
book's own opening uses the card ("who says to ask about her worst haircut"). Once a person
at casting has approved a card's text, it can be quoted.

## The templates

The card is quoted in the dater's own words: the app's fixed prompt question with only its
first letter lowercased (never the pronoun I), then the answer exactly as written. No
grammar is rewritten from free text.

- Doors: "Good evening, room. On my left, {left label}, whose card says: {question}
  {answer} On my right, {right label}, whose card says: {question} {answer}"
- Next up: "Up next, {left label}, whose card says: {question} {answer} And {right label},
  whose card says: {question} {answer}"

For example: "Good evening, room. On my left, Maya, 29, whose card says: ask me about my
worst haircut. On my right, Theo, 31, whose card says: I claim I can parallel park
anything."

## When they're used

Only when the switch is on, both cards on the date (or the next pair, for Next up) are
`approved_for_air`, each quoted answer is one sentence and names neither Nova nor the show
(on audio there's no closing quote, so a second sentence would sound like Nova's own
words), and the line passes the same guardrails as a model line for its moment, card
words allowed, within its length. Otherwise today's fallback. The card is quoted as typed,
with curly quotes straightened and line breaks made spaces. A card carrying
injected instructions, even an approved one, fails the guardrails and gets today's line.

## Sign-off

- Product owner: ____________________
- Date: ____________________
- Casting's approval step in place: ____________________
