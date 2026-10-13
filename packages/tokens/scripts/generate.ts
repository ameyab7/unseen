// Writes apps/mobile/src/theme/tokens.ts from tokens.json.
// With --check, writes nothing and exits 1 if the committed file is out of date.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildTokensModule, TokenError } from './build.ts';

export const SOURCE = fileURLToPath(new URL('../tokens.json', import.meta.url));
export const TARGET = fileURLToPath(
  new URL('../../../apps/mobile/src/theme/tokens.ts', import.meta.url),
);

function main(check: boolean): number {
  const shown = relative(process.cwd(), TARGET);
  let output: string;
  try {
    output = buildTokensModule(JSON.parse(readFileSync(SOURCE, 'utf8')));
  } catch (error) {
    if (!(error instanceof TokenError || error instanceof SyntaxError)) throw error;
    console.error(`tokens.json is invalid. ${error.message}`);
    return 1;
  }

  if (check) {
    const current = existsSync(TARGET) ? readFileSync(TARGET, 'utf8') : '';
    if (current !== output) {
      console.error(`${shown} is out of date. Run pnpm tokens to regenerate it.`);
      return 1;
    }
    console.log(`${shown} is up to date.`);
    return 0;
  }

  mkdirSync(dirname(TARGET), { recursive: true });
  writeFileSync(TARGET, output);
  console.log(`Wrote ${shown}.`);
  return 0;
}

if (import.meta.main) {
  process.exitCode = main(process.argv.includes('--check'));
}
