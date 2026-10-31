# 0012: A conversational floor within the running order

Date: 2026-10-26. Status: implemented for rehearsal; audit fixes 2026-10-27; transitions in
lively conversation 2026-10-31; two-person rehearsal prepared, human validation pending.

## Decision

The deterministic runner still owns activities, order, holds and decisions. Finishing
an answer does not automatically give Nova the floor. `nova/floor.py` keeps asked seats,
a handover or clarification, and a revision invalidating old host work. It keeps no words.
Local English rules recognize explicit thinking, passing, questions to the other dater
and addressing Nova; they never judge answer quality. Unknown speech uses a quiet gap.
Thinking, silence and unanswered handovers get a bounded 20-second opportunity.
Transitions require 2.5 seconds of quiet; active speech is never talked over after the
old 30-second cap. A clarification reply preserves the original dater's opportunity.

The agent defaults `NOVA_CONVERSATIONAL_TURNS` to true. Direct runner/rehearsal callers
opt in, preserving legacy regressions and the benchmark baseline; the candidate opts in.
These are conservative text hints, not general semantic understanding. Indirect and
unnamed handovers, quoted phrases, prosody and recognition errors still need audio tests.

## Detection options compared

| Option | Tradeoff | Decision |
| --- | --- | --- |
| VAD with fixed quiet | No contention; pauses and endings look alike | Rollback baseline |
| Explicit local floor signals | No inference or new recipients; limited understanding | First implementation, with exchange-level tests |
| Gemma classification per utterance | Richer text interpretation; extra serial inference, stale results and safety contention | Require measured benefit and latency budget first |
| LiveKit semantic/audio detector | Purpose-built endpointing; pinned SDK and audio privacy need review | Future audio comparison, not silently enabled |

This is a design comparison, not an audio benchmark. See LiveKit's separate turn and
interruption documentation: https://docs.livekit.io/agents/logic/turns/turn-detector/
and https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/.
Cloud turn detection and adaptive barge-in audio routing remain disabled.

## Timing and optional host words

The product owner chose 60 seconds of discretionary overrun. Safety holds are additional.
Recover delay from free talk, keeping at least 60 seconds in a full date, then omit the
optional second audience question if delay remains. Later beats shift together, keeping
the full decision window and result timing. Both last words get 20 seconds after their
prompts unless passed; all three lightning questions remain when unlocked. The budget
limits discretionary waiting, not an active person's speech: a continuous story may
cross it until a quiet boundary. Strict duration and never interrupting unbounded speech
cannot both be guaranteed.

`NOVA_LIVE_BRIDGES` defaults off pending audio and load acceptance. Eligible prepared
questions may acquire a prefix of at most eight words; the original question follows
unchanged. At most three are attempted, 30 seconds apart, only below 15% observed host
airtime. They are optional, never follow a last word, and are not added to every answer.
Failure or timeout uses the existing question. Prefix and combined line pass guardrails;
meaning checks are mandatory even with `NOVA_MEANING_LIVE` off. Both models must be
allowed this date's cleared context. No dater-derived words enter another date's memory.
New speech cancels an obsolete prefix, preserving the required question. No new fixed
silence-check-in wording is introduced.

The priority gate cannot preempt inference already running. Prefixes stay off until load
trials establish acceptable safety tail latency/timeouts. Frozen safety prompts and
schemas are unchanged; no extra model request is used to understand the floor.

## Rehearsal and acceptance

`NOVA_FULL_REHEARSAL` defaults true; false restores the compressed rehearsal. Set distinct
`NOVA_LEFT_PARTICIPANT` and `NOVA_RIGHT_PARTICIPANT` identities for two people. Both must
be present before opening. Each input session is pinned to its participant; the left
also voices Nova, the right has no audio/text output. Speech, transcripts and typed
commands carry their seat, including overlap: what each person types (a moment, a report,
a decision) is their own, whoever spoke last. `uv run python -m nova.two_person` signs
each person's join token; the run sheet is `docs/rehearsals/two-person-full-length.md`.
Closing either ends the date and cleans up the additional session. Without identities,
one microphone simulates the prompted seat or handover; typed selection overrides it.
This is not production authorization.

