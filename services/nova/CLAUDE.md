# Nova

The AI host: a deterministic show runner walks each date's running order, and a language
model only writes the lines inside its beats. Python 3.14, uv.

## Where Nova's decisions live

docs/decisions/0004 onwards: 0004 the show runner (running order, pauses, holds, the
result), 0005 lines, guardrails, fallbacks and the distress check, 0006 dater conduct, 0007
the listener delay (proposed), 0008 this date's context for live replies and silences (not
tonight's memory: one date, only what the checks cleared, never logged), 0009 the date
benchmark (whether a change makes a date better: `uv run python -m nova.benchmark`, on
demand with local Ollama, never in CI; its hard gates fail the run, its indicators hint,
and only people's blind ratings say whether it helped: `uv run python -m nova.ratings`),
0010 privacy at the request boundary (who may receive what: nova/privacy.py; every flow:
nova/data_flows.py, `uv run python -m nova.data_flows`, and docs/privacy/), 0011 a show's
dates (nova/show.py: one context a date, closed however the date ends) and the doors-hold
that tries again before the moderator decides.
Iterate on the benchmark's development set; its comparison set is the scorecard, never
tuned against. Read the one a change touches first, and update it in the same change. Lines
waiting for the product owner are in docs/sign-off/ and nova/sign_off.py.

## Rules

- Every line the model writes passes the guardrails (nova/guardrails.py) or an approved
  fallback is said instead. A new on-air line, fixed or fallback, needs the product
  owner's sign-off: draft it in nova/sign_off.py and docs/sign-off/, never straight into
  nova/script.py.
- No text in logs: nova/events.py accepts codes, numbers and flags only. Never log what a
  dater, listener or Nova said.
- Every model request goes through the privacy policy (nova/privacy.py, decision 0010): a
  model a Rehearsal or LineWriter guarded, never a bare one where real daters are heard.
  This date's turns reach a model only at an approved-local endpoint (loopback, or
  NOVA_APPROVED_LOCAL_HOSTS) or with NOVA_DIALOGUE_TO_PROVIDER. Only the evaluation and
  the benchmark, on made-up daters, use `PrivacyPolicy.for_synthetic_data()`.
