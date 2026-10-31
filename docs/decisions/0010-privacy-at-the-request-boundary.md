# 0010: Privacy at Nova's request boundary

Date: 2026-10-24. Status: accepted. Legal review pending.

## Context

The privacy audit (`docs/privacy/nova-data-flows.md`) found three gaps:

1. **Recent turns could reach a hosted classifier.** One setting, computed once from
   `NOVA_LLM` (`Settings.shares_dialogue`), decided whether this date's recent turns could
   go to "the model". So with a local line model and an Anthropic classifier, the live
   meaning check sent them to Anthropic without `NOVA_DIALOGUE_TO_PROVIDER`.
2. **Any Ollama counted as local.** It did wherever `OLLAMA_BASE_URL` pointed, another
   machine included.
3. **SDK debug logs would write prompts.** `ANTHROPIC_LOG=debug` or `OPENAI_LOG=debug` makes
   the SDK log every prompt, daters' words included.

Decision 0005 also said "with local Ollama, nothing leaves the machine". That holds for
model calls only: audio and Nova's lines always go to LiveKit Inference.

## Decision

- **Conversation use** (2026-10-26, decision 0012): local floor and handover signals
  use transcripts within Nova, with no new model requests or recipients. Opt-in live
  prefixes use the existing cleared date context for generation and mandatory meaning
  checks; the existing policy must permit both recipients. The inventory includes
  prefix meaning checks even when `NOVA_MEANING_LIVE` is off. No cloud turn-detection
  or barge-in service is enabled. The inventory and draft notice describe this purpose.

- **One policy, asked at each request** (`nova/privacy.py`, `PrivacyPolicy`). It decides
  who may receive this date's recent turns by the recipient of each request, not by a
  setting computed once:
  - the line writer asks before adding them to a prompt, for the line model and the
    meaning check's model separately;
  - every request the lines, the meaning check and the safety check send goes through
    `Guarded`, which inspects the outgoing prompt;
  - a request carrying them to a recipient that may not have them is never sent, and
    fails like any failed request: a fallback line, a line not kept, an alert.
- **Approved-local endpoints.** Local means:
  - a loopback host (`localhost`, `127.0.0.0/8`, `::1`); or
  - a host the operator names in `NOVA_APPROVED_LOCAL_HOSTS`.

  Not `host.docker.internal`, not a provider's name, never Anthropic. This date's turns go
  to a local endpoint, or anywhere once `NOVA_DIALOGUE_TO_PROVIDER` is true.
- **Safety context is not dialogue memory** (clarified 2026-10-24).
  - The safety check's input is classified as **conversation-derived safety context**:
    each utterance, with both daters' words from the 15 seconds before it, whatever the
    variables are called (`earlier`, `other_earlier`).
  - It goes to the classifier wherever that is. `NOVA_DIALOGUE_TO_PROVIDER` governs only
    dialogue memory (this date's recent turns).
  - Holding the safety context back would change the frozen check (decision 0005), so
    nothing about it changed: not its prompts, window, thresholds, routing or run-time
    behavior.
  - The inventory names it (`Data.SAFETY_CONTEXT`, a label nothing asks about at run
    time), and the notice discloses it in its own paragraph, apart from the model that
    writes the lines.
- **Production approval: hosted safety classification.** A provider that runs the checks
  receives the safety context for the whole date. The code allows it, but no real dater
  may meet it until:
  - the provider's terms are reviewed (retention, region, training, human review);
  - the product owner and legal approve it;
  - the notice keeps its paragraph for it.

  Until then, the checks run on a local or approved-local classifier
  (`docs/privacy/nova-data-flows.md`).
- **The rest flows as before.** The configured models still receive what they need: cards,
  a dater's words to Nova, audience questions and Nova's own lines. Choosing a provider is
  what sends these, and the notice must name it.
- **Logs.**
  - `ANTHROPIC_LOG` or `OPENAI_LOG` at `debug` is refused at start-up, unless
    `NOVA_SYNTHETIC_DIAGNOSTICS=true`, which is for runs on made-up daters.
  - The agent refuses that flag. In each job, it also refuses any SDK request logger at
    DEBUG.
- **Synthetic runs say so.** The evaluation and the benchmark use
  `PrivacyPolicy.for_synthetic_data()`, so their measurements don't change; a test keeps
  it out of everything else.
- **An inventory** (`nova/data_flows.py`, `uv run python -m nova.data_flows`) lists every
  flow under the current settings, from the same builders and policy. Retention and
  training are UNKNOWN until a contract or terms establish them. A test holds the draft
  dater notice (`docs/privacy/dater-notice-draft.md`) to it.

## Consequences

- Native runs with Ollama on localhost behave as before.
- Docker runs reach Ollama at `host.docker.internal`, which isn't approved local. Nova's
  replies there lose this date's context until `.env` has
  `NOVA_APPROVED_LOCAL_HOSTS=host.docker.internal`.
- A new model request must go through a model the policy guarded; `Rehearsal` and
  `LineWriter` guard theirs. A new prompt key that holds this date must be added to
  `nova/privacy.py`'s pattern, or the boundary won't see it.
- The notice stays DRAFT / NOT SHIPPED. Shipping it needs:
  - the controller and contact;
  - each provider's retention and training terms;
  - the configuration chosen for real shows;
  - legal sign-off;
  - a screen in the app.
- What happens after a request leaves Nova can't be tested here: the SDKs, LiveKit, the
  providers, and Ollama's own logs.
