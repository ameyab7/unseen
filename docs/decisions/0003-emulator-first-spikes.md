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

## Consequences

- The emulator proves the pipeline (agent, room, listen-only token, playback), not
  real-device audio. Still unproven until week 5: Bluetooth switches, phone calls,
  backgrounding and lock-screen controls, iOS audio sessions and Android battery savers.
- To keep that late risk contained, all LiveKit and audio-session code lives behind one
  module (apps/mobile/src/lib/livekit), so device fixes land in one place.
- The week-5 gate needs a written device checklist (from the plan's risk table) before
  the Apple account is bought, so the test is ready the day the build installs.
- Real Android phones are not covered by this record and are still needed before launch.
