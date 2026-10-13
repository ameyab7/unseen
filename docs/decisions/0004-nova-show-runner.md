# 0004: Nova's show runner

Date: 2026-10-13. Status: accepted.

## Decision

- **A deterministic runner walks the running order.** `services/nova/nova/show_runner.py`
  takes the current time and room events (speech, need a moment, a dater leaving, the
  moderator ending, audience questions, the lightning unlock) and returns cues: lines for
  Nova, sounds and room events. It never calls a model or the network, so every rule is
  tested on a fake clock. The language model only writes lines inside the beats.
- **Nova never sees who chose what.** The runner opens the decision window, closes it and
  asks for one answer, mutual or not. In production that is `resolve_date` in Postgres; in
  rehearsals an in-memory desk with the same rule (mutual only if both chose reveal; no
  answer counts as keep it unseen).
- **The result always comes when the window closes**, even if both decided early. Otherwise
  the timing would tell a dater who chose quickly whether the other one answered. The
  room's verdict comes after the result line, never before both decisions are locked.
- **Safety-critical lines are fixed, never generated:** the result, a dater leaving, the
  moderator ending the date, the need-a-moment cover and the invitation (`nova/script.py`).
  Lines not in the brand book or prototype are marked as assumptions until signed off.
- **Need a moment holds the show clock** for up to 30 seconds, or until that dater is
  back, so nobody loses their last word or decision time. Dates run slightly long instead.

## Consequences

- When cues are published to the room, their shapes get zod schemas in `packages/shared`
  and the Python classes follow them.
- The end reasons (`completed`, `dater_left`, `moderator_ended`) are the values for the
  `dates.end_reason` column when the schema is written.
