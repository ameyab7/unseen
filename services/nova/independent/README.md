# The independent held-out set

For the product owner or a moderator: whoever writes this set must not be the developer of
Nova's distress check (decision 0005). It is the measure of whether the check hears daters
the way the show has decided it should, before the first real show.

## Before you start

Don't open these, so that nothing you write is shaped by what the check was built on:
`services/nova/nova/distress.py`, `distress_dev.py`, `distress_set.py`, `conduct_set.py`,
and the measured runs in `docs/decisions/0005-nova-lines-and-guardrails.md` and `0006`
(they quote test sentences). This page tells you everything you need.

Your file stays on this computer. It is never committed (`.gitignore`), and no test reads it.
Nobody but the evaluation opens it, and the evaluation prints only counts, never a
sentence.

## What the show does (the decided policy)

Each thing a dater says gets one of three outcomes:

- **pause**: the date pauses for them, and the moderator is alerted;
- **alert**: the moderator is alerted to listen in, and the date goes on;
- **nothing**: ordinary conversation.

The product owner decided (2026-10-22):

- A dater who wants the date to stop or pause, wants to leave or get away from the other
  dater, or says they feel unsafe, uncomfortable, unwell, scared or harassed right now:
  **pause**. They often say it indirectly: asking how long is left, wishing they were home,
  their body in trouble, asking for someone from the show, or a short, flat or shaken
  answer to something invasive, controlling or threatening.
- (a) A clear refusal of something personal or intrusive, however it's said, pleading,
  repeated, or in answer to pressure or a demeaning remark: **alert**.
- (b) A calm boundary (declining to say where they live or work, their number, surname or
  photos, or asking why the other dater keeps asking): **alert**.
- A refusal that answers a threat or the other dater knowing their details, or that also
  says they want it to end, want to leave, or feel upset, uneasy, unsafe or scared:
  **pause**.
- (c) A playful refusal of something light (a favourite biscuit, a karaoke song):
  **nothing**.
- (d) Opting out of a game or a round of the show: **nothing**.
- (e) The dater who presses after a no, or asks for the other's details: **nothing** for
  them here (their conduct is flagged to the moderator another way).
- (f) Asking for someone from the show: **pause**.
- Ordinary talk that uses the same words about something else (stopping a habit, leaving
  a job, a scary film): **nothing**.

If something you want to write isn't covered, label it as you think the moderator would
want, and tell the product owner: that label is a policy question, not a test result.

## What to write

Write fresh sentences, as daters really talk, about 120 in all:

- at least 50 that should **pause**, most of them indirect, and at least 10 using none of
  the obvious words (stop, leave, help, scared, uncomfortable);
- at least 50 that should get **nothing**, many using those same words about something else;
- 15 to 20 that should **alert**: refusals and boundaries of every kind above;
- some written as speech-to-text gives them (lowercase, no punctuation, "um", repeated
  words), and about 10 split in two where a speaker would pause ("Can we stop?" then more);
- some that answer what the other dater just said, with that line as `other`.

## The file

Save it as `services/nova/independent/distress.json`:

```json
{
  "author": "your name",
  "written": "2026-11-02",
  "samples": [
    {"said": "<what the dater says>", "expected": "pause"},
    {"said": ["<first part>", "<second part, after a pause>"], "expected": "nothing"},
    {"said": "<the reply>", "other": "<what the other dater said first>", "expected": "alert"},
    {"said": "<the sentence>", "earlier": "<what the same dater said just before>", "expected": "nothing"}
  ]
}
```

## Running it

From `services/nova`, with the model the show will use running:

    uv run python -m nova.evaluation --independent

It first checks that no sample is too close to anything the check was built or tuned on.
If some are, it doesn't run, and it names them by position in your file (1 is the first)
so you can rewrite them. Then it runs every sample and prints counts and targets only, with
a short fingerprint of your file.

Record each run's counts and fingerprint in decision 0005. A run whose results lead to a
change to the check has been used: the next measure needs a new set, unless the product
owner decides otherwise.
