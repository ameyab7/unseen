// Fails if any .env file is committed, or if a committed .env.example holds a value.
// Real keys live only in untracked .env files; examples list the names, nothing else.
// Usage: node scripts/check-env-files.mjs  (CI runs it on every pull request)

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const ENV_FILE = /^\.env(\..+)?$/;
const NAME_ONLY = /^[A-Z][A-Z0-9_]*=$/;

/**
 * @param {string[]} paths tracked file paths, forward slashes
 * @param {(path: string) => string} read returns a tracked file's contents
 * @returns {string[]} one message per problem, empty when the repo is clean
 */
export function findProblems(paths, read) {
  const problems = [];
  for (const path of paths) {
    const name = path.split('/').at(-1) ?? '';
    if (!ENV_FILE.test(name)) continue;

    if (name !== '.env.example') {
      problems.push(`${path} is committed. .env files hold real keys and must stay untracked.`);
      continue;
    }

    read(path)
      .split(/\r?\n/)
      .forEach((raw, i) => {
        const line = raw.trim();
        if (line === '' || line.startsWith('#') || NAME_ONLY.test(line)) return;
        const assignment = /^([A-Z][A-Z0-9_]*)=/.exec(line);
        problems.push(
          assignment
            ? `${path} line ${i + 1} sets a value. Example files list names only: ${assignment[1]}=`
            : `${path} line ${i + 1} is not NAME= or a comment.`,
        );
      });
  }
  return problems;
}

if (import.meta.main) {
  const paths = execFileSync('git', ['ls-files', '-z'], { encoding: 'utf8' })
    .split('\0')
    .filter(Boolean);
  const problems = findProblems(paths, (path) => readFileSync(path, 'utf8'));
  for (const problem of problems) console.error(problem);
  if (problems.length > 0) process.exitCode = 1;
  else console.log('No .env files committed; .env.example files list names only.');
}
