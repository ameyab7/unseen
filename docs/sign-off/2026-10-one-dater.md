# Sign-off: fallbacks for one dater

Status: proposed. Waiting for the product owner. Until then they are not in
`services/nova/nova/script.py`, and when the model fails on a one-dater silence prompt,
Nova says nothing.

## When they're used

The moderator resumed the show without a dater who asked for a moment (decision 0004).
While that dater is away, a long silence in free talk brings a prompt for the dater who's
still on air. The model writes it from a prompt that has only that dater's card. If the
model fails, or writes a line a guardrail rejects, one of these would be said instead,
taken in turn like the other silence fallbacks. Today's silence fallbacks all name both
daters, so none of them can be used.

## The lines

`{name}` is the first name of the dater who's on air, exactly as on their card. Both are
built only from the first name, like every other fallback, and neither mentions the
other dater or why they're away.

1. "{name}, here's one just for you: what's the best thing that happened to you this week?"
2. "{name}, one for you: what's a small thing that reliably makes your day?"

Both pass the guardrails for a silence prompt (checked with Theo, Will and Robin as the
name): 84 to 85 and 69 to 70 characters, one question each.

## An audience question, and a reply, for one dater

While a dater is away, an approved audience question and a reply to the dater who spoke to
Nova go only to the dater who's here (decision 0004). If the model fails, today's
fallbacks name or address both daters ("Room, keep the questions coming. Maya, Theo, back
to you."; "Back to you two."), so Nova says nothing until one of these is signed off:

3. Audience question: "Room, keep the questions coming. {name}, back to you."
4. Reply: "Good question. I'll hand that one back to you, {name}."

Both pass the guardrails for their moments (a test checks them with Theo, Will and Robin as
the name).

## Sign-off

- Product owner: ____________________
- Date: ____________________
- Approved lines (if changed): ____________________
