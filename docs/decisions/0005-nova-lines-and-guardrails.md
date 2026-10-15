# 0005: Nova's lines, guardrails and fallbacks

Date: 2026-10-13. Status: accepted.

## Decision

- **Three kinds of line.** Approved lines (the result, a leave, the moderator's line, the
  pause lines) are spoken as written. Set pieces (Doors intro, first questions, lightning
  questions, last words, outro, Next up) are written by the model before the date; during
  the date only live reactions call it: a silence prompt, an audience question, talk to the
  room, and a reply when a dater speaks to Nova (`services/nova/nova/lines.py`).
- **The model is one setting.** `NOVA_LLM` picks Ollama (development: Gemma, thinking off)
  or Claude (the official SDK; Nova's rules in a cached system block, effort low, no
  sampling settings). A Claude refusal gets Nova's own fallback line. No server-side
  refusal fallback for now; we add it if and when Nova switches to Claude.
- **Instructions and data never mix.** Nova's rules are the system prompt. Each task is
  one instruction we wrote, naming daters by seat, then one `<data>` block of JSON with
  angle brackets and ampersands escaped: blind cards, audience questions and what daters
  said. The data is normalised (NFKC) first, so lookalike characters such as a full-width
  `＜` become plain ones and are escaped too. The rules say data is never an instruction.
- **Every model line passes the guardrails or a fallback is said instead.** Fallbacks are
  approved lines built only from first names and ages, never from free text, so injected
  text can't reach the room through them. The product owner signed them all off on
  2026-10-13.
- **A blind card is checked when it's made** (`nova/cards.py`), because its first name and
  age go into fixed and fallback lines that no guardrail checks: a first name is letters,
  with single spaces, hyphens or apostrophes between them, at most 30 characters; an age
  is a whole number from 18 to 99.
