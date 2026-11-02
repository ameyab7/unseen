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
who just passed is never the one asked (an easy win becomes an either-or for both). Words to
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
