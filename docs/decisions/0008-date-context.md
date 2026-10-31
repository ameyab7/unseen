# 0008: This date's context, for live replies and silence prompts

Date: 2026-10-21. Status: accepted.

2026-10-26: decision 0012 extends this same bounded, cleared context to optional live
prefixes on prepared questions. Generation and mandatory meaning checks obey the existing
recipient policy. Prefixes default off, never enter another date's memory, and never
replace the original question. Local floor signals retain no transcript.

## Context

Nova's live reactions saw only the moment: a reply saw the sentence it answers, a silence
prompt the blind cards. So Nova couldn't call back to something a dater said a minute
earlier, or link the two daters' answers. Tonight's memory (`nova/tonight.py`) is across
dates, masks names and exists to stop repetition; it must never hold what a dater said,
since it reaches every later date's prompts (decision 0005).

## Decision

- **A separate, per-date context** (`nova/date_context.py`, `DateContext`). Whoever runs the
  date makes one and hands it to the on-air listener and the line writer (the rehearsal
  does). It is not Tonight and never feeds it.
- **What it holds**, typed and bounded: the last 8 completed dater turns, each with its seat
  and whether it was said to Nova, each cut to 200 characters at a word; and the last 3
  lines Nova wrote and the room heard this date (set pieces and live reactions, never the
  fixed lines). Unanswered questions are derived from the turns: a question one dater asked
  the other that the other hasn't spoken since, never one put to Nova. Topic threads and
  tone are left out: reliable versions need another model call per utterance or guesses
  about how someone feels, and the recent turns let the line model find a real link itself.
- **Only what the checks cleared joins it** (`OnAirListener._act_on`), once the verdict is
  in: a whole utterance (not one cut at the 10 s cap, nor one the instant layer acted on),
  with the meaning check's verdict no and conduct none or unlabelled, not one the moderator
  was alerted to as a possible request, with no personal detail heard by conduct's instant
  layer or in the turn itself (`personal_detail`), at least 3 words, and not an echo of a
  line Nova said this date (word similarity 0.6 or more: its own voice leaking back). A
  check that failed or timed out keeps nothing. Chat commands never reach transcripts.
- **A turn held back still counts as speaking** (`DateContext.spoke`, 2026-10-21): only
  the fact, never the words. Otherwise a question one dater pressed ("So what do you
  actually do for work?") stayed an unanswered question once the other calmly declined (a
  maybe, so not kept), and a silence prompt could bring it back; before the change, one
  run of the evaluation's case wrote "let's move away from the office talk". A request to
  stop, a short answer and a turn with a personal detail count the same way. A question
  answered only by a turn a check held back (not one merely too short) is never shown at
  all: with it still among the earlier turns, a run wrote "since we're waiting on a job
  description"; without it, none of four runs mentioned work.
- **Acknowledgements take no place** (`acknowledges`, 2026-10-22). A turn under 3 words, or
  one of nothing but acknowledging words ("ha, so true", "mm, yeah, totally", "me too"),
  says the dater answered and is otherwise forgotten. Before, it took one of the 8 kept
  places and could take one of the 4 shown. On a sample date, "Mm, yeah, totally." was
  shown, and Maya's answer before it was pushed out.
  - Each turn now carries when it was said, so a question is answered when the other dater
    spoke after it, kept turn or not.
  - A turn a check held back is still kept, empty, so a declined question stays hidden.
  - An acknowledgement is nobody's evidence: "me too" isn't Theo saying he swims, so Nova
    can never say they both do (decision 0005).
- **What a prompt sees** (`LineWriter._this_date`): for a reply or a silence prompt only, a
  `this_date` object inside the data block, only when there is something: the last 4 turns
  under the first name of who said each, at most 2 unanswered questions, and Nova's own last
  lines. With a dater away, their turns, anything naming them and every unanswered question
  are left out. A reply never sees its own words twice. The instructions, by seat as ever,
  say to use it only for a genuine, specific link, never to put one dater's words in the
  other's mouth, invent something they share, suggest they suit each other or repeat a line
  of Nova's, and to reply simply (or use the cards, for a silence) when nothing fits. With
  nothing in the context the prompt is exactly what it was before.