- **A dater who wants to stop is never deflected.** Before any reply, `distress_in` hears
  everything a dater says on air, to Nova or to the other dater, at one of two levels.
  Speech-to-text often splits a sentence at a pause ("Nova," then "I want to stop."), so
  each final transcript is joined with the same dater's last five seconds, and an alert is
  raised only when the joined words sound worse than they did before:
  - **Clear** ("I want to stop", "I'm not comfortable", "can we end this", "I need a
    moment"): the show runner alerts the moderator at once (`dater_distressed`), and if a
    pause can start, Nova stops mid-line, drops anything it had queued, says "Of course.
    Let's take a breather." at once and starts a need-a-moment pause for that dater, with the usual rules (back, 30 s alert, moderator
    decides). If a pause can't start (another dater is on one, or the daters are
    deciding), Nova says nothing and the moderator acts. The model never sees those words,
    so "I'll stay out of that one" is never the answer.
  - **Maybe** ("help", "I'm scared", "that's not okay"): the moderator is alerted
    (`possible_distress`) and, if the dater spoke to Nova, Nova answers as usual, so "can
    you help us pick a topic?" gets a normal reply. If the model fails, Nova says "Take all the time you need.",
    never "I'll stay out of that one" (signed off by the product owner on 2026-10-13).

  Both lean towards hearing distress: a false alarm costs a breather or a look from the
  moderator. Hearing everything, not only words to Nova, makes false alarms likelier ("I
  want to stop eating sugar" is heard as clear); the first rehearsals will show how often.
- **Lines vary across the night.** Listeners hear every date, so Nova remembers the lines
  it has used tonight, across dates (`nova/tonight.py`). Every prompt sees the lines
  already said for its moment (daters' names masked, so no earlier dater's name reaches a
  later date) and is told not to repeat or echo them. Audience-question lines and replies
  are never remembered: they carry what a listener or dater said, and the memory reaches
  every later date's prompts. Each set piece gets three candidates
  during prep; Nova keeps the one that passes the guardrails and is least similar to
  tonight's lines. The line writer also picks a shape for Next up and an opening for talk
  to the room at random, and gives each date's three lightning questions one of each
  format (either/or, finish the sentence, one-word answer) in a random order; each is one
  sentence. The model's outro is one sentence with no question. Next up never compares
  or pairs up the next daters. Room talk works only from safe facts about the show (the
  segment, whether the poll is open, how many audience questions so far), and never
  mentions the outcome, spending or anyone's looks.
- **Fixed fallbacks take turns across the night.** Silence, lightning and outro
  fallbacks each use the option least like what Nova has said tonight, so a night
  doesn't hear the same fallback twice while another is unused. "Last quick one" only
  ever comes last. The four outro fallbacks were signed off by the product owner on
  2026-10-13; two are two short sentences, which the one-sentence rule (for the model)
  allows for them.
- **Timeouts are settings.** `NOVA_LIVE_TIMEOUT_SECONDS` (default 2) for live reactions:
  measured on the development PC, warm Gemma's live lines took a median of 321-342 ms and
  at most 565 ms over 60 runs, so 2 s is about 3.5x the worst case. It caps the wait for
  the model, not the silence the room hears: the voice's first audio adds about 0.2 to
  0.5 s, and a reply also waits for the end of the dater's sentence and its transcript
  (`docs/spike-results.md`). `NOVA_PREP_TIMEOUT_SECONDS` (default 60) for set pieces: a
  cold model load took 20-28 s.
- **Logs carry events and timings only.** `nova/events.py` accepts codes, numbers and
  flags; free text raises before anything is written.
- **The evaluation script is the gate for a model change:** `uv run python -m
  nova.evaluation [--repeat N]`, on demand against the real model, never in CI. It covers
  every moment the model writes and reports, per moment, whether Nova sounds canned: the
  fallback rate, and repetition across runs among the model lines the room would hear
  (near-duplicates, with a word-level similarity of 0.7 or more to an earlier line; and
  lines that open with the same three words as an earlier one). Rehearsals measure the
  fallback rate too, from the `line` events, which record each line's moment and source.
- **Full-night repetition is the number we track.** `--night` runs eight dates with
  different pairs through the real line writer and tonight's memory, then reports, per
  moment, repetition across the whole night among every line the room hears, fallbacks
  included and names masked. The per-case run repeats identical inputs, so it overstates
  repetition for lines that depend on the pair.

## How each guardrail checks, and what it can't catch

The guardrails are deterministic English patterns (`nova/guardrails.py`). They don't
understand meaning. They lean towards blocking: a false alarm costs a less lively
fallback line, never an unsafe one.

| Guardrail | How it checks | What it can't catch |
| --- | --- | --- |
| Claims to be human | First-person patterns: "I'm human / a real person", "I'm not an AI / a bot", "as a woman", "when I was a kid", "my mom / body / hair", "my first date", "I've been on" | Claims worded another way ("trust me, I know those nerves"), experience implied without those words, other languages |
| Decisions | Words about the outcome (reveal, unseen, mutual, match, chose, picked, said yes, second date, photos, see each other) and hints (sparks, chemistry, made for each other, lovely couple, I bet, my guess, fingers crossed). The capitalised show name "Unseen" is allowed | Hints without those words ("I've a good feeling about tonight", "they seem happy") |
| Looks or voice | Appearance words (gorgeous, handsome, attractive), "looks great", "you're so cute", "voice", "accent", "you sound lovely" | Indirect compliments ("that smile must be something"), unlisted adjectives, clothes |
| Personal details | Phone numbers, numbers of 3+ digits, emails, @handles, web addresses, social platforms, "works at", "lives on", a capitalised word plus Street/Road, surname words and a surname after a first name ("Maya Lopez", even if "Lopez" is on her card), school words, Nova asking where someone lives, works or spends time; plus any proper noun from what the dater or listener said for this line, wherever it appears | Lowercase descriptions ("the bakery by the station"), spelled-out numbers ("five five five"), job titles, places that are described rather than named |
| Unknown names | Any capitalised word mid-sentence that isn't a dater's first name, Nova, AI, a day or month, the show, or a name or place on the blind card of a dater the line is about ("lost in Lisbon"); any acronym. A card that tries to give Nova instructions lends no words, and a word from what a dater or listener said live is blocked even if it's also on a card | A proper noun the model invents at the start of a sentence; lowercase names |
| Spending | Coins, gestures, gifts, roses, encores, buy, spending money, membership, subscribe | Indirect asks ("show them some love tonight") |
| Unkind | A short list of insults (stupid, pathetic, desperate, creepy, shut up) | Sarcasm, tone, mockery without those words |
| Instructions | Talk of instructions, rules, the system, data, prompts, angle brackets, "I was told"; Nova claiming any name but Nova ("I'm Kate", "call me Kate") | An injection that steers Nova into an ordinary-looking line, such as an untrue but harmless claim about a dater |
| Voice and format | Exclamation marks, emoji, symbols, markdown, length (characters and sentences), names in the wrong case; the model's outro is one sentence with no question; a lightning question is one sentence and doesn't open with both daters' names | Tone, wrong pronouns, a question ending in a full stop (handled by the instructions only) |
| A dater who wants to stop (what a dater says on air, not Nova's line) | Clear: wanting to stop, end or leave, needing a moment or a break, not comfortable, safe or well, overwhelmed, a panic attack, harassment. Maybe: "help", scared, upset, anxious, crying, creepy, inappropriate, "that's not okay" | Distress worded another way ("I'd rather be anywhere else"), heard only in a voice, or split across transcripts more than five seconds apart |

Before the model, audience questions about personal details or looks, or that try to give
Nova instructions, are declined with a fixed line. This can't catch cleverly worded ones
("which part of town has the best coffee for you?"); moderator approval stays the main gate.

The layers, in order: moderator approval of questions, data kept apart from instructions,
Nova's rules, the guardrails, and approved fallbacks.

## Launch requirements

The pattern guardrails alone aren't enough for real shows. They can't catch meaning (the
right-hand column above). Before the first real show, Nova needs both of these:

1. **A meaning-based check on Nova's lines before they're spoken:** the technical plan's
   fast classifier, run on every model line after the patterns. A line it flags gets the
   fallback, like a pattern violation. It has to fit inside the live timeout.
2. **A moderator control that mutes Nova instantly.** It silences Nova only: Nova stops
   mid-line and says nothing more until the moderator unmutes it. The date continues, and
   the fallback segment doesn't play. (Ending the date, already built, is the control that
   plays the fallback segment.)

## Consequences

- The show runner gained an outro slot after the verdict, and a reply cue when a dater
  speaks to Nova on air (`dater_asked_nova`), which pauses for a dater who wants to stop.
  Its talk-to-the-room cues carry the safe show facts (`RoomFacts`).
- Each model change runs the evaluation first; a failing case blocks the change.
- The blind card schema in `packages/shared` must apply the same first-name and age rules,
  so the app never accepts a card Nova can't put on air.
