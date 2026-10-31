# Dater notice: Nova (AI) and your voice

**DRAFT / NOT SHIPPED.** Don't put this in the app, a store listing or any page daters see.

## Status (for the team, not part of the notice)

- Written 2026-10-24 from the audit in `docs/privacy/nova-data-flows.md`, to match what
  the code does today. It covers only Nova's part of a date: the rest of the privacy
  policy (accounts, ID checks, photos, recording, clips, payments, analytics) isn't
  written.
- `services/nova/tests/test_data_flows.py` fails if any of these happens:
  - a configuration sends daters' data to a third party this notice doesn't name;
  - a third party receives conversation-derived safety context (each utterance with both
    daters' 15 seconds before it) or dialogue memory (this date's recent turns) without a
    paragraph saying so;
  - the paragraph about the model that writes the lines claims it gets the safety context;
  - this file stops saying it's a draft not shipped;
  - it makes a claim nobody has established: that data is never stored, never leaves,
    isn't shared, or is deleted after a set time.
- The checks run on `NOVA_CLASSIFIER_LLM`, or on `NOVA_LLM` when that's unset. A provider
  running them is a production approval decision (decision 0010). It receives every
  utterance with both daters' 15 seconds before it, for the whole date, so it needs
  provider review and the **[IF ANTHROPIC RUNS THE CHECKS]** paragraph before a real
  dater meets it.
- It can't ship until:
  1. each **[TO CONFIRM]** below is answered from a contract or the provider's terms,
     never assumed;
  2. for the model that writes the lines and the one that runs the checks, the **[IF ...]**
     paragraph that matches real shows is kept, and `uv run python -m nova.data_flows`
     agrees with them for those settings;
  3. legal signs it off with the terms, privacy policy and on-air release;
  4. the app has a place to show it before a dater goes on air.
- If the code changes what goes where, change this in the same pull request.

## The notice

**Nova (AI) and your voice**

Nova, the show's host, is an AI. To host your date, it listens to it.

**What Nova hears.** While you're on a date, everything your microphone picks up goes as
audio to LiveKit, which runs the show's sound. LiveKit sends it to Deepgram, which turns
speech into text. This carries on for the whole date, pauses included, and while Nova is
muted.

**What Nova does with it.** Nova uses the text to answer you, to notice when you want to
stop or need help, and to tell a human moderator when something breaks the show's rules.
These checks cover everything said on your date, including while Nova is muted.

Nova also uses the text to recognize thinking pauses, questions and handovers, so it can
wait while you talk to each other and introduce the show's activities at a pause. When
enabled, brief host transitions use the same recent conversation context described below.

**The AI models behind Nova.** One AI model writes Nova's lines. Another runs the checks
above, and checks each line before Nova says it.

[IF THE LINES ARE WRITTEN ON COMPUTERS WE RUN: The model that writes Nova's lines runs on
computers we run. [TO CONFIRM: where those computers are, and who else can reach them.]]

[IF ANTHROPIC WRITES THE LINES: Claude, an AI model made by Anthropic, writes Nova's lines.
To write them, Anthropic receives your first name, age, pronouns and card answers,
questions the audience sends you, and what you say to Nova. Lines Nova said on your date
can be sent again, with names taken out, while it writes lines for later dates that
night. [ONLY IF NOVA_DIALOGUE_TO_PROVIDER IS TURNED ON: It also receives what you and your
date said earlier on the date.]]

[IF THE CHECKS RUN ON COMPUTERS WE RUN: The model that runs the checks runs on computers we
run. [TO CONFIRM: where those computers are, and who else can reach them.]]

[IF ANTHROPIC RUNS THE CHECKS: Claude, an AI model made by Anthropic, runs the checks. For
the safety checks, Anthropic receives each thing you say, with what you and your date said
in the 15 seconds before it, for the whole date, including while Nova is muted. To check
Nova's lines, it receives the lines, and your first name, age and card answers. [ONLY IF
NOVA_MEANING_LIVE IS TURNED ON: For lines during the date, it also receives what you say to
Nova and questions the audience sends you. [ONLY IF NOVA_DIALOGUE_TO_PROVIDER IS ALSO ON:
It also receives what you and your date said earlier on the date.]]]

**Nova's voice.** Nova's lines go as text to LiveKit, which sends them to Cartesia to be
spoken. A line can include your first name, something from your card, or something you
said.

**What Nova keeps.** Nova doesn't record your date. It holds what you say in memory while
your date runs, and lets go of it when the date ends. Nova's logs record what happened and
when, such as that a check ran, not what anyone said and not anyone's voice.

**What these companies keep.** LiveKit, Deepgram and Cartesia receive what's described
above [IF ANTHROPIC WRITES THE LINES OR RUNS THE CHECKS: and so does Anthropic]. [TO CONFIRM, for each: how long it keeps what
it receives, where, and whether any of it is used to train AI models.]

**Questions.** [TO CONFIRM: who is responsible for your data, and how to reach them.]
