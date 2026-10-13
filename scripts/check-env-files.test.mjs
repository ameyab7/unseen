import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { describe, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { findProblems } from './check-env-files.mjs';

const repo = fileURLToPath(new URL('..', import.meta.url));

function problemsFor(files) {
  return findProblems(Object.keys(files), (path) => files[path]);
}

describe('findProblems', () => {
  test('passes a repo with only .env.example files', () => {
    assert.deepEqual(
      problemsFor({
        'services/nova/.env.example': '# LiveKit\nLIVEKIT_URL=\nLIVEKIT_API_SECRET=\n',
        'apps/mobile/.env.example': 'EXPO_PUBLIC_LIVEKIT_URL=\n',
        'README.md': 'anything',
      }),
      [],
    );
  });

  for (const path of ['.env', 'services/nova/.env', 'apps/mobile/.env.local', '.env.production']) {
    test(`rejects a committed ${path}`, () => {
      const [problem] = problemsFor({ [path]: 'SECRET=x\n' });
      assert.match(problem, new RegExp(`^${path.replace(/\./g, '\\.')} is committed`));
    });
  }

  test('rejects a value in .env.example', () => {
    const problems = problemsFor({
      'services/nova/.env.example': 'LIVEKIT_URL=\nLIVEKIT_API_KEY=APIabc123\n',
    });
    assert.deepEqual(problems, [
      'services/nova/.env.example line 2 sets a value. Example files list names only: LIVEKIT_API_KEY=',
    ]);
  });

  test('rejects a line that is not NAME=', () => {
    const [problem] = problemsFor({ 'apps/mobile/.env.example': 'export TOKEN\n' });
    assert.match(problem, /line 1 is not NAME= or a comment/);
  });

  test('allows comments and blank lines in .env.example', () => {
    assert.deepEqual(problemsFor({ '.env.example': '\n# a comment\n   \nNAME=\n' }), []);
  });
});

describe('.gitignore', () => {
  const ignored = (path) =>
    spawnSync('git', ['check-ignore', '-q', '--no-index', path], { cwd: repo }).status === 0;

  for (const path of ['.env', 'services/nova/.env', 'apps/mobile/.env', 'apps/mobile/.env.local']) {
    test(`ignores ${path}`, () => assert.equal(ignored(path), true));
  }

  for (const path of ['services/nova/.env.example', 'apps/mobile/.env.example']) {
    test(`does not ignore ${path}`, () => assert.equal(ignored(path), false));
  }
});
