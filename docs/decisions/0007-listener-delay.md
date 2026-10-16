# 0007: Listener delay

Date: 2026-10-16. Status: proposed, as a launch requirement. Nothing is built; this records
the decision to make and the spike that would settle it.

## Context

The technical plan leaves it open (Risks and open questions: "Listener delay: launch
without it, or fund the relay spike now?"), and lists the delay as a later spike under
moderation misses. Today a dater saying an address or a slur reaches the whole city at
once. The distress check (decision 0005), the conduct check (decision 0006) and the
moderator all act after the words are out: the street line, a pause, an alert. With a
short delay on what listeners hear, they could act before listeners hear it.

## Proposal

- **A relay agent** (the technical plan's design): a LiveKit agent subscribes to the
  daters' audio tracks (and Nova's), buffers them for N seconds, and republishes them for
  listeners. Daters, Nova and the moderator hear each other live; only listeners are
  delayed.
  - Reading a remote track frame by frame is `rtc.AudioStream`, which resamples
    participant tracks for us; publishing is an `rtc.AudioSource` wrapped in an
    `rtc.LocalAudioTrack` and `local_participant.publish_track`
    ([processing raw tracks](https://docs.livekit.io/transport/media/raw-tracks/)). Input
    and output sample rates must match; WebRTC commonly uses 20 ms chunks, so the buffer is
    a queue of 20 ms frames, N seconds deep.
  - Listeners must never subscribe to the live tracks. Two ways: one room, with the
    daters' tracks restricted to Nova, the moderator and the relay by track subscription
    permissions ([setTrackSubscriptionPermissions](https://docs.livekit.io/reference/client-sdk-js/interfaces/ParticipantTrackPermission.html)),
    a client-side control a compromised app could undo; or two rooms, a live room (daters,
    Nova, moderator, relay) and a broadcast room the relay publishes into, where listener
    tokens can only subscribe ([tokens and grants](https://docs.livekit.io/frontends/reference/tokens-grants/):
    `canSubscribe`, `canPublish: false`, `hidden`). Two rooms is the one we'd choose: no
    listener token can reach a live track at all.
- **Everything listeners get is delayed by the same N**: Nova's voice and the show's sounds
  (republished with the daters, so they stay in step), captions, the poll, gestures and
  the room's verdict. The show runner's cues for the room carry a show time, and the
  broadcast side releases them N seconds after it. The daters' own clock doesn't change:
  their decision window and the timing of the result are as now (decision 0004); listeners
  hear the result N seconds after the daters do.
- **A dump control.** The moderator can replace the last N seconds listeners haven't heard
  yet with silence or the fallback segment, before they hear it. The instant layers can
  do the same automatically for what they're sure of (decision 0006: a street or a phone
  number), and Nova's street line then plays in place of the detail.
- **N**: 5, 8 or 10 seconds.

  | N | Gives the checks and the moderator | Costs the room |
  | --- | --- | --- |
  | 5 s | Time for the instant layers and most model checks (median 377 ms, worst 424 ms on Gemma; decision 0005); little time for a person | Gestures and votes land 5 s after the moment |
  | 8 s | The checks, and a moderator who is already listening | Gestures, questions and votes 8 s late; the daters hear reactions to what they said 8 s ago |
  | 10 s | A moderator a few seconds to decide | The room feels behind; a lightning unlock or a question may come after its moment |

  The recommendation is 8 s, to be confirmed by the spike.

## Cost

One relay per show, as one more participant: it subscribes to three audio tracks and
publishes three, so roughly three more participant connections' worth of LiveKit minutes
for the length of the show, plus the compute for an agent process with a few seconds of
audio buffered (a few megabytes). Listener minutes don't change. Measured in the spike.

## Risks

- Drift: buffered tracks fall out of step with each other or with the delayed data cues.
- The daters hear the room late: reactions to a joke arrive N seconds after it.
- A relay that crashes takes the broadcast down: listeners need the fallback segment, and
  the relay restarts with an empty buffer.
- A dump that's late lets the detail through; one that's early cuts good audio.
- Two rooms double the room-management work in the app and the console.

## Spike plan

Build a relay for one rehearsal in the Agents Playground (decision 0003's way of working),
two rooms, N = 8 s, on LiveKit credit.

Pass criteria:

1. Listener audio is N seconds behind live, within ±150 ms, over a 10-minute date, with
   no drift between the daters' and Nova's tracks greater than 40 ms (one frame either
   way).
2. A dump replaces the not-yet-heard audio with silence within 200 ms of the moderator's
   command, and the cut is clean (no click longer than one frame).
3. A delayed data cue (the verdict) and its audio arrive within 100 ms of each other.
4. The relay recovers from a restart within 3 s, the fallback segment covering the gap.
5. Cost per show-hour measured and written into `docs/spike-results.md`.

## Open questions

- Is the delay a launch requirement, or the first change after launch?
- Does the moderator listen live (to act in time) or delayed (to hear what the room hears)?
  Probably live, with the room's audio a click away.
- Do gestures sent during the delay count toward the lightning unlock by when they were sent
  or when they arrive?
- Does a dumped stretch still feed captions and the clip recorder?
