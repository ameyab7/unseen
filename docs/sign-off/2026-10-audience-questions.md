# Sign-off: reading an approved audience question when the model fails

Status: proposed. Waiting for the product owner. Until then the switch
`READ_APPROVED_QUESTION_ON_FAILURE` in `services/nova/nova/script.py` stays `False`.

## The line

> From the room: <the approved question, exactly as the moderator approved it>

For example: "From the room: Theo, what's your lucky number?"

## When it's used

Only when all of these are true:

- A moderator approved the question in the console.
- The model failed to write Nova's own reading of it (too slow, an error, a refusal, or a
  line a guardrail rejected).
- The framed line passes the same guardrails as a model line for that moment
  (`from_the_room`): no unknown names, no personal details, no decision talk, nothing
  about looks, and the length limit. "From the room: What would your friend Kate say about
  you?" fails on the unknown name, so the room hears today's fallback instead.

Otherwise Nova says today's fallback: "Room, keep the questions coming. Maya, Theo, back to
you."

## Why

Today a model failure loses the question: the listener who asked it, and the moderator who
approved it, hear a generic line instead. Decision 0005 says fallbacks never use free text,
so that injected text can't reach the room through them. This is the one exception
proposed: text a human moderator approved and the guardrails checked, read word for word.

## What it changes

Decision 0005's rule that fallbacks are built only from first names and ages gets this one
exception (marked proposed there until sign-off).

## Sign-off

- Product owner: ____________________
- Date: ____________________
