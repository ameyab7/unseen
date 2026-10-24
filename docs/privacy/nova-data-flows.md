# Nova's data flows: privacy audit

Status: audit of the code in this repository, 2026-10-24. Not a legal review, and not a
statement of compliance. The dater notice drafted from it
(`docs/privacy/dater-notice-draft.md`) is **DRAFT / NOT SHIPPED**.

Scope: Nova's LiveKit agent (`services/nova/nova/agent.py`, LiveKit Agents 1.8.6), one
rehearsal date per session, with its tests and rehearsal tools. The app sends nothing yet:
it has no analytics, no error reporting and no network calls. Recording (Egress),
Supabase, PostHog, Sentry, the ID vendor and the moderator console aren't built, so they
aren't covered. Each needs this audit extended before it ships.

`uv run python -m nova.data_flows` prints the flows below for the settings in
`services/nova/.env`, from the same code that sends them (`nova/data_flows.py`).

## How each fact is known

- **Tested:** a test in `services/nova/tests` proves it on every run.
- **Inspected:** read in this repository's code or in the installed library's source, at
  the version pinned in `uv.lock`; no test holds it.
- **UNKNOWN:** nothing in this repository establishes it. That covers every provider's
  retention and training terms: no contract, DPA or provider terms are recorded here.

## Data-flow table

Retention and training are UNKNOWN for every recipient outside Nova's process.

