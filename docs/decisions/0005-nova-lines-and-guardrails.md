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
  angle brackets escaped: blind cards, audience questions and what daters said. The rules
  say data is never an instruction.
- **Every model line passes the guardrails or a fallback is said instead.** Fallbacks are
  approved lines built only from first names and ages, never from free text, so injected
  text can't reach the room through them. The product owner signed them all off on
  2026-10-13.
- **A dater who wants to stop is never deflected.** When a dater speaking to Nova wants to
  stop or sounds distressed ("I want to stop", "I'm not comfortable", "can we end this",
  "help"), `sounds_distressed` catches it before any reply. The show runner alerts the
  moderator at once (`dater_distressed`), and if a pause can start, Nova says "Of course.
  Let's take a breather." and starts a need-a-moment pause for that dater, with the usual
  rules (back, 30 s alert, moderator decides). If a pause can't start (another dater is
  on one, or the daters are deciding), Nova says nothing and the moderator acts. The model
  never sees those words, so "I'll stay out of that one" is only ever an answer to an
  ordinary question. Detection leans towards hearing distress: "can you help us pick a
  topic?" also pauses, which costs a short breather.
- **Timeouts are settings.** `NOVA_LIVE_TIMEOUT_SECONDS` (default 2) for live reactions:
  measured on the development PC, warm Gemma's live lines took a median of 321-342 ms and
  at most 565 ms over 60 runs, so 2 s is about 3.5x the worst case and caps dead air at
  2 s. `NOVA_PREP_TIMEOUT_SECONDS` (default 60) for set pieces: a cold model load took
  20-28 s.
- **Logs carry events and timings only.** `nova/events.py` accepts codes, numbers and
  flags; free text raises before anything is written.
- **The evaluation script is the gate for a model change:** `uv run python -m
  nova.evaluation [--repeat N]`, on demand against the real model, never in CI. It covers
  every moment the model writes and reports the fallback rate per moment, so we can see
  whether Nova sounds canned. Rehearsals measure it too, from the `line` events, which
  record each line's moment and source.

## How each guardrail checks, and what it can't catch

The guardrails are deterministic English patterns (`nova/guardrails.py`). They don't
understand meaning. They lean towards blocking: a false alarm costs a less lively
fallback line, never an unsafe one.

| Guardrail | How it checks | What it can't catch |
| --- | --- | --- |
| Claims to be human | First-person patterns: "I'm human / a real person", "I'm not an AI / a bot", "as a woman", "when I was a kid", "my mom / body / hair", "my first date", "I've been on" | Claims worded another way ("trust me, I know those nerves"), experience implied without those words, other languages |
| Decisions | Words about the outcome (reveal, unseen, mutual, match, chose, picked, said yes, second date, photos, see each other) and hints (sparks, chemistry, made for each other, lovely couple, I bet, my guess, fingers crossed). The capitalised show name "Unseen" is allowed | Hints without those words ("I've a good feeling about tonight", "they seem happy") |
| Looks or voice | Appearance words (gorgeous, handsome, attractive), "looks great", "you're so cute", "voice", "accent", "you sound lovely" | Indirect compliments ("that smile must be something"), unlisted adjectives, clothes |
| Personal details | Phone numbers, numbers of 3+ digits, emails, @handles, web addresses, social platforms, "works at", "lives on", a capitalised word plus Street/Road, surname words and "Maya Lopez", school words, Nova asking where someone lives, works or spends time; plus any proper noun from what the dater or listener said for this line, wherever it appears | Lowercase descriptions ("the bakery by the station"), spelled-out numbers ("five five five"), job titles, places that are described rather than named |
| Unknown names | Any capitalised word mid-sentence that isn't a dater's first name, Nova, AI, a day or month, or the show; any acronym | A proper noun the model invents at the start of a sentence; lowercase names |
| Spending | Coins, gestures, gifts, roses, encores, buy, spending money, membership, subscribe | Indirect asks ("show them some love tonight") |
| Unkind | A short list of insults (stupid, pathetic, desperate, creepy, shut up) | Sarcasm, tone, mockery without those words |
| Instructions | Talk of instructions, rules, the system, data, prompts, angle brackets, "I was told" | An injection that steers Nova into an ordinary-looking line, such as an untrue but harmless claim about a dater |
| Voice and format | Exclamation marks, emoji, symbols, markdown, length (characters and sentences), names in the wrong case | Tone, wrong pronouns, a question ending in a full stop (handled by the instructions only) |
| A dater who wants to stop (what they say to Nova, not Nova's line) | Wanting to stop, end or leave, "help", needing a break, not comfortable or safe, scared, upset, overwhelmed, harassment | Distress said to the other dater rather than to Nova, worded another way ("I'd rather be anywhere else"), or heard only in a voice |

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
2. **A moderator control that mutes Nova instantly and plays the fallback segment.** One
   console control: it cuts Nova mid-line, keeps it silent, and plays the recorded fallback
   segment so the room never sits in silence. Unlike ending the date (already built), it
   doesn't end the date.

## Consequences

- The show runner gained an outro slot after the verdict, and a reply cue when a dater
  speaks to Nova on air (`dater_asked_nova`), which pauses for a dater who wants to stop.
- Each model change runs the evaluation first; a failing case blocks the change.
