# 0011: One context a date, and a doors-hold that tries again

Date: 2026-10-26. Status: accepted. The product owner's choices: 2026-10-24.

## Context

There is no show server yet: the LiveKit agent (`nova/agent.py`) is the only entrypoint, one
session, one `Rehearsal`, one `DateContext`. Tracing a date from the session's start to its
end found:

1. **Nothing held a show to one context a date.** Decision 0008 left "a show agent that
   runs several dates must make one each" to whoever writes it, with nothing to stop it
   reusing a date, or running two at once in one show.
2. **Only a date that ran was cleaned up.** The context and tonight's planned set pieces were
   cleared at the end of `Rehearsal.run()`. A date the moderator ended before it opened never
   ran, so turns heard while Nova got ready stayed in its context, and set pieces it never
   said stayed in tonight's memory as planned. An error or a cancel relied on the process
   ending.
3. **A cleared context still took turns.** A check that finished after the clear could add
   one.
4. **A date outlived its session** (found in two Playground rehearsals, 2026-10-26). When
   the person playing both daters left, LiveKit closed the session, but the date ran on to
   the empty room: lines and model requests carried on, with this date's turns, until
   LiveKit cancelled the job about 35 s later. That cancel skipped the report.
5. **The doors-hold only waited** (decision 0005, 2026-10-22). If Nova wasn't ready from
   the session's start (60 s), the moderator was told and chose; nothing tried again. An
   Ollama up 30 s late meant opening on fallbacks or ending the date, though Nova would have
   been ready a minute later.

## Decision

- **A show's night** (`nova/show.py`, `Night`). Every date opens through
  `night.date(pair, ...)`, which makes the date, with a new `DateContext`, and closes it
  when the block exits, however it exits: it ran, the moderator ended it, it never opened,
  an error, a cancel.
  - One live date a night: opening a second raises `DateInProgress`.
  - Tonight's memory (Nova's own lines, names masked) belongs to the night and is shared
    by its dates. Two nights share nothing, so two shows in one worker never meet.
  - The agent opens its one date through a Night: the rehearsal and a future show server
    keep the same contract. A test checks that the agent makes no date any other way.
- **Closed means closed** (`Rehearsal.close`, `DateContext.close`; safe to call again):
  - the checks still in flight stop;
  - set pieces never said leave tonight's memory;
  - the context is emptied, and after that `heard`, `spoke` and `said` do nothing, so a
    late check can't add to it.
- **A date ends with its session** (`nova/agent.py`, `run_date`). If the session closes
  first, whatever the date is doing stops: getting ready, the doors-hold, or the date
  itself, whose checks in flight still finish. The Night then closes the date, and the
  report is written with a line saying the session closed, and LiveKit's reason code.
- **0008's bounds are unchanged.** An acknowledgement takes no place, and a personal
  detail records only that the dater spoke. The tests now check both through a whole date,
  and that nothing a dater said, nor their names, reaches the next date's prompts.
- **The doors-hold tries again** (`Rehearsal.hold_until_ready`; the product owner chose
  120 s of tries, and that Nova opens itself once ready and tells the moderator):
  - Not ready within the 60 s bound (decision 0005): the moderator gets `nova_not_ready` at
    once, as before, and the doors hold.
  - While they hold, a failure that may pass is tried again: a timeout, or a server
    unreachable, overloaded or failing after the warm-up's own tries (`unavailable`, split
    from `error` in this change).
  - The tries come after 2, 4, 8, then every 16 s, within `NOVA_READY_RETRY_SECONDS`
    (default 120).
  - Each try warms each failing model with one request held for what's left, so a load is
    never cut short. Once the models answer, only the set pieces that fell back are written
    again, and each fallback stays until a line replaces it.
  - Ready after all: the moderator gets `nova_ready`, and the date opens.
  - A failure that can't pass (a model not pulled, a bad key, a refusal, anything it can't
    tell), or no luck by the deadline: the moderator gets `nova_still_not_ready`, the
    tries stop, and the doors hold for the moderator's choice: `start` with the approved
    fallbacks, or `end`.
  - The moderator's choice wins at any point, and stops the tries.
  - Nova never opens itself on fallbacks alone, and the tries always end: after that, only
    the moderator opens or ends the date.
