# Nova hardening review, 2026-10-22

Findings, decisions and test results from the remediation pass on
`fix/nova-boundaries-and-teasing`: seven prompts of fixes, then a final audit (Prompt 8).
It ran on 2026-10-22 after the ten-prompt pass of 2026-10-21. Nothing in it is committed yet. Each prompt's own detail is in the decisions
it names. This file is the running record across them, and the gate table at the end is
the release picture as of today.

Every real-model number is from local Ollama (`gemma4:26b`) on the development PC, run
with the machine otherwise idle. "Native" means the Windows suite without the LiveKit
files, and "Docker" means the full suite in `services/nova/compose.yaml`.

## Prompt 1: audit and baseline (read-only)

- The branch is identical to `main` at 2346211 (#37). The work is all uncommitted: 23
  tracked files modified and 11 untracked at the time. There were no secrets in the diff.
- The five findings marked fixed in the first pass were still fixed: silence prompts use
  context; declined topics stay hidden; outros and lightning lead-ins are short; airtime
  is 19% for the night (on the edge of "well under a fifth"); and the indicator word
  lists.
- The risk register: P0 for the refusal regression (R1), real-audio yield (R2), and
  decision 0005's missing meaning check and mute (R3). P1 and P2 risks follow below,
  each with the prompt that took it up.
- A proposed split into stacked reviewable branches (A start-up, B safety, C voice and
  ranking, D context, E arc, F yield, G benchmark). It isn't done yet: only the combined
  state is tested.
- Tests: native 1,641 passed. The Docker-only tests had never run.

## Prompt 2: refusals versus distress

- **Finding:** the branch's "calm boundary: alert only" rule was attributed to the
  product owner in decision 0005, but it was never their decision. The committed rule
  was to pause. The wrong attribution has been corrected.
- **The product owner's decision (2026-10-22), categories (a) to (f):**
  - (a) a clear refusal alerts only, even when it pleads, repeats, or answers a coercive
    or demeaning remark. That goes against the recommendation to pause.
  - (b) a calm topic boundary alerts only.
  - (c) a playful refusal gets nothing.
  - (d) a quiz opt-out gets nothing.
  - (e) pressing after a no is harassment.
  - (f) genuine distress pauses. So does a refusal that answers a threat or says the
    dater wants out or is scared.
- **Changes:**
  - the distress rules (`nova/distress.py`) follow those categories;
  - the dev set was relabelled and given balanced contrast pairs (70 real, 70 ordinary,
    9 refusals);
  - the conduct dev sample at 0.67 similarity to a held-out one was replaced;
  - a pressing-after-a-no pair was added.
- **Dev set, before and after:**

  | | Before | After |
  | --- | --- | --- |
  | Paused | 62 of 66 | 69 of 70 |
  | Missed | 1 (the sofa sentence) | 1 (the sofa sentence) |
  | False pauses | 1 | 0 |
  | False alerts | 4 of 66 | 5 of 70 |
  | Harassment caught | 3 of 4 | 5 of 5 |
  | Unchecked | 0 | 0 |

- **Held-out, run once:** 2 of 53 missed, up from 1. It wasn't tuned against.
- **What went wrong:** a pytest failure printed six held-out sentences. They weren't used
  for anything. The guard tests now print scores only, and decision 0005 records it.
- **Open:** three playful refusals still alert the moderator, where (c) says they should
  get nothing. It errs on the safe side.
- **Tests:** native 1,652 passed.

## Prompt 3: mid-line yield with real LiveKit audio

- **Audio path:** the agent hears only a dater's microphone track, never its own. Nova's
  voice gets back in only as acoustic echo, and nothing on the server tells echo from
  speech. LiveKit's 3 s echo warm-up silences speech recognition only, not the voice
  detector.
- **A bug that only real audio could find:** no dater cutting in mid-line ever stopped
  Nova. A check compared two separate readings of the clock, which a real clock never
  makes equal. It's fixed, with a test that fails without the fix. It hasn't been
  confirmed live yet.
- **Changes:**
  - up to 3 yields a line, sharing one 30 s budget;
  - Nova resumes only from a cut point it actually knows (Cartesia's word timings);
    with no timings, it repeats the line (cut within 2 s) or drops it;
  - a newer line goes before a stale rest;
  - the rehearsal matrix itself (`nova/yield_matrix.py`, `nova/yield_live.py`), with
    proof that the audio was real: a text-only run never counts.
- **Smoke run (one trial each):**
  - a dater cutting in as Nova started stopped it 485 ms after their first frame, and
    536 ms in a later run;
  - Nova's audio looped raw into the mic stopped the line 3 times, and none through the
    echo canceller.
- **Blocker:** the full run of 51 trials is invalid. LiveKit Inference returned 429 for
  every voice request from trial 6 on, account-wide. The report is re-checked as NOT
  VALID.
- **Tests:** native 1,680 passed; Docker 1,702 passed, 5 skipped.
- **Open, release-blocking:** a full rerun once the quota clears, then the echo choice
  (decision 0004 lists options a to d).

## Prompt 4: the Docker-only suites

- `test_agent.py` and `test_voices.py` passed: 23, with the documented command. They
  also passed with no network and no `.env` (27, including `test_yield_live.py`), so they
  reach no external service.
- CI's `nova` steps run inside the image: lint clean, 1,702 passed. The 5 skipped are
  sign-off tests, because `docs/sign-off` isn't copied into the image. They pass natively.
- No code was changed.
- **Gaps in coverage:**
  - nothing tests voice connection reuse or retry;
  - nothing tests the agent's hookup from LiveKit speech events to speaker attribution.

## Prompt 5: blind human comparison and grounding

- **The 30 pairs are frozen** as a blind comparison set and never used for tuning.
  - The packet is in `services/nova/rehearsals/blind-comparison/` (untracked).
  - Labels are neutral (P01 to P30), and the candidate is shown as A in exactly 15 pairs.
  - The rubric was fixed before any rating: seven dimensions, a preference, and a short
    reason.
  - `nova.ratings summary` reports wins, ties and losses with intervals.
- **Human ratings: pending.** None has been collected or invented, and no model rated in
  their place.
- **Finding:** the "link" indicator rewarded invented common ground. It now counts
  grounded lines only.
- **The product owner's decisions:**
  - Nova never claims feelings of its own;
  - a line that claims common ground without support is blocked on air;
  - the packet is kept as local files.
- **Changes:**
  - a "you both" claim needs each dater's own words (`nova/grounding.py`), checked by a
    guardrail plus a prompt rule;
  - the own-feelings guardrail;
  - a separate development set of benchmark scenarios;
  - simulated daters with seeded settings and memory.
- **Before and after:**

  | | Before | After |
  | --- | --- | --- |
  | Cases passed | 33 of 39 | 37 of 39 |
  | Benchmark hard gates failed (36 runs) | 10 | 0 |
  | Runs with unsupported "you both" | 4 | 0 |
  | Runs with Nova's own feelings | 4 | 0 |
  | Benchmark fallbacks | 0% / 2% | 3% / 4% |
  | Night fallbacks | 3 of 127 | 7 of 127 |

- **The cost:** more fallbacks. The guardrail is deliberately strict, so it also rejects
  the model's own paraphrases.
- **Tests:** native 1,744 passed; Docker 1,766 passed.

## Prompt 6: identity rule, context packing, provider privacy, arc

- **Identity:** the product owner allowed "AI" when someone asks. There is now one rule:
  only when asked whether Nova is human, real or what it is, Nova says it's "the show's
  AI host". "AI" is the only acronym allowed, and only then.
- **Context:** acknowledgements ("yeah", "me too") no longer take memory slots. The
  context is capped at 1,000 characters (about 150 tokens), each line attributed to who
  said it. The LiveKit agent creates exactly one context per session.
- **Provider privacy:**
  - There is no privacy notice in the repo, and none names a model provider.
  - With Claude, this date's recent turns stay local unless
    `NOVA_DIALOGUE_TO_PROVIDER` is true. It's off by default.
  - The product owner chose to gate context only. Cards, audience questions, words to
    Nova and every distress check still go to the provider under that setup, and need the
    notice first.
- **Arc:** dramatized differences ("opposite sides of the clock") are blocked on air
  unless a dater said it first. The outro never has to mention a date detail.
- **Before and after:**

  | | Before | After |
  | --- | --- | --- |
  | Cases passed | 38 of 41 | 38 of 41 |
  | Night near-duplicates | 7 | 4 |
  | Night same openings | 33 | 26 |
  | Benchmark fallbacks | 3% | 5% |
  | Reply prompt | 1,282 tokens | 1,349 tokens |

- **Open:** "a shared interest that isn't the same" passed 1 of 6 repeats. The model
  frames related hobbies as "you both", and the guardrail swaps the line for a fallback.
- **Tests:** native 1,771 passed; Docker 1,794 passed.

## Prompt 7: reliability and launch gates

- **Cold start:**
  - Measured at 30.0, 29.0 and 29.5 s: a load of about 18 s, then 11 s of prep, with all
    ten set pieces from the model.
  - The product owner set the bound at 60 s. The defaults are now a 40 s warm-up and a
    20 s prep budget; it was 135 s.
  - A date Nova isn't ready for holds the doors. The moderator gets `nova_not_ready`, then
    `start` opens the date with the approved fallbacks and `end` ends it.
  - Never a silent date of fallbacks.
  - The hold has no time limit of its own: it waits for the moderator, who has been told.
- **Claude cancellation:**
  - A timeout closes the connection.
  - Nothing shows that Anthropic stops generating or billing, and showing it would need a
    paid call. The working assumption is that an abandoned request is billed in full.
  - Tests show an abandoned request is never followed by a second one for the same thing:
    live lines, checks, set pieces and the warm-up.
- **Classifier slot:**
  - The 95th percentile is 444 ms idle, 1,161 to 2,013 ms during prep, and 1,460 ms with
    both daters during live lines (2 s timeout).
  - One prep run had 8 of 36 checks time out, and each alerted the moderator.
  - The product owner chose a priority gate (`nova/priority.py`). Measured, it made no
    difference on this PC: with one request at a time there is nothing queued for it to
    jump.
  - Isolating the check needs an infrastructure choice (decision 0005).
- **Meaning check:** not built. The proposal is in decision 0005, launch requirement 1.
- **Moderator mute:**
  - Built on the agent's side: `mute` and `unmute` stop Nova, drop every line while
    muted, report each to the show runner, and keep checks and alerts going.
  - The console's side isn't built, because the console doesn't exist. Its contract is in
    decision 0005, launch requirement 2.
- **Spike results:** corrected from the eight Playground reports of 2026-10-18. Two of
  them opened a date of fallbacks.
- **Repetition:**
  - Measured: silence 8 of 15 the same opening ("if you had", "if you were"), last word 7
    of 16, doors 1 to 5 of 8.
  - Proposed, not built, by the product owner's choice: a rotated pool of line shapes.
- **After the changes:**
  - cases 39 of 41;
  - distress dev: 1 missed (the sofa sentence), 0 false pauses, 5 of 70 false alerts,
    0 unchecked, a median of 406 ms;
  - conduct dev: every category caught, ordinary talk left alone 19 of 19;
  - benchmark hard gates passed, with fallbacks at 1% and 5%.
  - The two cases that failed were both the common-ground guardrail rejecting a "you
    both" line, so the room hears the fallback ("you're both working on some handmade
    crafts", and a difference turned into "you're both so focused on your schedules").
- **Tests:** native 1,785 passed; Docker 1,809 passed, 5 skipped; lint clean.

## Prompt 8: final audit

- **Fresh runs on the final code:**
  - lint clean; native 1,785 passed; Docker 1,809 passed, 5 skipped;
  - `test_agent.py` (21) and `test_voices.py` (4) all passed when run on their own.
  - Nothing in `nova/` or `tests/` changed after the last evaluation (Prompt 7), so its
    results stand for the final code.
- **A fresh `--night`:** 30 of 127 lines (24%) open the same way as an earlier one.
  - Per moment: silence 13 of 16, last word 7 of 16, doors 3 of 8, from the room 2 of 8.
    Near-duplicates are also over 10% for last word, room talk and from the room.
  - The gate fails. Silence repeats more than in Prompt 7's measure (8 of 15), so the
    night varies a lot from run to run.
- **Held-out timing:** run once, after Prompt 2's tuning. Since then the classifier's only
  change was wiring in the gate; its rules and data are unchanged, so that result still
  measures today's check. Not run again, by the rule.
- **LiveKit:**
  - A one-request voice probe was still refused (429) at 22:23 UTC.
  - LiveKit's docs give the free plan $2.50 of Inference credit a month, as a hard cap
    that resets on the 1st, and 5 connections at once. A single refused request points to
    the monthly cap. Not confirmed on the dashboard.
- **Blind ratings:** the sheet is still blank (no rater, nothing filled in).
- **spike-results:** re-checked against all eight reports, and every figure matches.
- **New findings:**
  - in the yield smoke run, a TV at -12 dB stopped Nova once; it's now in decision 0004;
  - playful refusals: 3 of 5 still alert the moderator, where the signed-off (c) says
    nothing should happen;
  - every setup sends daters' audio to Deepgram (speech recognition) and Nova's lines to
    Cartesia (the voice) through LiveKit Inference, and no privacy notice exists;
  - the not-ready hold has no time limit of its own.
- **Branch:** still one uncommitted, unsplit working tree. Prompt 1's split was never
  carried out, and no PRs exist.

## Open items

| # | Item | Waiting on |
| --- | --- | --- |
| 1 | Full LiveKit yield rerun (51 trials, about 25 min) | LiveKit Inference's 429 clearing (most likely the free monthly allowance: 1 November, or a paid plan) |
| 2 | Echo policy, options a to d (decision 0004) | The rerun, then the product owner |
| 3 | Human ratings of the 30 blind pairs | At least two raters |
| 4 | Meaning check on Nova's lines | Approval of the proposal, then a labelled line set |
| 5 | Moderator console: mute, start and end controls, the zod schema, acknowledgements | The console app |
| 6 | Classifier isolation | An infrastructure choice |
| 7 | Repetition: rotated shapes for silence, last word and doors | Approval, then nights before and after |
| 8 | Paraphrased "you both" lines falling back | A measured prompt change |
| 9 | The missed sofa sentence (dev) and 2 of 53 missed (held-out) | Classifier work |
| 10 | Playful refusals that still alert (category c) | Classifier work |
| 11 | Auto-mute for serious conduct (decision 0006) | The product owner |
| 12 | Listener delay (decision 0007) | Not started |
| 13 | A privacy notice that names every provider: Deepgram and Cartesia through LiveKit today, Claude if used | Its owner, before the first real show |
| 14 | A Playground rehearsal on the current code | LiveKit credit and the 429 |
| 15 | Tests of voice reuse and retry, and of the agent's speaker attribution | Approval to add them |
| 16 | Splitting the branch into reviewable changes | The user |
| 17 | A time limit for the not-ready hold | The product owner |

## Gates as of 2026-10-22

| Gate | Result | Evidence |
| --- | --- | --- |
| Launch requirement 1: meaning check on Nova's lines | **Fail** | Not built; proposal in decision 0005 |
| Launch requirement 2: moderator mute | **Fail** (incomplete) | Agent side built and tested; no console |
| Launch requirement 3: hearing conduct | **Pass** in first form, small sets | Dev: every category caught, 19 of 19 ordinary talk left alone; auto-mute undecided |
| Distress: none missed | **Fail** | Dev 1 of 70 (the sofa sentence); held-out 2 of 53 |
| Distress: false pauses under 1 in 20 | **Pass** | 0 of 70 |
| Distress: false alerts under 1 in 10 | **Pass** | 5 of 70 |
| Distress: none unchecked | **Pass** idle, at risk under load | 0 of 148; 8 of 36 timed out in one prep run |
| Refusals alert, don't pause | **Pass** | 9 of 9 |
| Evaluation cases | **Fail** (fails safe) | 39 of 41; both failures are fallbacks from the common-ground guardrail |
| Benchmark hard gates | **Pass** | Development set, 36 runs |
| Night: same opening at most 20% a moment | **Fail** | Night of 2026-10-22 on the final code: 30 of 127 (24%); silence 13 of 16, last word 7 of 16, doors 3 of 8 |
| Human blind ratings | **Unverified** | Pending |
| Mid-line yield on real audio | **Unverified**, release-blocking | Smoke run only, where a TV stopped Nova once; the full run is invalid (429) |
| Echo, false self-yield | **Unverified** | One trial each |
| Cold start within 60 s | **Pass** | 29.0 to 30.0 s measured |
| No silent date of fallbacks | **Pass** (tests) | The not-ready hold |
| Timed-out requests sent once | **Pass** (tests) | `tests/test_priority.py` |
| Claude stopping work or billing on cancel | **Unverified** | Assume billed |
| Classifier isolated from line writing | **Fail** | Shared slot; the gate made no difference |
| Dialogue to a provider only with approval | **Pass** for recent turns | Other flows need the privacy notice |
| Provider disclosure and consent | **Requires product decision** | No privacy notice; daters' audio already goes to Deepgram, and Nova's lines to Cartesia |
| Not-ready hold time limit | **Requires product decision** | The hold waits for the moderator, with no limit |
| Playful refusals get nothing (sign-off c) | **Fail**, safe direction | 3 of 5 alert the moderator |
| Branch split into reviewable changes | **Fail** | All uncommitted, in one working tree |
| Listener delay (decision 0007) | **Fail** | Not built |
| Playground rehearsal on the current code | **Unverified** | Last run 2026-10-18 |
| Native, Docker and lint | **Pass** | 1,785; 1,809 (5 skipped); clean |
