# 0013: Nova steps in when the conversation goes wrong, not only when it stops

Date: 2026-11-02. Status: built, off by default (`NOVA_STEERING`) until a rehearsal with two
people accepts it.

## Context

Nova's only cue to step in was a long silence (decision 0004). In the full-date simulation
(decision 0009, 2026-11-02) it sat through two minutes of one dater firing heavy questions
while the other apologised more with every answer: the talk never stopped, it went wrong.
The product owner asked for the scenarios first, positive, negative and edge cases, and
approved them as listed below.

## Decision

`nova/steering.py` reads each dater's finished turns as numbers and flags only (word count,
a question, a self put-down, a pass), never their words, and the show runner
(`ShowRunner._steer_due`, `_step_in`) decides when Nova may act on them.

**Nova steps in when** (one line, at the next gap):

| Signal | What Nova does |
| --- | --- |
| A dater puts themselves down or apologises in 3 turns within 2 minutes | An easy win: a light question about something on their own card |
| One dater asks 4 or more questions in 2 minutes, two thirds of their turns or more, and the other asks at most one back | Turns it round, playfully: the asker answers one themselves |
| One dater has 3/4 of the words over 2 minutes and the other only says "yeah" | Brings the quiet one in, by name |
| A dater passes and the other comes straight back with a question | Moves the talk somewhere new for both; never sees this date's context |
| Six short turns in a row, no questions, over 45 s or more | A quick either-or for both |
| Talking over each other 3 times in a minute | The brand book's "One at a time. I want both of those stories. {name} first.", once a date |

**Nova stays out**: banter, a long story the other is into, a conversation both want (the
signals need one side to be stuck); "sorry" as manners or asking again; the first minute of
free talk; within 45 s of a line of Nova's or a silence prompt; while a question of Nova's is
still being asked, anyone speaks or the floor holds; with a beat due within 20 s; on a hold
or with a dater away; for 60 s after any distress verdict but no, an unchecked check or a
conduct flag (the moderator's); once Nova has more than a fifth of the last three minutes'
airtime; after a dater tells Nova to stay out ("Nova, let us talk"), for the date; more
than twice a date, within 2 minutes of the last, or the same kind twice running. A dater
who passed in the last 2 minutes is never the one asked (an easy win, turning it round or
bringing them in becomes an either-or for both; 2026-11-03, below). Words to
Nova aren't the daters' conversation. Another language never fires the English signals.

**The line**: the model writes it (`prompts.steer`), never commenting on how the conversation
has been going: not apologies, nerves, who has been talking or asking, or what was declined.
It passes the guardrails like any live line; if it's late, fails or is rejected, Nova says
nothing, so no new fallback line is needed. A step-in is optional: new speech voids it, like
a silence prompt (`rehearsal.py`). It counts as a nudge, so no silence prompt follows it
straight away.

## Measured

`tests/test_steering.py` holds every scenario above, and each guard's test fails without
the guard. The date benchmark's "today's Nova" steps in (`--no-steering` turns it off); full
dates report how often and which kind. Nova's share of the conversation must not rise
(decision 0009). Whether it helps is for people's blind ratings and a two-person rehearsal.

On Gemma, 2026-11-02, the nervous overthinker's full date stepped in once in one run (an
easy win for Wyn, after their third apology) and never in the next, with the same signal
up from two minutes in. Replayed offline, the next run's guards left one gap: the first
minute of free talk, then 45 s after Nova's silence prompt and its reply, then Nova at 22%
of the last three minutes' airtime (the first questions still in the window), then the
room's question due. The guards worked as written here; whether they are too strict is for
the rehearsal.

## A pass is remembered, and "let us talk" gets no question, 2026-11-03

In the Gen Z dates (decision 0009, 2026-11-02) two step-ins went wrong:

- **The easy win asked about what Remy had declined.** He said "I'd rather not say" about
  his worst first date, apologised three more times, and the easy win asked him, by name,
  about that very date. The guard looked only at his latest turn, and the topic came from
  his own card ("Ask me about: the worst first date of my life."): this date's context
  already hides a declined question (decision 0008), but the card still held it. Now a pass
  anywhere in the last 2 minutes counts (`Steering.due`), for every step-in that names one
  dater. And the line writer rejects a line Nova starts itself (a step-in or a silence
  prompt) that takes up a question a dater declined, even from their card
  (`grounding.takes_up`: two of the question's words, or its only one, filler and names
  aside; `Violation.DECLINED`). A rejected step-in says nothing; a silence prompt says its
  approved fallback. A reply may come back to it: it answers the dater's own words. A
  question answered with a pass the checks let through now counts as declined too
  (`DateContext.declined`), as a held boundary did.
- **"Nova, we've got this, let us talk." got "You're the boss, Milo, so tell me more about
  this lost footwear."** Now that reply steps back (`Say.step_back`, `prompts.STEP_BACK`):
  a few warm words, no question, no topic, without this date's context; a question in it is
  rejected, and then Nova says nothing, so no new line needs sign-off. It doesn't depend on
  `NOVA_STEERING`: the words are heard whether or not stepping in is on
  (`steering.asks_nova_to_stay_out`).

Tests: `tests/test_steering.py`, `tests/test_date_context.py`, `tests/test_grounding.py`.
The evaluation has a case for the step-back reply.

On Gemma, 2026-11-03, made-up daters:
- **The evaluation**, before and after: line cases 42 of 45 before, 44 of 46 after; the new
  case passed ("Understood, Maya. I'll step back and leave you to it."). The distress dev
  set unchanged (70 of 73 paused, the same 3 known misses, 0 false pauses, 2 false alerts,
  0 unchecked); conduct, banter and meaning checks pass.
- **The eight full dates** (`benchmark --full --dates all --bridges`), every run valid: in
  Remy's date the step-in became the either-or for both ("Remy, would you rather memorize a
  hundred espresso orders or Zoe, would you rather defend pineapple on pizza to a room full
  of critics?"), nothing about the date he declined; "Nova, we've got this, let us talk."
  got "Understood, Milo. I'll step back and leave you both to it." (and, from the baseline,
  which shares the show runner, "I'll step back then, Milo. The floor is yours."). Stepping
  in came as written in 4 of the 5 Gen Z dates; the dry texters' flat stretch still got no
  either-or (Nova's recent airtime). The check against declined questions rejected no line.
  Two gates failed, both on the frozen distress check, which nothing here changed: its yes
  for the boundary tester's own overshare ("I work at Northwind Bank on Harbour Street...
  Where do you work?") paused him, in today's Nova's run; and a maybe on one of the roast's
  lines alerted the moderator, in the baseline's.
