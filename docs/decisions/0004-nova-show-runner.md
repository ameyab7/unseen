# 0004: Nova's show runner

Date: 2026-10-13. Status: accepted.

## Decision

- **Conversational mode supersedes the VAD-only turn rules below** (2026-10-26,
  decision 0012). Local floor signals run independently of the frozen safety check.
  Thinking, handovers and clarification keep an opportunity open; silence is bounded
  at 20 seconds rather than treated as a pass at six. Transitions need a quiet boundary,
  including after the old speech cap. Extra waiting has a 60-second discretionary budget;
  free talk and the optional second room question absorb delay before protected last
  words or decision time. Legacy callers can opt out for regression comparison.

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
- **A hold starts and ends cleanly** (2026-10-17, from scripted dates run against the real
  model on main at 9edcb48). A dater's own moment stops Nova mid-line, as a report and a
  distress pause already did, and what was queued is dropped as for those: before, Nova
  finished an eight-second question to both daters before "Let's take a short
  breather." Talk to the room and "Bear with us" that cover a hold and haven't been said
  when it ends (the dater is back, the moderator resumes or ends the date) are never
  said, even if Nova had already picked one up and the model was still writing it, and
  one that's playing stops mid-line, so "And we're back." comes at once: before, they
  played after the resume, for twenty seconds of a date that had moved on, and a line
  already playing held the welcome back about five seconds.
  While a dater is away after a resume, the other dater's first question and last word,
  written before the date for both, are checked again as for the dater who's here; one
  that names the absent dater gives way to its approved fallback
  ("Theo, the last word is yours."), which names only the dater on air. Before, Theo was
  asked what he hoped Maya would discover about him while she was away.