- Before any model change (a prompt, the model, the guardrails it's measured against), run
  `uv run python -m nova.evaluation` against local Gemma, before and after, and put both
  summaries in the pull request. It covers the cases and the distress and conduct dev sets,
  each a gate (missed distress, false pauses, false alerts, unchecked checks, conduct
  recall, and the meaning check on Nova's lines, `--meaning`). `--night` is the number we
  track and a gate. Tune on dev only; `--held-out`
  runs the held-out sets once a change is settled, and prints counts, never sentences
  (the first held-out set was consulted on 2026-10-23, decision 0005). `--independent`
  runs the independent held-out set, written by someone other than the developer: never
  open services/nova/independent/distress.json, and never tune against its counts.
- The safety check is frozen (decision 0005, 2026-10-23; tests/test_safety_freeze.py pins
  it). Its numbers come from `--safety`: every set across fresh model reloads (`--fresh N`,
  default 3), each sentence counted at its worst. `--repeat` repeats within one model
  session, which overstates stability: never report it as the safety result.
  Run it with nothing else busy: part of Gemma runs on the CPU, and a test run alongside
  times the 2 s checks out.
- Every date opens through a show's Night (`with night.date(pair, ...)`, nova/show.py,
  decision 0011), the rehearsal's one date included: never a Rehearsal made directly where
  real daters are heard. The Night closes it however it ends.
- Nothing here calls production, and the tests never call LiveKit, Ollama or Claude.

## Running it on this machine

- Conversation changes are in decision 0012. The agent defaults to full-length dates
  and local floor tracking (`NOVA_FULL_REHEARSAL`, `NOVA_CONVERSATIONAL_TURNS`). Set either
  false for legacy comparison. `NOVA_CONVERSATION_OVERRUN_SECONDS` defaults to the product
  owner's 60 seconds. `NOVA_LIVE_BRIDGES` defaults false pending audio/load acceptance.
  A scheduled transition the conversation has held back for `Timing.transition_grace`
  (15 s) goes in a `Timing.brief_opening` (0.4 s) of quiet, never over speech or a floor
  protection; a closing phrase (nova/floor.py) shortens the lull to the turn gap before
  that. A question's answer window opens only once the room has heard it, judged on the
  voice detector's times. The report's Transitions and Finals rows and the `transition`,
  `final` and `delivered` events are the rehearsal diagnostics (decision 0012).
  For a two-person rehearsal, configure distinct `NOVA_LEFT_PARTICIPANT` and
  `NOVA_RIGHT_PARTICIPANT` LiveKit identities and join both before opening:
  `uv run python -m nova.two_person` signs each person's token (no LiveKit call), and
  docs/rehearsals/two-person-full-length.md is the run sheet. Each input, and what each
  person types, is pinned to its own seat. Empty identities retain the single-microphone
  simulation; speech follows the prompted seat/handover unless a typed seat overrides it.
  Start through the existing Docker rehearsal command; no live rehearsal or worker
  restart is performed by unit tests. Check the report's segments, last words, decision
  window, interruptions, both airtime shares, reply latency and safety checks. Human
  validation stays pending until two real people have run it: blind audio ratings need a
  recording, consent and the data-flow docs first, and none is ever simulated.
  The ordinary local-model baseline currently has existing missed-distress failures;
  do not report passing unit tests as a passing safety evaluation.
  `uv run python -m nova.conversation_trial [--rounds N]` is the load gate for optional
  prefixes (decision 0012): ordinary synthetic safety checks alone and while a prefix
  contends, locally and with nothing else busy. It fails (non-zero exit) on any unchecked
  or changed verdict, or a contended p99 over three quarters of the distress timeout. It
  prints codes/counts/timings only, never measures safety recall, never enables prefixes,
  and does not replace the frozen safety report or human rehearsal.

- `uv sync`, then `uv run ruff check`, `uv run ruff format` and the tests.
- Smart App Control blocks PyAV, so the tests that import LiveKit (tests/test_agent.py,
  tests/test_voices.py, tests/test_yield_live.py) run only in Docker:
  `docker compose -f services/nova/compose.yaml run --rm --build nova uv run pytest`
  (from the repository root). Natively:
  `uv run python -m pytest --ignore=tests/test_agent.py --ignore=tests/test_voices.py
  --ignore=tests/test_yield_live.py`
  (`python -m pytest`, since Smart App Control may also block the pytest launcher).
- The yield rehearsal (decision 0004) runs Nova and a scripted dater in a real LiveKit room,
  in Docker only, and costs LiveKit credit: `docker compose -f services/nova/compose.yaml run
  --rm --build nova uv run python -m nova.yield_live`. Its report (numbers only) lands in
  services/nova/rehearsals/yield-*.txt.
- The mute rehearsal (decision 0005, launch requirement 2) runs Nova with the agent's own
  moderator control, a dater, a moderator and an impostor in a real LiveKit room, in Docker
  only, at LiveKit credit: `docker compose -f services/nova/compose.yaml run --rm --build nova
  uv run python -m nova.mute_live`. Its report lands in services/nova/rehearsals/mute-*.txt.
- The startup rehearsal (decision 0011) runs eight sessions in real LiveKit rooms (cold,
  warm, an Ollama outage, a missing model) and measures how Nova gets ready and how the
  doors-hold ends, in Docker only, at LiveKit credit, with the machine otherwise idle:
  `docker compose -f services/nova/compose.yaml run --rm --build nova uv run python -m
  nova.startup_live`. Its report lands in services/nova/rehearsals/startup-*.txt.
- The moderator's messages are zod schemas in packages/shared (src/moderator.ts);
  nova/moderator.py checks the same shapes, and both are tested on
  packages/shared/fixtures/moderator.json. Change them together.
- The evaluation and rehearsals need services/nova/.env (see .env.example). Open
  .env.example by its exact path: Claude Code's Glob and Grep can't see it under the Read
  deny rules for .env files. A rehearsal and voice samples cost LiveKit credit; the
  evaluation with Ollama costs nothing.