Fake-clock tests cover thinking, passing, handovers, clarification, stale work, speech at
a due transition, long speech, time recovery and decision-window duration. Adapter tests
cover pinned tracks and cleanup; prefix tests cover privacy, timeout, rejection, meaning
checks and memory separation. Two-human full-length audio rehearsals remain pending.
Measure interruptions, end-to-first-audio latency where Nova owes a reply, activities,
both elapsed-date and speaking-time host share, and safety median/p95/p99/timeouts with
prefixes off/on. Blindly rate the audio and ask daters about interruptions; never simulate
human ratings. Reports contain only codes and numbers.

Pre-change native tests: 2,109 passed. The ordinary pre-change local-model evaluation
already failed: 3/73 distress cases missed, two false alerts, zero false pauses and zero
unchecked checks; safety median 420 ms, p90 491 ms, worst 1,120 ms. A room-talk candidate
also failed the wrong-segment check. This is not the frozen multi-reload safety report.
These failures must not be reported as passing gates. No worker is restarted here.

The first post-change ordinary evaluation also missed 3/73 distress cases, with two
false alerts, zero false pauses and zero unchecked checks: median 429 ms, p90 499 ms,
worst 1,154 ms. Conduct and meaning gates passed. Several generated live candidates
failed their existing content gates. No improved safety recall is claimed.

`python -m nova.conversation_trial` is an additional local-only synthetic probe, not an
audio or recall test. Its four-prefix trial accepted one prefix (1,421 ms); the others
used their existing questions. Three idle safety probes had median 326 ms; four probes
with prefix work had median 882 ms, with no unchecked or unexpected verdicts. The sample
is too small for tail guarantees and supports keeping prefixes off. Reports are in
gitignored `services/nova/rehearsals/`, with no real daters' words.

## Final verification, 2026-10-27

- Native suite: **2,163 passed**, with zero failures or skips; saved in
  `services/nova/rehearsals/conversation-native.xml`. Ruff lint and formatting pass.
  Cleanup also has a regression proving an already-closed voice session cannot prevent
  date memory from closing or stop the disconnect report from being saved.
- The 42-case ordinary evaluation passes the new clarification case (603 ms model time,
  not end-to-audio latency). Existing content gates still reject two generated candidates.
  Safety remains 3/73 missed, two false alerts, zero false pauses/unchecked checks;
  median 426 ms, p90 504 ms, worst 1,112 ms. Conduct and meaning gates pass. Safety
  prompts, schemas and freeze digests are unchanged.
- The eight-date repetition trial (127 lines) fails its existing opening-repetition
  thresholds. This is not a passing release gate and is not evidence of human preference.
- The latest four-prefix probe accepts two prefixes (1,405 and 1,561 ms), falling back
  for two. Idle safety probes: median 251 ms, max 549 ms (three probes); with prefix work:
  median 954 ms, max 1,165 ms (four probes). No unchecked/unexpected probe verdicts.
  These small samples do not measure recall or establish tail guarantees; prefixes
  remain disabled by default.
- Final Docker validation completed after restarting Docker Desktop: **2,172 passed,
  33 skipped**, saved in `services/nova/rehearsals/conversation-docker.xml`. The Linux
  engine is responding (29.7.2); the final snapshot includes the LiveKit adapter tests.
  Windows security settings were not changed.
- A full-length rehearsal with two actual people, blind audio ratings, and worker
  restart remain pending. No live room was opened and no human ratings were simulated.

## Audit fixes and the two-person rehearsal, 2026-10-27

An audit against the code found four defects that would have spoiled a two-person
rehearsal. Each is fixed, with a regression test:

- **Any sound cancelled Nova's reply.** Now a conversational reply waits up to the
  talk-over grace for the other dater, then is dropped rather than said over them; it
  stops only for sustained speech, as other lines do; and it is dropped only once the dater
  who asked starts again and says more than an acknowledgement. The other dater's laugh no
  longer cancels a pending clarification, so the asker's 20 s run from Nova's answer.
- **The kind line could be lost.** "Take all the time you need." after a maybe or
  unchecked verdict is said whatever either dater says next, as before this decision.
- **Overlapping speech hid a dater.** Each seat's own stretch of speech decides whether
  Nova stops; the other dater's "mm" no longer resets it.
- **Typed commands went to whoever spoke last.** With two people, each person's moment,
  report, leave or decision is their own seat's, and the right participant can type.

