# Week 2 spike results

Status: in progress. Tasks 5 to 7 fill this in; task 8 finishes it.

## Task 5: Nova in the Agents Playground

### Targets

Set by the product owner on 2026-10-13, before the first rehearsal. Results never change
them; a target that turns out to be wrong is changed in a later round, before that round's
rehearsals.

| # | Target | Pass if |
| --- | --- | --- |
| 1 | Prepared lines start within 1 s of their cue | Every prepared line in the rehearsal starts within 1 s |
| 2 | Live reactions start within 2 s of their cue | Every live reaction starts within 2 s |
| 3 | Replies to a dater start within 2.5 s of the end of their sentence | Every reply starts within 2.5 s |

Each rehearsal reports, per target, how many lines it covered, the median, the worst case,
and passed or failed.

### How each one is measured

- **Starts:** the moment Nova's first audio frame plays out of the agent into the room.
  It's measured inside the agent, so it leaves out the network trip to a listener's phone
  (task 7 measures that).
- **Cue:** the moment the show runner asks for the line.
- **Prepared line:** any line whose words are ready without a model call during the date:
  set pieces written before it (Doors, first questions, lightning questions, last words,
  outro, Next up), approved lines (time, result, invitation, the pause lines) and fixed
  fallbacks.
- **Live reaction:** a line the model writes during the date: a silence prompt, an audience
  question, talk to the room.
- **Reply:** Nova's first line after a dater speaks: a reply when they speak to Nova, or
  the kind line when they want to stop, whoever they say it to. **End of their sentence**
  is when the voice detector hears them stop, not when the transcript arrives. Nova acts
  once their utterance has closed (decision 0005: the grace after the last transcript),
  so that wait counts. When the distress check decides (decision 0005, the meaning
  layer), its time counts too, and a reply to Nova waits for it.

### What the rehearsal leaves out

The rehearsal plays Nova's lines and nothing else. It skips the show's sounds (the
on-air notes as a date starts, the time's-up notes), the daters' voice intros and the
recorded fallback segment. So after the moderator ends the date (`end`), the room goes
silent, unlike decision 0004. That's acceptable for a spike that measures Nova's timing;
the agent for real shows has to play all three.

### Suggested changes, not applied

These came before any rehearsal. The targets above stay as set unless the product owner
changes them before the first one.

1. **Measure from when Nova is free, not from the cue, when a line has to wait.** Some
   cues come while Nova is still talking: at Time, talk to the room follows "That's time.
   Maya and Theo are deciding, privately." A line queued behind a four-second line misses
   1 s by design. Each rehearsal reports both, but passes or fails on the cue as written.
2. **Targets 2 and 3 can't pass in the worst case with the model timeout at 2 s.** A live
   reaction that times out waits 2 s before its fallback even starts, plus the voice's
   first audio (about 0.2 to 0.5 s). A reply adds the end-of-speech wait and the
   transcript (about 0.5 to 1 s). Either lower `NOVA_LIVE_TIMEOUT_SECONDS` to 1.2 s (Gemma's
   slowest live line so far took 565 ms, so there's still twice the headroom), or judge
   targets 2 and 3 on the 95th percentile rather than every line.
3. **Target 1 looks right.** No model is involved, only the voice's first audio.

### Rehearsals

**Eight measured Playground rehearsals ran on 2026-10-18**: one person playing both daters,
about 4 minutes each, local Gemma (`gemma4:26b`), Cartesia's voice, Deepgram's speech
recognition. The reports are in services/nova/rehearsals/2026-10-18-*.txt, kept on the PC
that ran them, never committed. What they show, read from the reports:

- **Target 1, prepared lines within 1 s of their cue:** failed in all eight from the cue
  (worst 5.6 to 12.6 s); passed in seven of eight from when Nova was free (medians 176 to
  200 ms). The misses waited behind a line still playing, or a dater still talking
  (suggestion 1).
- **Target 2, live reactions within 2 s:** failed in all eight from the cue; from when Nova
  was free, passed in four and failed in four (worst 2.2 to 11.7 s).
- **Target 3, replies within 2.5 s:** one reply in all eight rehearsals, at 1,949 ms: one
  line is no measure.
- **Two of the eight opened a date of fallbacks.**
  - In the first, the warm-up timed out at the then 30 s deadline.
  - In the third, it failed with an error after 20.8 s.
  - Both then ran out the 45 s prep budget, so every set piece was a fallback.
  - In the first, all 12 distress checks went unchecked at 2 s.
  - This is why the warm-up's deadline was raised, and why, since 2026-10-22, a date Nova
    isn't ready for holds for the moderator (decision 0005).
- **Nova's airtime:** 27% to 32% of each date, against the brand book's "well under a
  fifth". The later work on brief lines targeted this; no Playground rehearsal has measured
  it since.
- **The checks:** distress checks took medians of 553 to 602 ms, worst 1,080 ms. Utterances
  were acted on a median of about 600 ms after the dater stopped.
- **Credit:** about $0.07 to $0.08 per rehearsal, as the reports estimate it.

Limits: they ran on the code of 2026-10-18, before the changes since (the warm-up, the
arc, this date's context, the yield, the checks), with one person playing both daters
through one microphone. No Playground rehearsal has run on the current code.

The yield rehearsal (decision 0004, `nova.yield_live`) ran in a real LiveKit room on
2026-10-22, as a smoke run of one trial each:
- A dater cutting in as Nova started was heard to stop Nova 485 ms after their first frame
  (agent 400 ms); a later run gave 536 ms.
- Its full run is invalid: LiveKit Inference refused every voice request with 429 Too Many
  Requests from the sixth trial on, for every voice tried, and still did 12 minutes later.
- Mid-line yielding stays release-blocking until a full run passes.

### The model's lifecycle on the development PC (2026-10-21)

Measured against local Ollama (`gemma4:26b`) through the OpenAI SDK Nova uses, outside
any rehearsal, to settle how warm-ups and timeouts behave (decision 0005, timeouts):

| What | Measured |
| --- | --- |
| A tiny request, warm | 0.09 to 0.10 s |
| A 600-token request, run to the end | 7.2 s |
| A tiny request right after cancelling that one at 1 s | 0.36 s: Ollama dropped it |
| The same after cancelling three queued at once | 0.35 s: dropped too |
| A tiny request sent 1 s into one left running | 6.09 s: one request at a time |
| A cold load (first request after unloading) | 17.7 s, then 17.8 s |
| A request after cancelling the cold one at 5 s | 17.5 s: the load was aborted, and started again |
| The new warm-up: cold / Ollama unreachable / a model not pulled | 17.8 s, 1 try / 26.5 s, 5 tries, failed / 0.27 s, rejected |
| Distress checks during a full prepare, one every 0.4 s | 27 checks: median 586 ms, worst 1,315 ms, none unchecked; prep 28.6 s |