| Source | Data | Processor / provider | Purpose | Retention | Logging | Configuration | User disclosure |
|---|---|---|---|---|---|---|---|
| A dater's microphone | Raw audio, live | LiveKit Cloud (the room's media server) | Carry the date to Nova and the room | UNKNOWN. Nova sets no recording (`record=False`, tested) and starts no Egress | None by Nova | `LIVEKIT_URL` | Draft notice |
| A dater's microphone | Raw audio, every frame from the session's start to its end, silences included, **whether or not Nova is muted** (inspected: LiveKit's `_push_audio`; the mute never touches audio input, tested). Metadata: room, job, agent and session IDs as request headers | LiveKit Inference, then Deepgram (`nova-3`) | Speech to text | UNKNOWN | Final transcripts appear in LiveKit's DEBUG logs only under `lk.pii.*` keys, which `RedactPii` blanks (tested in Docker) | `NOVA_STT` | Draft notice |
| A dater's microphone | Raw audio | Voice activity detector, in the agent process | When someone starts and stops | Memory only | None | Turn detection `vad`; LiveKit's barge-in service off (tested) | Not needed |
| Speech recognition | Transcripts | Nova's process memory: each dater's last 15 s (checks), this date's last 8 turns and 3 Nova lines (`DateContext`) | Replies, safety checks, this date's context | Memory only; cleared when the date ends (tested) | Codes only: `EventLog` rejects text (tested); not published to the room (`text_output=False`, tested) | Fixed | Draft notice |
| Speech recognition | **Conversation-derived safety context**: each finished utterance, with both daters' words from the 15 s before it, the whole date, Nova muted or not | The classifier model, wherever it is | Distress and conduct check (frozen, decision 0005) | Local Ollama: on that machine; its own server log isn't set by this repository. A provider: UNKNOWN | Codes only | `NOVA_CLASSIFIER_LLM`, else `NOVA_LLM`; `NOVA_DIALOGUE_TO_PROVIDER` doesn't apply | Draft notice, [IF ANTHROPIC RUNS THE CHECKS] |
| Cards, audience, speech recognition | First names and pronouns, approved audience questions, a dater's words to Nova; card answers in set pieces | The line model | Write Nova's lines | As above | Codes only | `NOVA_LLM` | Draft notice, if a provider |
| `DateContext` | **Dialogue memory**: this date's recent turns (at most 1,000 characters in a prompt) | The line model; the classifier, for live meaning checks | Replies and silences that follow the date | As above | Codes only (a `context` flag) | Only to an approved-local endpoint, or with `NOVA_DIALOGUE_TO_PROVIDER=true`, decided per recipient at each request (tested) | Draft notice, opt-in clause |
| Tonight's memory | Nova's earlier lines tonight, names masked; an earlier dater's card details can remain | The line model | Not repeating itself | As above | Codes only | Fixed | Draft notice |
| Nova's candidate line | The line, the cards with ages and answers, the segment, what it answers | The classifier model | Meaning check | As above | Codes only | `NOVA_MEANING_CHECK` (on), `NOVA_MEANING_LIVE` (off) | Draft notice, if a provider |
| Nova's line | Text that can hold first names, card details, an audience question or what a dater said; the same request headers | LiveKit Inference, then Cartesia (`sonic-3`, with word timestamps) | Nova's voice | UNKNOWN | Text only under `lk.pii.text` (redacted) | `NOVA_VOICE` | Draft notice |
| The voice | Nova's audio | LiveKit Cloud | Nova on air | UNKNOWN | None | `LIVEKIT_URL` | Draft notice |
| The room | Participant identities, the `role` attribute, the moderator's `MuteCommand` and Nova's `NovaState` (codes, seq, session) | LiveKit Cloud (data channel) | The moderator's mute | UNKNOWN | Codes only | Topic `unseen.moderator` | Not needed for daters |
| Rehearsals only | Text typed in the Agents Playground (commands, `ask <question>`) | LiveKit Cloud, then Nova | Run a rehearsal | UNKNOWN | Not logged (tested) | Playground | Developers only |
| Nova | Rehearsal report: codes, timings, model and voice names, who receives this date's turns | `services/nova/rehearsals/*.txt` (gitignored), the log, Docker's container log until the container is removed | Measure a rehearsal | Until deleted by hand | It is the log | None | Not needed |
| Test systems | Made-up sentences and daters (the evaluation, the benchmark, safety sets); synthetic speech clips (yield and mute rehearsals); fixed voice-sample lines | `NOVA_LLM`'s model; LiveKit Inference (Deepgram Aura, Inworld, Cartesia) | Measure Nova | Gitignored reports and WAVs; held-out sentences never in a report (tested) | Codes, numbers; development-set sentences in evaluation reports | On demand, never in CI | No real person's data |
| CI | Nothing real, no secrets | GitHub Actions | Tests | n/a | Test output | `.github/workflows/ci.yml` | n/a |
| The operator | `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `ANTHROPIC_API_KEY` | `services/nova/.env`: gitignored, excluded from the Docker image, passed in at run time | Authenticate | On the operator's machine | The secret and the Anthropic key are hidden from `Settings`'s repr; `Settings` is never logged | `.env` | n/a |

## What each provider receives

| | LiveKit Cloud | LiveKit Inference, then Deepgram | LiveKit Inference, then Cartesia | Anthropic, if configured | Ollama |
|---|---|---|---|---|---|
| Raw audio | Daters' and Nova's | All of each dater's, Nova muted or not | No | No | No |
| Conversation-derived safety context | No | No | No | Whenever it runs the checks | Whenever it runs the checks |
| Transcripts (a dater's words to Nova) | No | Produces them | Inside Nova's lines | Yes, if it writes the lines, or runs live meaning checks | The same |
| Dialogue memory (this date's recent turns) | No | No | No | Only with `NOVA_DIALOGUE_TO_PROVIDER=true` | Only at an approved-local endpoint, or with the opt-in |
| Nova's text | No | No | Every line | Candidate lines, tonight's memory | The same |
| Card and profile | Identities only | No | Inside Nova's lines | First names, ages, pronouns, card answers | The same |
| Metadata | Identities, roles, room names, timings | Room, job, agent and session IDs | The same | SDK headers (OS, Python and SDK versions) and the API key | The same SDK headers |

## Safety context and dialogue memory

Two kinds of data come from what daters say to each other. They have different rules,
and the inventory (`nova/data_flows.py`) and the notice keep them apart.

| | Conversation-derived safety context | Dialogue memory |
|---|---|---|
| What | Each utterance a dater finishes, with both daters' words from the 15 seconds before it (`nova/distress.py`, `nova/on_air.py`; their variable names say "earlier") | This date's recent turns: up to 8 the checks cleared, 3 of Nova's lines, open questions (`nova/date_context.py`) |
| For | The distress and conduct check | Replies and silences that follow the date; live meaning checks |
| Who receives it | The classifier: `NOVA_CLASSIFIER_LLM`, or `NOVA_LLM` when that's unset | The line model, and the classifier for live meaning checks |
| When | Every utterance, the whole date, Nova muted or not | Only where the policy allows it, per recipient, at each request |
| Local Ollama (loopback or approved host) | Receives it | Receives it |
| Anthropic, or Ollama elsewhere | Receives it, whatever `NOVA_DIALOGUE_TO_PROVIDER` says | Only with `NOVA_DIALOGUE_TO_PROVIDER=true` |
| Can it be held back? | Not without changing the frozen check (decision 0005) | Yes: left out of the prompt |
| Approval | A hosted classifier needs production approval (below) | A provider needs the opt-in, and the notice's clause |
| Tested | `tests/test_privacy.py`: the safety requests go where the inventory says, and are identical whichever classifier gets them; `tests/test_data_flows.py`: the inventory and the notice, local and hosted classifiers | `tests/test_privacy.py`: every configuration, on the real adapters' requests |

**Production approval: hosted safety classification.** A classifier at a provider receives
the conversation-derived safety context: everything each dater says, with the other's words
around it, for the whole date. The code allows it, as it always has. No real dater may meet
such a configuration until all four of these are done:
- the provider's terms are reviewed: retention, region, training, and any human review of
  flagged content;
- the product owner and legal approve it;
- the notice keeps its **[IF ANTHROPIC RUNS THE CHECKS]** paragraph;
- `uv run python -m nova.data_flows` agrees with the notice for the settings used.

Until then, run the checks on a local or approved-local classifier.

## Who may receive this date's turns (decision 0010)

One policy, `nova/privacy.py`, decides it per recipient. The line writer asks it before
adding this date to a prompt, separately for the line model and the meaning check's model.
Every request the lines, the meaning check and the safety check send then goes through
`Guarded`, which inspects the outgoing prompt. A request that carries this date to a
recipient that may not have it is never sent; the caller treats it as a failed request
(a fallback line, a line not kept, a moderator alert).

**Approved-local endpoints.** A model's endpoint is local only if:
- its host is a loopback address (`localhost`, `127.0.0.0/8`, `::1`); or
- the operator lists the host in `NOVA_APPROVED_LOCAL_HOSTS`, because they run it and
  nobody else receives what it's sent.

Nothing else is local:
- not `host.docker.internal`, which Docker resolves to whichever machine runs it;
- not a model just because its provider is Ollama;
- not a model whose endpoint isn't known;
- never Anthropic.

To run Nova in Docker against Ollama on the same PC with this date in its prompts, add
`NOVA_APPROVED_LOCAL_HOSTS=host.docker.internal` to `services/nova/.env`.

| Configuration | Before (2026-10-23) | After |
|---|---|---|
| Ollama on localhost, both roles | Both | Both |
| Local line model, Anthropic classifier, live meaning check on | Both, **including Anthropic** (gap 1) | The line model only |
| Anthropic line model, local classifier | Neither | Only the local classifier's meaning check |
| Anthropic, both roles | Neither | Neither |
| Anthropic with `NOVA_DIALOGUE_TO_PROVIDER=true` | Both | Both |
| Ollama on another host | Both, **as if local** (gap 2) | Neither, unless the host is approved or the opt-in is on |
| Ollama at `host.docker.internal` (Docker) | Both | Neither, unless approved |
| The evaluation, the benchmark | Always (made-up daters) | Always (`PrivacyPolicy.for_synthetic_data`, used nowhere else: tested) |

Every row is tested on the requests the real Ollama and Anthropic adapters build
(`tests/test_privacy.py`). The safety check still reads each utterance with both daters'
15 seconds before it, wherever the classifier is. Holding that back would change the
frozen check, so it isn't (see open decisions).

## Logs

- Nova's own events carry codes, numbers and flags only. `nova/events.py` rejects
  anything else (tested).
- LiveKit marks the fields of its log lines that hold what people said with `lk.pii.*`,
  and `RedactPii` blanks them (tested in Docker). LiveKit's other log lines were inspected
  at 1.8.6; a LiveKit upgrade needs them inspected again.
- The OpenAI and Anthropic SDKs log each request's full prompt at DEBUG
  (`*._base_client`, "Request options"):
  - `ANTHROPIC_LOG=debug` or `OPENAI_LOG=debug` is refused at start-up (gap 3, tested).
  - `NOVA_SYNTHETIC_DIAGNOSTICS=true` allows it, for the evaluation or the benchmark on
    made-up daters.
  - The agent refuses that flag. In each job, it also refuses to start if either logger
    would write at DEBUG, however that happened (tested natively; the agent's wiring in
    Docker).
- A whole rehearsal with a model that answers, raises, refuses, drops the connection or
  never answers, every error carrying the whole request, leaves none of the planted words
  in any log record, the report, the event log or the console. The planted words were in
  cards, first names, speech, an audience question and chat, and every one of them did
  reach the model (tested).
- Retried requests (the warm-up's) log nothing they carried (tested).

## What the repository's tests can't verify

- What the SDKs, LiveKit and the providers do with a request after Nova builds it. The
  tests capture each request where the adapter hands it to the SDK client.
- What LiveKit Cloud, LiveKit Inference, Deepgram, Cartesia and Anthropic keep, for how
  long, where, and whether they train on it.
- Ollama's own server log on its machine. With `OLLAMA_DEBUG` it may log prompts; nothing
  here sets it.
- The compiled voice-activity module. Its Python layer makes no network call.
- LiveKit's log lines beyond the `lk.pii.*` fields, beyond inspection at 1.8.6.

## Open decisions (launch-blocking)

1. Who is responsible for daters' data, and how daters contact them.
2. Each provider's retention, region and training terms, from contracts or their terms:
   LiveKit (Cloud and Inference), Deepgram, Cartesia, and Anthropic if it's used.
3. **Hosted safety classification**: a production approval decision (above). A provider
   that runs the checks receives the conversation-derived safety context for the whole
   date. It needs provider review, approval and the notice's paragraph before any real
   dater meets it. Choosing it in the settings is today the only thing that turns it on,
   and the frozen check can't run without that context.
4. Whether `NOVA_DIALOGUE_TO_PROVIDER` may ever be turned on, and with which wording.
5. Where Nova's models run for real shows, and so whether any host should be approved.
6. Whether daters' audio should stop reaching speech recognition while Nova is muted.
   Today it doesn't stop: the checks keep running, by design.
7. Where the app shows the notice. The app has no screens for it yet.
8. Legal sign-off of the notice (terms, privacy policy, on-air release: technical plan).