The report adds segment times, each last word's time, the decision window, interruptions
(starts under Nova, stops, lines started over a dater), Nova's share of the time anyone
spoke, and distress p95, p99 and timeouts. `uv run python -m nova.two_person` signs the
two join tokens offline; `docs/rehearsals/two-person-full-length.md` is the run sheet.

Still open, measured rather than fixed: "you go first" gives away the deferring dater's
turn; host speech preempted by new speech waits for the floor's 20 s; an answer addressed
to Nova by name counts as a clarification; once the budget is spent, the other dater's
"That's it." ends a protected last word; the budget counts recovered time and waits for
speech; any delay drops the second room question; locked lightning slots stop the clock;
with prefixes on, a superseded prefix loses its question. Transitions wait for 2.5 s of
quiet with no cap: in a synthetic two-seat dry run (scripted seats and a fake model, not
human validation), back-and-forth with sub-second gaps held the room's questions 150 s
late, and the date ran about 110 s over, beyond the 60 s budget. Order, both last words
(20.1 and 32.3 s), the 60.0 s window and a mutual result typed from each seat were right.

Tests after the fixes: native **2,174 passed**; Docker **2,183 passed, 33 skipped** (each
skip a file the image doesn't hold; those pass natively); ruff lint and formatting pass.
**Human validation is pending**: no two-person rehearsal has run. Optional prefixes stay
off.

## Transitions in lively conversation, 2026-10-31

A scheduled transition (a turn beat: a first question, a question from the room, the
lightning round, a last word, "That's time") waited for 2.5 seconds of quiet, with no cap.
Back-and-forth with half-second gaps never leaves that much, so it waited for ever. A
closing-phrase rule alone would not fix it: 1.5 seconds never comes either. A follow-up
review the same day settled how pending age and protections interact, when a question's
answer window opens and how transcript timing holds an opening. This section is the result.

### The quiet a transition needs

`ShowRunner.quiet_needed` decides it. The runner uses it before it commits the beat, and
the player again before the voice and after a yield:

| Pending age | Closing phrase | Quiet needed |
| --- | --- | --- |
| Under 15 s | No | The usual gap: the 2.5 s lull (1.5 s for the second last word) |
| Under 15 s | Yes | 1.5 s, the turn gap |
| 15 s or more | Either | 0.4 s |

`Timing.transition_grace` (15 s) and `Timing.brief_opening` (0.4 s) are initial tuning
values, not validated by listening, and don't scale in the compressed rehearsal. Past the
grace, no closing phrase is needed. The run loop wakes for the runner's next opening
(`ShowRunner.next_opening`), because a 0.4 s opening in a half-second gap often falls
between two 0.25 s ticks. Set pieces are written before the date, so the line is ready at
the opening. No model bridge is used, and `NOVA_LIVE_BRIDGES` stays off.

### Pending age and protections are separate

- **The age** counts from the first time the beat is due, a time kept until the beat goes,
  on the runner's wall clock. The quiet counts separately, from the latest speech. The
  transition is the beat's action and argument, so a retried beat is the same one.
- **What counts toward it:** speech, a story, thinking, a handover, a clarification, a dater
  who hasn't answered yet, retries and yields. None of them restarts it: each is the
  conversation holding the transition back.
- **What doesn't:** room holds ("need a moment", a distress or conduct pause, a report).
  They stop the show clock, and nobody's conversation held the transition back meanwhile.
  Their length is kept apart and left out of the age; the due time itself never moves.
- **Protections still apply while overdue.** Being overdue shortens only the quiet. In
  order (`ShowRunner._deferral`):
  1. a dater speaking: never talked over, however overdue;
  2. Nova's previous turn line, for at most 20 s;
  3. a last word's 20 seconds;
  4. the quiet;
  5. the floor (`Floor.holds`), within the 60 s overrun budget: a clarification Nova owes,
     a handover not yet taken, an explicit hesitation, speech no final has said yet, and a
     dater asked who hasn't answered.
- **A silence alone never ends** a hesitation, a handover or a clarification. Each ends as
  before: when the floor hears what it waits for, or 20 s after the floor last changed. Any
  new final ends a hesitation, the asker moving on ends a clarification, and the other
  dater speaking takes a handover.
- **Once a protection ends**, an overdue transition goes in the brief opening. Four existing
  tests show it: Theo's prompt now comes 0.4 s after the answer that ends a "Let me think.",
  a handover, a clarification or a minute-long story (or when the grace ends), instead of
  2.5 s after it.
- **The player checks the same** before the voice: nobody speaking, the floor not holding
  (with no budget there), and the same quiet. A dater who starts meanwhile defers the line
  to the next opening.

### A question's answer window

- **Queuing a question opens nothing.** The window opens when the room has heard the
  question (`line_done`, `Floor.window`): from the first audio of the delivery that said
  it, to its end.
- **Speech answers it only if the voice detector timed it inside the window:** it began
  after that first audio, and it went on to the question's end or began after it. Its start
  is the earliest speech of that dater no final has said yet, never when the final arrived.
  So a late final for speech from before the question doesn't answer it.
- **Speech over the question's end** is an answer, and its final often arrives before Nova
  has finished. Finals heard before the question is said are kept, with their times only,
  and judged once it is (`Floor.early`). An "mm" in the middle of the question, which ended
  before the question did, is not an answer.
- **Interrupted, then said again** (`Then.RESUMED`, `Then.RESTARTED`): the window is that of
  the delivery that said it, so the interruption isn't the answer, and what follows it can
  be.
- **Partly said, cut late, the rest rightly never said** (`Then.DROPPED`: every word had
  played, or more than 2 s of a voice without word timings): the room heard it up to the
  cut, which ends the window. Speech over the cut is the answer: the dater heard enough to
  respond to it, and Nova won't say the rest.
- **Never said, or not all of it** (dropped from the queue, stopped by a pause or the date
  ending, superseded by a newer line, never voiced, muted): nobody owes it an answer
  (`Floor.withdrawn`), and the next turn doesn't wait for one. The beat isn't tried again: a
  pause still drops the question, as decision 0004 has it. Each line is reported done once,
  and an activity commits once.
- **Before**, speech between a question's cue and its voice used up the answer, and also
  held Nova back for the floor's 20 seconds (the audit's "host speech preempted by new speech
  waits for the floor's 20 s"). Both are fixed.

### Transcript timing

- **A final that arrives while its dater is still speaking** is never taken as their last
  word. When the voice detector reports the stop, a final from this speech at most
  `FINAL_LEAD` (1 s) earlier already said how it ended. A closing phrase in it becomes a hint
  then, if nothing has happened since (no newer speech or final, by the floor's revision) and
  nobody else is speaking. A final further back means more followed, and a later final will
  say it.
- **Speech no final has said yet holds the floor** (`Hold.UNHEARD`), on purpose. Until
  speech-to-text says what it was, Nova can't know it wasn't "let me think", a handover or a
  question to Nova. The hold is bounded: until a final arrives, or `LATE_FINAL` (2 s) after
  the stop.
- **Before, it was an accidental stall.** Any speech start set a hesitation that only the
  floor's 20 s ended, so a cough with no final could hold a transition for up to 20 s. Now
  that is bounded. Explicit hesitations are untouched, and a pass ends when its dater speaks
  again.
- **The deferral is intentional, and can keep a transition pending.** If every final arrives
  after the next dater has started, no opening ever has the latest speech known. The runner
  then waits until the 60 s budget is spent and goes in a brief opening: a test shows 111.9
  s for a prompt due at 51 s. The player has no budget, so it waits for an opening with the
  latest speech known.
- **How often this happens** depends on how late real finals arrive. `FINAL_LEAD` and
  `LATE_FINAL` are assumptions about LiveKit's voice detector and speech-to-text, and the
  rehearsal report now measures both.

### Diagnostics

Codes and numbers only (`nova/events.py`), with no recording and no transcript kept. All
times are milliseconds from the date's start, so the events line up on one timeline:

- `transition`: each due transition's phases (due; deferred, with its reason and the quiet it
  needs; went, with the quiet it used; dropped), its beat, due time and pending age;
- `final`: when each final arrived, which speech it was taken to say (by its start), how long
  after its dater stopped, its move, and whether it was the latest speech, a hint or an
  answer;
- `delivered`: whether the room heard a transition's line, from when to when, its yields,
  and what Nova did after its last cut;
- `speech`: now with its time.

The report adds a row per transition: when it came due and went, its pending age and quiet,
what held it back and for how long, and how its line went. A finals row gives how late
finals came after the stop, how many arrived while their dater was still speaking or after
newer speech, the answers and hints, and the stops no final followed.

### Tests and limits

`tests/test_transitions.py` and cases in `tests/test_floor.py`, on fake clocks, cover:
- half-second gaps through the runner's own eligibility path: held for ever without the
  grace, a brief opening after it;
- a closing phrase at 1.5 s, the lull without one, and a closing phrase whose final came
  just before the stop;
- active speech while overdue;
- a clarification, a handover and thinking while overdue, each released by its answer or its
  bound, never by a silence;
- a room hold left out of the age, with the due time kept;
- retries;
- speech while a question is queued, and speech from before it with a final after it;
- a normal answer, an answer over the question's end, and an "mm" in its middle;
- an interruption, then the question said again;
- a question cut late with the rest dropped, one superseded and one stopped;
- new speech clearing a hint, late finals not reviving it, and an outstanding final holding a
  brief opening until it says what it was;
- a cough with no final;
- finals that lag every gap;
- the diagnostics, without words;
- unrelated timing.

This is synthetic, fake-clock evidence. It says nothing about real audio, speech-to-text lag
or how a date feels, and it does not show that Nova never overlaps a dater.

Results: native **2,232 passed**; Docker **2,241 passed, 33 skipped** (each skip a file the
image doesn't hold; those pass natively). Ruff lint and formatting pass.

Remaining limits:
- a hesitation ends on any new final, the other dater's included, as before;
- if one final says both speech from before the question and the answer after it, it isn't
  counted, and the floor waits up to 20 s for another;
- the player's check has no budget, so finals that lag every gap can hold a line already
  committed;
- a question dropped by a pause is not asked again (decision 0004).

**Still for the two-person rehearsal:**
- whether 15 s and 0.4 s sound right, and how often Nova starts in a thinking pause;
- how late real finals arrive, and how often speech not yet heard holds a brief opening;
- whether `FINAL_LEAD` and `LATE_FINAL` match the real voice detector;
- whether protected waits should count toward the grace.

## Optional live prefixes, 2026-10-31

The product owner wants prefixes on in the final product, sounding like a host. Until then
`NOVA_LIVE_BRIDGES` stays off by default. Turning it on needs three things, in order:
1. the load gate below, passed on this machine;
2. a two-person rehearsal with prefixes on, judged by ear;
3. the product owner's go-ahead, with the privacy documents updated in the same change.

Only the first can be checked without people.

**What a prefix does.** A prefix is a short lead-in that Nova may put before three
questions: the second dater's first question, the first room question and the first last
word. That is at most three a date, and only while Nova's airtime is low. The question
itself is unchanged. A prefix changes Nova's words, not when Nova speaks. It is written
when the runner commits the transition, so it adds its own writing time before the
question. If anyone speaks meanwhile, the prefix is dropped and Nova asks the plain
question, so prefixes fit quiet moments.

**How it's written now, and why.** A look at what `gemma4:26b` wrote for five made-up
exchanges, and the load trial below, showed four problems. Each now has a fix:
- **Almost every prefix used one formula,** "From X to Y." ("From hiking views to picnic
  rides."). That is generic, and it sets the daters side by side. The prompt now asks for
  a host's quick reaction to one detail, two to six words. It names that formula as one to
  avoid, and gives two examples in the brand book's voice ("Bees on the roof, of course.",
  "The long way home, then.").
- **The model often wrote the question again after its prefix,** which broke the eight-word
  and no-question rules. Only the prefix's first sentence is kept now (`first_sentence`), so
  the question is said once, and the guardrails and the meaning check judge exactly what is
  said.
- **Given the date's recent turns, it often picked an older one,** so the prefix landed out
  of nowhere. It now sees only the turn just taken (`DateContext.last_turn`). If that turn
  was held back or too short to keep, no prefix is written at all. This is less of this
  date's context than before, to the same recipients.
- **A lightning question is one sentence,** so a prefix before it always broke its limit:
  every one in the first trial was rejected, each after a model call. Lightning questions
  are no longer bridged. No prefix is asked for where the line's limits leave no room.

**A conflict with the brief opening, fixed.** After a prefix went stale, the player waited
a fixed 1.5 s turn gap before the plain question. For an overdue question that is the stall
this decision fixed, since lively talk never leaves 1.5 s. Now the plain question waits like
any other transition line (`_polite`): the brief opening once overdue.

**Diagnostics.** Each transition line records what became of its prefix: said, obsolete (new
speech came first) or rejected (it failed its checks or took too long). This appears in the
`delivered` event, the transition's report row and the Conversation row's counts.

**The gate** (`uv run python -m nova.conversation_trial --rounds N`, default 14):
- **What it does.** Each round adds one of five made-up exchanges to the date. It makes the
  same ordinary safety check three times with nothing else running, then once while each
  bridged question writes its prefix, so the prefix contends for the model as it would on
  air. 14 rounds make 42 checks of each kind.
- **What fails it:**
  - any check that times out (unchecked);
  - any verdict other than no;
  - the slowest 1% of contended checks over three quarters of the distress timeout (1,500
    ms with the 2 s default).

  The 75% margin is proposed, and the product owner hasn't confirmed it yet. With 42
  checks, p99 is close to the worst.
- **How it runs.** It exits non-zero on failure. It is synthetic only: fixed sentences, no
  real daters, codes and numbers only. It never measures safety recall. Run it on demand with
  local Ollama and the machine otherwise idle, never in CI.

**Results**, 2026-10-31, local `gemma4:26b`, the machine otherwise idle (2 to 9% CPU just
before each run):

| Run | Prefixes said | Fell back | Contended check p99 | Gate |
| --- | --- | --- | --- | --- |
| First trial: 10 rounds, one exchange, lightning included | 13 of 40 | 14 too long or a question; 10 lightning, no room; 3 meaning | 1,280 ms | Passed |
| Before the prompt change: 14 rounds, five exchanges | 17 of 42 | 21 too long or a question; 4 meaning | 1,197 ms | Passed |
| New prompt and first sentence only | 29 of 42 | 13 meaning | 1,500 ms | Passed, no margin |
| Only the turn just taken | 33 of 42 (79%) | 9 meaning | 1,362 ms | Passed |

- **Safety checks.** In every run none went unchecked and none gave another verdict than no.
  In the last run, checks alone took a median 258 ms and p99 729 ms; with a prefix
  contending, a median 992 ms and p99 1,362 ms. That is 138 ms under the limit. More prefixes
  passing means more meaning checks contending, and contention roughly quadruples the
  median, so the margin stays thin: a busier machine or a slower check could fail it.
- **Prefixes.** In the last run the model wrote one in a median 1,515 ms, p95 1,764 ms.
  They now pick up the latest turn, for example "A library book, then.", "Flat roads and
  picnics." and "A silent room, then.". Many open with "A". Whether they sound like a host,
  and whether that sameness shows across a night, is for the rehearsal.
- **The evaluation** (`uv run python -m nova.evaluation`) ran before the prompt change and
  after each step, as decision 0005 requires. It doesn't cover prefixes, and nothing else
  changed: missed distress 3 of 73 (an existing failure), false pauses 0, false alerts 2 of
  73, unchecked 0. The conduct and meaning gates pass, and safety check times were within
  noise (median 426, 388 and 382 ms; worst 1,178, 1,184 and 1,179 ms).
- **Limits.** Synthetic runs on one machine: they measure contention and how often a prefix
  survives its checks, not recall, and not how a prefix sounds.

**Privacy.** Nothing changes while the default stays off. Prefixes use this date's cleared
context (now only the turn just taken) and the existing recipients, as
`docs/privacy/nova-data-flows.md` and decision 0010 describe for the opt-in. Turning the
default on changes the default flows, so that change updates both documents and the dater
notice, in the same change.

**Tests.** `tests/test_conversation_trial.py` covers the gate's rule, the exit code, and the
check that only fixed synthetic examples are sent. `tests/test_bridges.py` covers:
- a stale prefix on an overdue question waiting only for the brief opening;
- a prefix said and counted;
- only the first sentence kept, and a repeated question said once;
- only the turn just taken shown, and none when it was held back;
- no model call where a question leaves no room, and no lightning question bridged.
