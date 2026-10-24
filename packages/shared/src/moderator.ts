// The moderator console's control over Nova (decision 0005, launch requirement 2): the
// messages on one LiveKit data topic between the console and Nova's agent. The agent checks
// the same shapes in Python (services/nova/nova/moderator.py); both are tested against
// fixtures/moderator.json.
//
// - Only a participant whose token carries role=moderator is obeyed. Only the server that
//   mints tokens sets that attribute; a command from anyone else is ignored, with no reply.
// - The console sends a MuteCommand. `seq` must increase for each moderator identity (a
//   timestamp in milliseconds does, across console restarts); `session` is Nova's, from its
//   last NovaState. A command with an old seq or another session is stale and changes
//   nothing.
// - Nova answers every moderator command with a NovaState saying what is true, and sends one
//   to each moderator who joins and after every change. The console shows that, never what
//   it asked for, and shows the state as unknown until a NovaState arrives.
// - Anyone in the room can publish on the topic (the mute rehearsal of 2026-10-23 showed a
//   dater's message reaching the moderator), so the console trusts a NovaState only from
//   Nova's agent participant (kind agent), and sends its commands to that participant only
//   (destination identities), so daters never receive them. Nova sends NovaStates only to
//   moderators.
// - Muted, Nova stops mid-line, drops what it had queued and says nothing, safety lines
//   included, until a moderator unmutes it. Checks and alerts go on. The mute lasts the
//   session: it survives a moderator's connection dropping, and a restarted agent (a new
//   session) starts unmuted.

import { z } from 'zod';

export const MODERATOR_TOPIC = 'unseen.moderator';
export const ROLE_ATTRIBUTE = 'role';
export const MODERATOR_ROLE = 'moderator';

const session = z.string().min(1).max(64);

export const MuteCommand = z
  .object({
    type: z.enum(['mute', 'unmute']),
    seq: z.number().int().positive(),
    session,
  })
  .strict();
export type MuteCommand = z.infer<typeof MuteCommand>;

export const NOVA_STATE_RESULTS = ['state', 'applied', 'unchanged', 'stale', 'invalid'] as const;

export const NovaState = z
  .object({
    type: z.literal('nova_state'),
    session,
    muted: z.boolean(),
    // The command's seq this answers; 0 for a state Nova sent on its own.
    seq: z.number().int().nonnegative(),
    result: z.enum(NOVA_STATE_RESULTS),
  })
  .strict();
export type NovaState = z.infer<typeof NovaState>;
