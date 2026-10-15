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
  is when the voice detector hears them stop, not when the transcript arrives.

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

No rehearsals yet.
