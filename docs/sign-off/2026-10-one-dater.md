# Sign-off: silence fallbacks for one dater

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

## Sign-off

- Product owner: ____________________
- Date: ____________________
- Approved lines (if changed): ____________________
