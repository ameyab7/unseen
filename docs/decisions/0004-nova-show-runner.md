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
  is hidden when fewer than 20 people voted, and rounded to the nearest 10%: a percentage
  of a handful of listeners says too much about each of them, and the daters see it again
  in their recap ("81% of the room said yes"). *Proposed, until the product owner agrees*:
  the threshold was 5 votes and the share exact; the code already uses 20 and 10%.
- **Safety-critical lines are fixed, never generated:** the result, a dater leaving, the
  moderator ending the date, the need-a-moment cover and the invitation (`nova/script.py`).
  Each names its source; lines not in the brand book or prototype need the product
  owner's sign-off.
- **Nova reads only moderator-approved audience questions.** Passing the automatic screen
  for kindness and personal details is not enough; anything not approved is dropped. An
  approved question the screen still catches is skipped without a word, never declined
  aloud: the console gets a `QuestionSkipped` code, and the next approved question takes
  the slot (decision 0005).
- **Need a moment holds the show clock** until that dater is back, so nobody loses their
  last word or decision time; dates run slightly long instead. "And we're back." is said
  only when the dater who paused actually returns. If they aren't back after 30 seconds,
  Nova says "Bear with us a little longer, room." once, the show stays on hold, and the
  moderator is alerted and chooses: keep waiting, resume without the dater (Nova says
  nothing), or end the date. While a dater is away after a resume, their first-question
  prompt and last word are skipped (the other dater's turn comes on time, never earlier),
  and a long silence brings a prompt for the dater who's on air only, written by the
  model from that dater's card alone; if the model fails, Nova says nothing, since every
  silence fallback names both daters (one-dater fallbacks wait for sign-off in
  `docs/sign-off/2026-10-one-dater.md`). Audience questions and replies to Nova go to
  the dater who's here too: the model is given only their name, a line naming the absent
  dater is rejected, and since today's fallbacks name or address both daters, Nova says
  nothing if the model fails (one-dater lines wait in the same document). With both
  daters away, audience questions wait. If the dater comes back while the date is live,
  Nova says "And we're back.", which is true then (before, a return after a resume got
  nothing). Their decision is unchanged: they can still decide in the app, and no answer
  counts as keep it unseen. After a report, the reporter isn't welcomed back: their date
  ended in the app. There is no limit on how often a dater can pause, but the
  moderator is also alerted when the same dater pauses a second time (counted per dater).
  Alerts go to the moderator console only, never to the room.
- **Holds can overlap** (2026-10-16, from the review of main at bfaed17). A dater who asks
  for a moment while the show is already held (the other dater's moment, a conduct pause)
  gets their own pause as soon as that hold ends, rather than being put back on air.
  A dater the moderator resumed without who comes back during another hold is welcomed
  only once that hold ends, since "And we're back." must be true. Every dater the
  moderator resumes without stays away until they come back: a later resume never
  brings an absent dater back on air, and with both away nobody gets a silence prompt.
  A lightning unlock that comes in during a hold is announced once the date is on air
  again, never over the pause.
- **A report pauses the date** until the moderator resumes or ends it (the prototype's
  report sheet: "Nova pauses the date and a live moderator listens in", "Pause and
  report"). The moderator is alerted first (`dater_reported`); then, if a pause can start,
  Nova stops mid-line and covers with the need-a-moment line and talk to the room, never
  naming the reporter or the report. The show clock holds as for a need a moment, with the
  usual 30-second alert and "Bear with us", but the reporter's own return doesn't end it,
  since their date ends in the app: only resume or end does. A report never counts as a
  repeated pause. If the show is already held (the other dater's moment, a conduct pause),
  that hold becomes the report's: the other dater's return no longer lifts it, and a
  resume leaves the reporter off air. Otherwise (before the date, deciding, after it), the
  alert is all.
- **Alerts never depend on the show clock.** Daters, the moderator and the room can act at
  any moment of the night, before a date starts and after it ends, and nothing they do
  raises. Off air, a distress check or a report still reaches the moderator, and nothing
  else happens; an approved question or a lightning unlock that comes in before the date
  starts is kept for it (the unlock is announced after the doors intro, so a paid unlock
  is never lost). The moderator's end, or a dater leaving, before the date starts ends it
  at once, with nothing said and nobody named: the moderator's one real control is never
  lost while set pieces are prepared. A beat that fails (in production, the room poll or
  the decision desk behind it) is tried again a second later, up to five times, and the
  console hears of each failure (`BeatFailed`, then `gave_up`, when the moderator decides
  what happens next; what Nova says then is the product owner's call), so no beat, and no
  alert asked for in the same instant, is lost. A poll that fails only loses the room's
  verdict. Ending the date while the daters decide closes the decision window, and the
  date reports Finished only once Next up is said, even if it waited for a dater; nothing
  that waited is said after Finished. An approved audience question that was waiting for
  a dater when a pause began goes back to the front of the queue, uncounted. The number
  of lightning questions must fit before the last word, a full gap each, or the runner
  refuses the timing; the signed-off unlock line says "Three", so a test pins the count.
- **The lightning unlock meter closes at 5:30**, or as soon as the round is unlocked, or
  when the date ends early (the runner's `LightningMeterClosed` cue). No listener can pay
  toward an unlock that will be ignored. The gesture work must follow this: once the meter
  closes, gestures stop counting toward the unlock, and the app says so before anyone pays.
- **No dead air while the show is held.** A pause (need a moment, a distress pause or a
  report) used to get one line to the room, then about 25 s of silence until "Bear with
  us", then silence for as long as the moderator took. Now Nova talks to the room every
  `cover_every` seconds (10), at most `cover_lines` lines a hold (3, the first with the
  pause), written by the model from the show facts; if the model fails, the room-talk
  fallback is said at most once a hold, and after that Nova says nothing rather than
  repeat it. Once the lines run out, or at the 30-second alert if that's first, the
  recorded fallback segment plays (`PlayFallbackSegment`), and when the hold ends (the
  dater is back, the moderator resumes or ends the date) it stops (`StopFallbackSegment`)
  before Nova speaks again. Talk to the room is told its situation explicitly
  (`RoomFacts.situation`: a pause, the daters deciding, or a date that ended early),
  instead of guessing it from the segment.
- **After a dater leaves,** Nova says the approved leave line, then one line thanking the
  room for listening (the ended-early situation: it never says who left or why), and Next
  up comes `next_up_after_leave` later (12 s; it reused the 30 s after a result, which
  left the room in silence). With no next pair, the fallback segment plays. Six more
  room-talk fallbacks wait for sign-off in `docs/sign-off/2026-10-room-talk.md`.
- **After the moderator ends a date,** Nova says only the approved closing line, then the
  recorded fallback segment from the technical plan plays until the moderator chooses what
  happens next. The room never sits in silence. (Not yet in the Agents Playground
  rehearsal, which plays Nova's lines only: no sounds, voice intros or fallback segment;
  see `docs/spike-results.md`.)
- **Nova never talks over a dater.** The brand book says sounds never play over someone
  speaking, and beats used to fire at their exact show time. The runner hears the voice
  detector (`speech_started`, `speech_stopped`, through the on-air listener): while a
  dater is speaking, a beat's lines and sounds (the first question, audience and
  lightning questions, the last word, the time's-up sound and line, the lightning-unlocked
  line) wait until nobody is, or at most `talk_over_grace` (5 s, the same in rehearsals),
  in their order. State changes in the same beat go on time: the segment, the decision
  window, the poll, the meter and the desk. Safety lines and the result never wait (the
  kind line, the need-a-moment cover, "Bear with us", "And we're back", a dater leaving,
  the moderator ending, the result). What's waiting is dropped if a pause starts or the
  date ends. No silence prompt comes while a dater is speaking, and silence counts from
  when they stop. Whoever plays the cues asks again before each line that waits
  (`waits_for_speech`): a beat's lines go out together (the result, the outro and the
  invitation), and a dater often starts talking while the first plays, so the next one
  waits for them too, up to the same grace, and is measured from when they finished.
- **Fairness:** who answers first (first question, last word) is chosen at random for each
  date, and the long-silence prompt is an open one to both daters, so nobody is singled out
  for being quiet. *Proposed, for the product owner*: the brand book's example silence
  line addresses one dater with a detail from their card ("Theo, you say you can parallel
  park anything. Defend that."), and CLAUDE.md says the design system wins. Nova keeps
  this rule (both daters) but now builds the question on a light detail from each card, so
  it keeps the brand book's specificity without singling anyone out; whether to follow the
  example instead is the product owner's call.

## Consequences

- When cues are published to the room, their shapes get zod schemas in `packages/shared`
  and the Python classes follow them.
- The end reasons (`completed`, `dater_left`, `moderator_ended`) are the values for the
  `dates.end_reason` column when the schema is written.
- The questions table needs a moderator-approved status distinct from the automatic screen.
- The moderator console shows `dater_reported` as its most urgent alert. It also needs the
  two pause alerts ("not back after 30 seconds" and "second pause by the same dater"), the
  three distress alerts ("a dater wants to stop", "a dater may be distressed" and
  "couldn't check what a dater said: listen in", decision 0005), and for a dater who isn't
  back or who reported, three controls: keep waiting,
  resume without them, and end the date (the runner's `moderator_keep_waiting`,
  `moderator_resume` and `moderator_end`). It also needs a control to stop the fallback,
  the conduct alerts of decision 0006 (with `show_held` when a conduct pause runs past 30
  seconds), and `BeatFailed`: a beat retrying, or given up on, after which the moderator
  ends or resumes the date.
- The audio side plays the fallback segment on `PlayFallbackSegment` and stops it on
  `StopFallbackSegment`, before Nova's next line.