- **Measured on every date**: the `opened` event (codes and numbers only) gives
  - how the start was decided (`ready`, `recovered`, `moderator`, `ended`);
  - the time from the warm-up's start to that decision, and how long the doors held;
  - the tries and rewrites, and the fallbacks among the set pieces.

  The rehearsal report says it in one sentence.
- **The startup rehearsal** (`nova/startup_matrix.py`, `nova/startup_live.py`; Docker
  only, LiveKit credit) runs eight sessions in real LiveKit rooms with the real models:
  three cold (the models unloaded first), three warm, one outage and one missing model.
  - Outage: Ollama is unreachable for the first 45 s, through the harness's own proxy, so
    Nova must hold, try again and open itself.
  - Missing model: the moderator must hear at once that Nova won't be ready, with no tries,
    and end the date.

  Each kind must show what it should, or the run exits non-zero.

## Measured, 2026-10-26

The startup rehearsal passed 8 of 8 on the development PC: Gemma (`gemma4:26b`) on Ollama,
Nova in Docker, real LiveKit rooms. The report stays local, since `rehearsals/` is ignored.

| Measure | Result |
| --- | --- |
| Cold start, from the warm-up's start to ready | 35.4, 36.2 and 38.3 s: a load of 18.3 to 21.0 s, then prep of 16.3 to 17.9 s |
| Warm start | 17.3, 17.4 and 17.5 s: a warm-up of 0.4 to 0.5 s, then prep of 16.8 to 17.0 s |
| Tries while the doors held | Outage: 4, at 2, 6, 14 and 30 s into the hold; the first three refused, the fourth answered. Missing model: none |
| Time held | Outage: 48.0 s. Missing model: 0.5 s, until the moderator ended it |
| Fallback rate after warm-up, dates that opened | 2 of 70 set pieces (3%): one each on two of the six cold and warm dates; none on the outage's date after its rewrite |
| Date-start failures | 1 of 8: the missing-model trial, which is meant to fail |

How the two hold trials ended:
- **The outage recovered.** Once Ollama answered again (0.5 s), the ten set pieces were
  written again in about 17.5 s, every one the model's. The moderator got `nova_ready`, and
  the date opened itself 63.1 s after the warm-up's start.
- **The missing model was reported at once.** It was rejected on one try, and the moderator
  got `nova_not_ready` then `nova_still_not_ready`, 0.8 s after the warm-up's start.

Nova starts its warm-up within about 0.1 s of LiveKit's job request, so the warm-up's start
stands for the session's start.

Findings:
- **Prep has about 2 s to spare.** It now takes 16.3 to 17.9 s of its 20 s budget, against
  11.2 to 11.5 s on 2026-10-22. The likely cause is the meaning check on each set piece,
  added on 2026-10-24, though it wasn't measured on its own. Cold starts stay inside the
  60 s bound, but two dates had one set piece cut off by the budget. Raising
  `NOVA_PREP_BUDGET_SECONDS` would raise the bound with it, so that is the product owner's
  call; it is not changed here.
- **A recovered date can open up to about 16 s late.** After the fourth try the pauses are
  16 s, so the next try can come up to 16 s after Ollama is back. Then the rewrite takes
  about one prep.

## Consequences

- A show server opens each date with `night.date(...)`, one Night a show; it never makes a
  `Rehearsal` itself.
- The moderator console, when it is built, must show `nova_ready` and
  `nova_still_not_ready` beside `nova_not_ready`. They are Nova's alert codes
  (`nova/cues.py`); none of the alerts is in packages/shared yet.
- `nova_still_not_ready` comes at most 120 s after `nova_not_ready` (the pauses, the tries
  and the rewrites all fit inside it), so at most about 3 minutes from the session's start:
  the 60 s bound, then the tries. The moderator may choose at any time before that; a
  shorter `NOVA_READY_RETRY_SECONDS` brings it sooner.
- The classifier is unchanged: no new infrastructure in this change. Isolating it is still
  the product owner's choice (decision 0005, "The classifier's slot").
