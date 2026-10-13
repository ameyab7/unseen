# 0003: Emulator-first audio spikes, real iPhone at the end of Listen

Date: 2026-10-13. Status: accepted.

## Context

The technical plan front-loads the riskiest work: a week-1 audio spike on at least six
physical phones and a Nova speech bake-off in weeks 1 and 2. Today there is no Apple
Developer account and no Android phone, only the Android emulator on a Windows PC.

## Decision

- **Nova:** build the show runner in services/nova and test it in LiveKit's Agents
  Playground in a browser. Nova runs one scripted date end to end. No phone needed.
- **App audio:** the Android emulator, running our development build, joins the same
  LiveKit room as a listener and hears Nova.
- **Real iPhone:** moves to the end of the Listen phase (about week 5), when the Apple
  Developer account is bought. Nothing after Listen starts until audio works on a real
  iPhone.
- **Language model:** during development Nova uses Gemma (`gemma4:26b`) on the
  developer's PC through Ollama, with thinking switched off. The model is chosen by one
  setting in `services/nova/.env`, so Claude Haiku or Opus can replace it without code
  changes. The launch model is still undecided: in production Nova runs in LiveKit's
  cloud and cannot reach a PC on a home network.
- **Pipeline, not speech-to-speech:** Nova runs speech-to-text, then the language model,
  then text-to-speech. A speech-to-speech model would answer faster, but it leaves no
  text to check, and Nova's guardrails (never claims to be human, no comments on looks or
  voices, no pushing anyone to reveal, no guessing a decision) need every line as text
  before it is spoken.

## Consequences

- The emulator proves the pipeline (agent, room, listen-only token, playback), not
  real-device audio. Still unproven until week 5: Bluetooth switches, phone calls,
  backgrounding and lock-screen controls, iOS audio sessions and Android battery savers.
- To keep that late risk contained, all LiveKit and audio-session code lives behind one
  module (apps/mobile/src/lib/livekit), so device fixes land in one place.
- The week-5 gate needs a written device checklist (from the plan's risk table) before
  the Apple account is bought, so the test is ready the day the build installs.
- Real Android phones are not covered by this record and are still needed before launch.
- Measured on the development PC (RTX 5070 Ti, 16 GB): Gemma 26B splits 26% processor,
  74% graphics card, yet once loaded gives its first token in 0.1 to 0.2 seconds at about
  85 tokens a second. Loading it takes about 28 seconds, so it is loaded before a date,
  never during one. With thinking left on, one line took 7.6 seconds instead of 0.4.
- Whatever the launch model is, it must pass the Nova evaluation script (show moments
  checked against the guardrails and the brand voice) before it goes on air.