- **Each dater has time to answer** (2026-10-18, from a LiveKit rehearsal heard live). Turns
  went by the clock alone: when Nova ran late (the lightning-unlocked line queued behind the
  doors intro), it asked Theo his first question and asked Maya hers 0.2 s later, before
  Theo could say a word. Now whoever plays the cues reports each of Nova's lines as it
  finishes (`ShowRunner(paced=True)`, `line_done`; the rehearsal does, for lines cut off or
  dropped too). A beat that starts a dater's turn (the first questions, the audience
  questions, the lightning questions, the last words) or ends them ("That's time.") then
  waits until Nova has finished asking the dater before and they've had
  `answer_window` (6 s, not scaled for rehearsals) to start answering, and while a dater
  is still speaking, and until nobody has spoken for `turn_gap` (1.5 s, not scaled):
  before, the next line went out over them after the 5 s talk-over grace, cutting a
  dater off five seconds into an eight-second answer, and the voice detector hears the
  half-second pause between two sentences as the end of an answer. It also waits until
  everyone the turn before asked has started answering (the dater named, or both for an
  audience or lightning question): Nova asked the next question 1.5 s after the first
  dater finished, as the second began. One who hasn't started within `answer_window` of
  the last person to speak is passed over. An audience question, which breaks into the
  daters' own conversation, waits for `lull` (2.5 s of quiet) instead of `turn_gap`. While a turn waits
  the show clock stops, as in a hold, so everything after it moves too: a late date runs
  a few seconds long, never skips a turn, and the decision window is never cut short (a
  first version moved only the turn, and a late "That's time." left the daters 1.8 s to
  decide). After 20 s of waiting (a line never reported played, daters who never pause
  long), counted from when Nova finished asking (counted from when the turn came due, it
  ran out before Theo had answered), a turn stops waiting for the lull and the answers but
  still needs the turn gap, so it never cuts a dater off (a first cap ran out while Theo
  was mid-answer, a second went the instant Maya stopped, as Theo began); only 30 s of
  speech without a break, a TV the voice detector hears, is talked over. When a dater and Nova take the floor at
  the same moment after a silence (Nova's voice takes about 0.5 s to start and the voice
  detector as long to hear the dater, so neither knew), a dater who starts within 1.5 s of
  Nova handing a line to its voice goes first: Nova stops, lets them finish (the turn gap
  of quiet, not their first pause: a first version came back in between two of Theo's
  sentences), and says the line again, once (`yielded` in the log). Only lines that wait for a dater anyway, never
  a reply, a safety line or the result. **Later in a line too** (2026-10-21): before, a
  dater who began more than 1.5 s into a line was talked over for the rest of it, though
  Nova's voice can be stopped at any point (`AgentSession.interrupt(force=True)`, as a
  safety stop does). Now a dater who begins anywhere in such a line and keeps going for
  0.3 s (`YIELD_HOLD`, so a "mm-hm" or a cough never does) stops Nova, and after the same
  turn gap Nova goes on.
  **Hardened after a real LiveKit run** (2026-10-22, `nova/yield_live.py`, below):
  - *Up to three stops a line* (`MAX_YIELDS`), the part Nova goes on with included. Before,
    a line stopped once at most, so a dater who started again, or the other dater, was
    talked over because Nova had already stopped once. The waits after a line's stops share
    one `SPEECH_CAP` from its first stop, and past it the line no longer stops: a TV that
    keeps starting and stopping can't hold one line back for more than 30 s.
  - *Going on only from where the cut is known* (`after_a_cut`). Nova's voice (Cartesia)
    now times its words (`add_timestamps`), and LiveKit's transcript synchronizer turns those
    timings, against the audio actually sent, into the words that had played when the line
    stopped (`PlaybackFinishedEvent.synchronized_transcript`). Its text output goes nowhere,
    so nothing is published to the room (`agent.time_played_words`). With those words, Nova
    says the sentence the first unplayed word is in and every one after it (`resumed`), or
    nothing if every word had played. Without them (a voice that doesn't time its words),
    nothing is guessed from a speaking rate, as `rest_of` did before: a line cut within
    `RESTART_WITHIN` (2 s) is said again from its start (`restarted`), so at most 2 s is
    heard twice, and a later cut drops the rest (`dropped`).
  - *A newer line goes first.* If a line was queued after the cut (a reply to what the
    dater said, "Let's keep it kind."), the rest of the stale line is dropped
    (`superseded`); lines queued before the cut, the rest of a beat, don't count. Before,
    the rest played first, and a reply waited behind it.
  - A line waiting to go on is still dropped if Nova is told to stop (`stopped`). Safety
    lines all come with a stop cue, which ends the wait at once; the tests check the kind
    line after a cut comes with only the grace and the voice's start.
  - *A bug the fake clock hid*: the hold compared the clock read when the voice detector
    heard the dater with a second reading a moment later. On a real clock the two never
    match, so no dater ever stopped Nova after a line's first 1.5 s. Every fake-clock test
    passed; the first LiveKit run showed it. It now reuses the one reading, with a test on a
    clock that moves between reads.
  The `yielded` event carries the moment, `stop_ms` (from when the voice detector heard the
  dater start to when the line had stopped, at the agent), `played_ms`, the sentence (`-`
  when not known) and how many there were, and `then`; the report sums them up. Options weighed
  (livekit-agents 1.8.6): LiveKit's own barge-in (`InterruptionOptions`, `mode: "vad"`)
  pauses after 0.5 s and resumes the exact audio only for a false interruption, cancelling
  the line on a real one, inside turn-taking built for an agent with a language model,
  which this session hasn't got; its `"adaptive"` mode and the cloud turn detector would
  send daters' audio to another LiveKit service; the local end-of-turn model
  (`turn-detector-v1-mini`, through `livekit-local-inference`, already installed with
  livekit-agents) judges when a dater has finished, not when they began, so it could only
  shorten the wait before Nova resumes, a separate change to measure.
  **The yield rehearsal in LiveKit** (`uv run python -m nova.yield_live`, Docker only,
  LiveKit credit): Nova's side is a rehearsal's (AgentSession, SessionSpeaker, Rehearsal),
  the date not started, playing a fixed four-sentence test line. A second participant plays
  the dater: it publishes speech synthesized in another voice and receives Nova's audio over
  WebRTC. Both run on one machine with one clock. The matrix (`nova/yield_matrix.py`) has no
  interruption, cuts early, halfway, late and twice in one line, pauses between the dater's
  sentences, a TV at -12 dB, a cut as the line ends, uplink audio sent 0.4 s late, a request
  to stop, and Nova's own audio looped into the microphone with and without WebRTC's echo
  canceller (`rtc.AudioProcessingModule`, as a phone runs it). The report (numbers only,
  services/nova/rehearsals/yield-*.txt) times each stop from the dater's first frame to the
  last of Nova's audio received, and at the agent; it counts missed and false stops,
  duplicate lines, and where the agent placed each cut against the pauses heard. A run
  counts only with the proof LiveKit gives: a room id, an agent participant and its track,
  Nova's audio received over WebRTC, the agent's voice detector hearing the dater's track,
  and Nova's line heard in every trial.
  Results so far, 2026-10-22, all of it **not yet enough to call yielding validated**:
  - A smoke run, one trial of each kind, before the hold fix: real audio both ways.
    - Cut as Nova starts: Nova stopped 485 ms after the dater's first frame, as heard
      (agent 400 ms, voice detector 394 ms), and went on from the right sentence.
    - Every mid-line cut was missed: the hold bug above.
    - Nova's own audio looped back with nothing cancelling it stopped the line 3 times in
      one trial. Through WebRTC's echo canceller it stopped it none.
    - A TV at -12 dB through the line stopped Nova once, and Nova went on after: speech
      the voice detector hears counts as a dater's, up to the cap (above).
    - A request to stop reached the moderator's alert 2.4 s after the dater began.
  - The full run (51 trials) is invalid. From the sixth trial on, LiveKit Inference refused
    every voice request with 429 Too Many Requests. That was account-wide (Cartesia and
    Deepgram alike) and still so 12 minutes later, so Nova never spoke in 46 trials. Its
    first report read "0 missed" all the same, so a run now counts only if Nova's line
    reached the dater in every trial. Its one early cut: 536 ms heard.
  **The full run, 2026-10-23 10:23 UTC: valid.**
  - All 51 trials ran with real audio both ways in a fresh room, no voice errors, and
    no 429 (a one-request voice probe passed first).
  - The audio code was unchanged since 5dbf276, and nothing was changed during the run. The
    report and a per-trial table are in services/nova/rehearsals/yield-2026-10-23-102308.*.
  - The dater's clips start with 0.24 s of silence (0.21 s for the stop clip). Times below
    are from their first speech; the report's own times include that silence.
  - Genuine cut-ins: 37 of 37 stopped Nova, and none was missed.
    - The voice detector heard the dater a median of 176 ms after they began speaking
      (worst 246 ms).
    - Nova's voice stopped at the agent a median of 199 ms after the dater began when they
      cut in within the first 1.5 s (5 trials), and a median of 478 ms (p90 528, worst
      548) later in a line, which includes the 0.3 s hold.
    - The dater heard Nova's audio end a median of 562 ms after they began (p90 601, worst
      681). In seven cut-ins the dater began near the end of one of Nova's sentences, so
      they heard it end sooner, or before the stop.
    - Cuts came 10 in the first third of the line, 22 in the middle third and 5 in the
      last.
  - The second cut-in in a line stopped Nova too, 5 of 5. Nova went on each time, and the
    room never heard a line twice.
  - Nova never went on between the dater's sentences (3 of 3), and a cut as the line ended
    never stopped it.
  - Every yield resumed. Where the sentences fell couldn't be compared: the run found no
    pause length that split the uninterrupted line into its four sentences.
  - The cut, against what the dater heard: median -288 ms, the same as an uninterrupted
    line's -310 ms (the voice's quiet lead-in and tail). The outliers, down to -1,044 ms,
    are cut-ins during a pause between sentences.
  - False stops:
    - Nova's own audio in the microphone with nothing cancelling it: 3 stops in each of 3
      trials, the cap of 3 a line;
    - through WebRTC's echo canceller: none in 5 trials, and none alongside 3 genuine
      cut-ins, which all stopped Nova, about 50 ms slower;
    - a TV at -12 dB: 1 stop in each of 3 trials;
    - silence: none in 5 trials.
  - Safety monitoring:
    - every dater utterance reached the distress check (62 checks, all answered, none
      unchecked), while Nova spoke, after it stopped, and while it waited to go on;
    - a request to stop reached the moderator 3 of 3 times, 2,424 ms after the clip began,
      about 2.2 s after the dater began speaking.
    - Without echo cancelling, Nova's own line was transcribed and checked as the dater's
      words, 3 or 4 times a trial. Through the canceller, once in 5 trials. A line of Nova's
      with warning words would reach the check the same way.
  - Not covered by the matrix: a short "mm-hm" or backchannel (the 0.3 s hold is tested
    only on fake clocks), quiet speech, and one long continuous interruption.
  - All of it is simulated: synthesized speech, simulated echo and the software echo
    canceller, on one machine. No real phone on speaker was tested.
  - The "options a to d" for echo, named in docs/reviews/2026-10-22-nova-hardening.md, are
    not in this decision. The only options it weighs are LiveKit's barge-in modes.
  Release-blocking until a full run passes: mid-line stops and the second stop in a line
  on real audio, and false stops from echo on real phones on speaker. Nova's own audio
  reaches the voice detector only through a dater's microphone: the session listens to one
  remote participant's microphone track and never its own, so nothing on the server can
  tell that echo from the dater's speech. During LiveKit's 3 s echo warm-up only speech
  recognition gets silence (agent_activity.py `push_audio`); the voice detector still
  hears the real audio, and the doors intro can yield. The rehearsal logs each start and stop
  the voice detector hears (`speech`, the seat only), to check turn-taking afterwards. The schedule gives far more time in a full date, so
  this matters when Nova runs late and in rehearsals. Unpaced (the evaluation, the
  runner's own tests), every beat goes by the clock as before.
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
