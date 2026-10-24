import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import { MuteCommand, NovaState } from './moderator.ts';

// The same fixtures the agent's Python checks run against (services/nova/tests).
const fixtures = JSON.parse(
  readFileSync(new URL('../fixtures/moderator.json', import.meta.url), 'utf8'),
) as Record<'command' | 'state', Record<'valid' | 'invalid', unknown[]>>;

describe('the console tells Nova to mute or unmute', () => {
  test('every valid command parses', () => {
    for (const message of fixtures.command.valid) {
      assert.ok(MuteCommand.safeParse(message).success, JSON.stringify(message));
    }
  });

  test('every invalid command is refused', () => {
    for (const message of fixtures.command.invalid) {
      assert.ok(!MuteCommand.safeParse(message).success, JSON.stringify(message));
    }
  });
});

describe('Nova says what is true', () => {
  test('every valid state parses', () => {
    for (const message of fixtures.state.valid) {
      assert.ok(NovaState.safeParse(message).success, JSON.stringify(message));
    }
  });

  test('every invalid state is refused', () => {
    for (const message of fixtures.state.invalid) {
      assert.ok(!NovaState.safeParse(message).success, JSON.stringify(message));
    }
  });
});
