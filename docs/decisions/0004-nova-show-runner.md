# 0004: Nova's show runner

Date: 2026-10-13. Status: accepted.

## Decision

- **A deterministic runner walks the running order.** `services/nova/nova/show_runner.py`
  takes the current time and room events (speech, need a moment, a dater leaving, the
  moderator ending, audience questions, the lightning unlock) and returns cues: lines for
  Nova, sounds, room events and moderator alerts. It never calls a model or the network,
  so every rule is tested on a fake clock. The language model only writes lines inside the
  beats.
- **Nova never sees who chose what.** The runner opens the decision window, closes it and
  asks for one answer, mutual or not. In production that is `resolve_date` in Postgres; in
  rehearsals an in-memory desk with the same rule (mutual only if both chose reveal; no
  answer counts as keep it unseen).
- **The result always comes when the window closes**, even if both decided early. Otherwise
  the timing would tell a dater who chose quickly whether the other one answered. The
  room's verdict comes after the result line, never before both decisions are locked, and
  is hidden when fewer than 5 people voted: a percentage of a handful of listeners says
  too much about each of them.
- **Safety-critical lines are fixed, never generated:** the result, a dater leaving, the
  moderator ending the date, the need-a-moment cover and the invitation (`nova/script.py`).
  Each names its source; lines not in the brand book or prototype need the product
  owner's sign-off.
- **Nova reads only moderator-approved audience questions.** Passing the automatic screen
  for kindness and personal details is not enough; anything not approved is dropped.
- **Need a moment holds the show clock** until that dater is back, so nobody loses their
  last word or decision time; dates run slightly long instead. "And we're back." is said
  only when the dater who paused actually returns. If they aren't back after 30 seconds,
  Nova says "Bear with us a little longer, room." once, the show stays on hold, and the
  moderator is alerted and chooses: keep waiting, resume without the dater (Nova says
  nothing), or end the date. There is no limit on how often a dater can pause, but the
  moderator is also alerted when the same dater pauses a second time (counted per dater).
  Alerts go to the moderator console only, never to the room.
- **The lightning unlock meter closes at 5:30**, or as soon as the round is unlocked, or
  when the date ends early (the runner's `LightningMeterClosed` cue). No listener can pay
  toward an unlock that will be ignored. The gesture work must follow this: once the meter
  closes, gestures stop counting toward the unlock, and the app says so before anyone pays.
- **After the moderator ends a date,** Nova says only the approved closing line, then the
  recorded fallback segment from the technical plan plays until the moderator chooses what
  happens next. The room never sits in silence. (Not yet in the Agents Playground
  rehearsal, which plays Nova's lines only: no sounds, voice intros or fallback segment;
  see `docs/spike-results.md`.)
- **Fairness:** who answers first (first question, last word) is chosen at random for each
  date, and the long-silence prompt is an open one to both daters, so nobody is singled out
  for being quiet.

## Consequences

- When cues are published to the room, their shapes get zod schemas in `packages/shared`
  and the Python classes follow them.
- The end reasons (`completed`, `dater_left`, `moderator_ended`) are the values for the
  `dates.end_reason` column when the schema is written.
- The questions table needs a moderator-approved status distinct from the automatic screen.
- The moderator console needs the two pause alerts ("not back after 30 seconds" and
  "second pause by the same dater"), the two distress alerts ("a dater said they want
  to stop" and "a dater may be distressed", decision 0005), and for a dater who isn't
  back, three controls: keep
  waiting, resume without them, and end the date (the runner's `moderator_keep_waiting`,
  `moderator_resume` and `moderator_end`). It also needs a control to stop the fallback.
