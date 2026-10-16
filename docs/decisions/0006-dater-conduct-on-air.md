# 0006: Dater conduct on air

Date: 2026-10-16. Status: accepted for detection and the brand book's lines; the automatic
mute is left to the product owner (below).

## Context

Decision 0005 hears a dater who wants to stop. Nothing heard a dater behaving badly: a
dater being harassed who freezes was invisible. The brand book (Nova on air) scripts two
moments the runner didn't have, a personal detail slipping and an unkind remark, and the
technical plan (Real-time audio and the AI host) asks for a classifier for harassment,
sexual content, hate, minors, and addresses or workplaces.

## Decision

- **Two layers, on every closed utterance** (`services/nova/nova/on_air.py`), like the
  distress check:
  - **Instant, with no model** (`nova/conduct.py`): a personal detail slipping on air.
    Case-insensitive, since speech-to-text often doesn't capitalise: a street or address
    ("i live on mercer street", "14 cardew road", "my address is ..."), a phone number in
    digits or read out in words, an email, a handle, a web address, and what follows a
    cue like "i work at", "i studied at" or "my insta is" (decision 0005's slot words).
    Narrower than the guardrails on Nova's lines: "I hated school", "my favourite street
    food" or "I live in Manchester" (a city) don't count, and nor does "I work at being a
    better listener".
  - **Meaning**: the distress check's call (decision 0005) also returns a conduct label,
    in the same structured output, so the number of calls doesn't grow: none,
    personal_detail, unkind, harassment, sexual, hate, threat, minor or unsure. It's about
    what the speaker's own words do; the yes, maybe or no label stays about whether the
    speaker needs the moderator.
- **Actions** (`ShowRunner.dater_conduct`), on air; off air, only the alerts:

  | What | On air | Console |
  | --- | --- | --- |
  | A street or address slips | Nova says "Let's keep street names off air. The story's better without them anyway." (brand book) once the dater stops, never over anyone | `personal_detail_on_air` |
  | Another personal detail (workplace, school, handle, phone, email) | Nothing; lines for these wait for sign-off in `docs/sign-off/2026-10-conduct.md` | `personal_detail_on_air` |
  | Unkind, the first time in a date (per dater) | "Let's keep it kind." (brand book), once the dater stops | nothing |
  | Unkind, the second time by the same dater | Nova stops, says "We're taking a short pause." (brand book), and the show clock holds for both daters; Nova covers as for any hold (decision 0004) | `conduct_flag` with `unkind`; the moderator resumes or ends the date |
  | Harassment, sexual content, hate, a threat, anything about a minor | Nothing | `conduct_flag` with the category, at once |
  | Unsure | Nothing | `conduct_flag` with `unsure` |

  A personal detail the instant layer heard isn't flagged again when the model labels it
  too. The show-wide hold is not a dater's need-a-moment: neither dater's return ends it,
  only the moderator.
- **No automatic mute in this change.** The technical plan allows Nova to mute a dater
  instantly; whether harassment, hate, a threat or a minor should cut the dater's audio
  before a moderator acts is for the product owner. The flag reaches the console at once
  either way.
- **Privacy**: nothing logs or stores the words. Events and alerts carry codes only
  (`conduct=harassment`), as for distress.
- **The conduct set** (`nova/conduct_set.py`): written fresh, in a dev half (the rules
  are tuned on it) and a held-out half (the measure), each with ordinary look-alikes.
  `uv run python -m nova.evaluation --conduct` reports, per category, recall and false
  alerts on ordinary talk, with Wilson 95% intervals.

  First run, 2026-10-16, Gemma (`gemma4:26b`), 40 sentences a half (28 conduct, 12
  ordinary): every street, personal detail, unkind, sexual, hate and minor sample caught
  in both halves; harassment 3 of 4 in each; threat 3 of 3 (dev) and 2 of 3 (held out,
  missed: "I'll be waiting outside when the show finishes."). Ordinary talk left alone 11
  of 12 (dev) and 9 of 12 (held out: "My mum lives near the sea" heard as an address, "I
  work at keeping my plants alive" as a workplace, since fixed for any "work at" followed
  by a verb, and "You're useless at lying, ha" as unkind). The sets are small: every
  interval is wide.

  Adding the conduct label to the distress check's call changed its results a little:
  held-out, 50 of 51 real distress paused, 1 missed, no false pauses; dev, 1 missed and 3
  false pauses of 60 (5.0%: "a cooking edition", "go home and bake that bread" and "tap
  out of the quiz round"). The rules weren't tuned again after the held-out run.

## What it can't catch

Tone without words (sarcasm, a laugh at someone), slow-building pressure spread across
many utterances, details described rather than named ("the bakery by the station"), and
anything the model misjudges; the evaluation measures the last.

## Consequences

- Decision 0005 lists this as the third launch requirement.
- The moderator console shows `personal_detail_on_air` and `conduct_flag` with its
  category, the second as urgent as a report for harassment, a threat or a minor.
- A listener delay (decision 0007, proposed) would let a moderator, or the instant layer,
  keep a slipped detail from reaching listeners at all.
