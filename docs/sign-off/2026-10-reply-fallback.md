# Sign-off: a neutral reply fallback

Status: proposed. Waiting for the product owner. Until then `REPLY_FALLBACK` in
`services/nova/nova/script.py` stays as it is.

## Why

When a dater speaks to Nova and the model fails (too slow, an error, or a line a guardrail
rejected), Nova says today's fallback: "I'm the AI host, so I'll stay out of that one. Back
to you two." It sounds as if the dater asked something off limits, when the model only
timed out. (A dater who may be distressed never hears it: after a maybe or a failed check,
Nova always says "Take all the time you need.", signed off on 2026-10-13.)

## The lines

Neutral, naming no one, built from nothing a dater said. Each passes the guardrails for a
reply (`tests/test_sign_off.py`).

1. "Good question. I'll hand that one back to you two."
2. "I'll let you two take that one. Over to you."
3. "That one's yours to answer, not mine. Back to you both."

One replaces today's line, or all three take turns like the other fallbacks.

## Sign-off

- Product owner: ____________________
- Date: ____________________
- Approved line(s): ____________________
