# Two-person full-length rehearsal (decision 0012)

Status: prepared, not yet run. **Human validation is pending** until two people have done
this. A synthetic dry run, scripted seats or a passing test suite is not human validation,
and no rating is ever filled in on someone's behalf.

## Who and what

- Two people, each playing one dater: **Maya** (left seat) and **Theo** (right seat). Use
  those names: Nova's floor rules listen for them. One of the two also operates, or a third
  person runs the agent and reads the report.
- Each on their own device, in separate rooms, **with headphones**. Otherwise Nova's voice
  reaches the other microphone and is heard as a dater.
- About 30 minutes: one full-length date (10 to 12 minutes) and one short date for the
  disconnect. About $0.20 of LiveKit credit a date (two speech-recognition inputs and
  Nova's voice).
- Their audio goes to LiveKit Cloud and speech recognition, and their words to the line
  model, as `docs/privacy/nova-data-flows.md` lists; show them the draft notice first.
  Nothing is recorded. Blind audio ratings need a recording, and so consent and an update
  to the data-flow docs first: until then, ask the questions at the end instead.

## Before

1. Ollama is running with `NOVA_LLM`'s model, and the machine is otherwise idle.
2. In `services/nova/.env`, set two distinct identities, for example
   `NOVA_LEFT_PARTICIPANT=dater-left` and `NOVA_RIGHT_PARTICIPANT=dater-right`. Leave
   `NOVA_LIVE_BRIDGES` unset (optional reactions off) and `NOVA_FULL_REHEARSAL`,
   `NOVA_CONVERSATIONAL_TURNS` and `NOVA_CONVERSATION_OVERRUN_SECONDS` at their defaults.
3. Check no other Nova worker is running (`docker ps`; never restart one someone else is
   using), then start the agent from the repository root:
   `docker compose -f services/nova/compose.yaml up --build`.
4. From `services/nova`, sign the join tokens: `uv run python -m nova.two_person`. Send
   each person the server URL and **their own** token, privately. A token lets whoever
   holds it into the room as that person for an hour.
5. Each person opens agents-playground.livekit.io, chooses **Manual**, enters the server
   URL and their token, and connects. Nova opens the date once both are in.

## The date

Times are from the doors and drift later as the conversation runs long. Each exercise says
what it checks.

| Segment | Do | Checks |
| --- | --- | --- |
| Doors | Both listen. | Nothing talks over Nova's intro. |
| First question, Maya | Say "Hmm, let me think.", stay quiet about 10 s, then answer for about 20 s with one short pause in the middle. | Thinking pause, long answer, no cut-off at the pause. |
| First question, Theo | Ask "Nova, do you mean ever, or this year?". Maya laughs briefly while Nova answers. Then Theo answers. | Clarification; a laugh doesn't cost Theo his answer. |
| Free talk | Maya asks "Do you cook, Theo?"; Theo waits about 3 s, then answers. Once, both talk at the same time for a few seconds. Theo tells a 40-second story without long pauses. Then both stay silent for about 15 s. Then talk normally. | Direct handover, overlapping speech, a long answer, a silence prompt. |
| Free talk, typed | Maya types `unlock` around 3:00, and `ask What's the best meal you've ever had?` and `ask Mountains or sea?` before 4:00. | The room's questions and the lightning round. |
| From the room | Theo says "You go first, Maya."; Maya answers. Answer the second question normally if it comes. | Handover between daters. |
| Lightning round | Answer each question in a word or two, both of you. | Quick turns. |
| Last words | Maya says one sentence. Theo says "Hmm.", waits about 6 s, then speaks. | Each keeps at least 20 s. |
| That's time | Each types `reveal` or `unseen` in **their own** chat. Don't tell each other. | Each decision is the typist's own; the window lasts 60 s. |
| Result | Listen. | The result at the end of the window. |

Late transcripts can't be staged: they happen on their own, and the report's utterance row
shows how long Nova waited for them.

## The disconnect

Sign new tokens (`uv run python -m nova.two_person`: a new room each time), open a second
date, and during free talk Theo closes his tab. The date should end there, and the report
should say "The session closed (participant_disconnected) before the date's end".

## A date with prefixes (decision 0012)

The product owner wants prefixes on in the final product, so run this date too. Only after
the load gate has passed on this machine
(`uv run python -m nova.conversation_trial`, from `services/nova`, with nothing else
running). Then:
1. Set `NOVA_LIVE_BRIDGES=true` in `services/nova/.env` and restart the agent.
2. Sign new tokens and run one more full-length date, as above.
3. Set it back to unset afterwards.

Nova may put a short lead-in before Theo's first question, the first room question and the
first last word: a quick reaction to what was just said ("A library book, then."). At most
three a date, and only at quiet moments: if anyone speaks while it is being written, Nova
asks the plain question. Listen for:
- a lead-in that doesn't fit what was just said;
- a lead-in that sounds forced, or praises or guesses at feelings;
- lead-ins that all sound alike (many open with "A");
- a longer pause before those questions than on the first date.

The report's Conversation row counts the prefixes said, obsolete (new speech came first) and
rejected (they failed their checks or took too long); each transition row says which. Ask
each person: did Nova's lead-ins feel natural, and did any feel wrong?

## After

The report is in `services/nova/rehearsals/` (codes and numbers only). Note:

- **Segments**: the order, doors to result, and how late each started.
- **Last words**: each at least 20 s unless the dater passed.
- **Decision window**: 60 s, give or take the one-second tick.
- **Interruptions**: how often a dater started under Nova, how often Nova stopped for them,
  and whether Nova ever started a line over a dater.
- **Targets**: replies within 2.5 s of the end of the dater's sentence.
- **Airtime**: Nova's share of the date and of the time anyone spoke, both well under a fifth.
- **Distress checks**: median, p95, p99 and timeouts.
- **Conversation**: extra waiting and the time recovered.
- **Transitions**: for each activity, when it came due and went, the quiet it needed, what
  held it back and for how long, and whether its question was heard in full. Note any held
  back by "unheard" for long.
- **Finals**: how late transcripts came after each person stopped (median, p95, worst), how
  many arrived while they were still speaking, and how many stops had no transcript within
  2 s. These decide whether the 1 s and 2 s assumptions hold.

The log has the same as a timeline (`transition`, `final`, `delivered` and `speech` events,
times in ms from the start), so a moment someone remembers can be matched to what Nova saw.
These are this run's real timings; the test suite's are synthetic and never stand in for
them.

Then ask each person, separately, and write down what they say in their own words: Did
Nova ever cut you off? Were there silences that felt too long? Did Nova answer what you
asked it? Did the date feel rushed or slow?

## Known issues to watch for

From the audit of 2026-10-27, not fixed yet (decision 0012):

- "You go first, Maya" gives Theo's own turn away.
- Answering Nova by name ("Well Nova, I'd say…") counts as a clarification, so Nova waits
  20 s before the next turn.
- Once the 60 s budget is spent, the other dater's "That's it." ends a protected last word.
- The budget counts time already recovered and time spent waiting for speech.
- Any delay at the room's questions drops the second question.
- Locked lightning slots stop the clock.

Changed on 2026-10-31 (decision 0012), to listen for:

- An activity held back 15 s by the conversation now goes in a 0.4 s pause, never while
  anyone speaks. Note any time Nova started in someone's thinking pause, and whether
  lively talk still held a segment back.
- "That's about it for me." lets Nova move on after 1.5 s instead of 2.5 s, including when
  its transcript arrives just before the person is heard to stop.
- Time spent talking, thinking, waiting on a handover or a question to Nova counts toward
  the 15 s, but never overrides them: Nova still waits for the thinker, the person handed
  the floor or the answer it owes. After that, it can come in 0.4 s later. A "need a moment"
  doesn't count toward the 15 s.
- If someone starts talking just as Nova starts asking a question, Nova stops and asks
  again in the next pause, no longer after 20 s. What they said before the question was
  asked never counts as its answer; an answer that runs over the end of the question does.
- A question Nova never finished (a pause, a newer line) owes nobody an answer, and isn't
  asked again.
- A cough or laugh with no transcript holds Nova back for at most 2 s, no longer up to 20 s.
  But Nova still waits for each transcript to arrive: if they always come after the next
  person has started, an activity can wait until the 60 s budget is used.