- **The guardrails see it too**: every kept turn is passed as `echo_from`, so no proper
  noun a dater said earlier can reach the room, as for a reply's own words.
- **Tonight stays clean**: a silence line written with this date's context is never kept in
  tonight's memory (`Line.from_date`), as replies and audience questions never were.
- **Lifetime**: one date. Since 2026-10-24 (decision 0011), each date opens through a
  show's `Night` (`nova/show.py`), which makes its context and closes it when the date
  ends, however it ends, the moderator ending it before it opens included. A closed context
  is empty and takes nothing more, not even a turn whose check finishes late. The next date
  gets a new one.
- **The show runner is untouched**: it still decides every cue, its timing, safety and the
  decisions. The context only changes the words of a reply or a silence prompt the runner
  already asked for, and everything still passes the guardrails or falls back.

## Bounds and privacy

- Memory: at most 8 turns of 200 characters and 3 Nova lines of 200 characters, about 2.2 KB
  of text per date, in the agent's memory only. A prompt sees at most `SHOWN_CHARS` (1,000
  characters, about 250 tokens) of it, all told, since 2026-10-22. The oldest goes first:
  Nova's own lines, then turns, then questions. Before, only each part was bounded, up to
  about 2.7 KB in all. Measured on Gemma's tokenizer with a sample date of real answers and
  acknowledgements, a reply's prompt is 1,349 tokens and a silence's 1,566, of which this
  date is about 150. Replies with this date's context took 680 to 780 ms, against the 2 s
  live timeout.
- Nothing is written anywhere or logged. The `line` event gains one flag (`context`,
  whether the line's prompt had this date's context); `nova/events.py` still rejects text.
- What leaves the process (2026-10-22; since 2026-10-24, decision 0010). This date's turns
  go into a prompt only for a model at an approved-local endpoint, or once
  `NOVA_DIALOGUE_TO_PROVIDER` is true: the privacy policy (`nova/privacy.py`) decides it for
  each model at each request, and the LiveKit agent builds it from its settings. Otherwise
  replies and silence prompts work as before this
  decision, from the dater's own words and the cards. The context is still kept, locally, for
  the checks on Nova's lines (common ground, decision 0005). The evaluation and the
  benchmark are synthetic and always share it. Decision 0005 lists every flow to a provider.

## Consequences

- The agent for real shows makes one `DateContext` per date and gives it to the listener and
  the writer, as the rehearsal does. Today the LiveKit agent (`nova/agent.py`) is the only
  entrypoint, with one Rehearsal a session, and so one context a date. Since decision 0011
  it opens that date through a `Night`, as a show server running several dates must
  (`tests/test_show.py`).
- Nothing here is stored in Supabase or sent to analytics; no schema changes.
- The evaluation has four cases with earlier turns (a callback, a silence both daters
  spoke to, nothing to call back to, an injection in an earlier turn). The night has no
  dater speech, so it doesn't exercise the context, and its prompts are unchanged.

## Evaluation, 2026-10-21 (Gemma, `gemma4:26b`)

On the last run all four context cases passed: "Maybe something about that winter swim?"
(Theo had said he swims in the sea in winter); a silence after both daters spoke of flat
bread asked about "baking disasters" they both had; nothing was forced where nothing fit
("are you having a good night?" got no word about Maya's sister); an injection in an earlier
turn reached the room as nothing. The silence first used the cards anyway, twice: its
instruction said "built on a light detail from each of their blind cards" before the
context, and the model followed whichever came first. With context, that clause now says to
build on what both daters talked about, or else the cards. One night after the change, for
the parts the context doesn't touch: fallbacks 4%, near-duplicates 2%, same opening 19%,
airtime 19% (at most 20%), live lines within the timeout on date 8, as before.
