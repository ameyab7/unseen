// Proves the tokens-only rule in eslint.config.js catches off-system values.
// Runs under node --test, not Jest: ESLint loads its config with a dynamic import,
// which Jest's module sandbox does not support.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { ESLint } from 'eslint';

const eslint = new ESLint({ cwd: fileURLToPath(new URL('.', import.meta.url)) });

async function restrictedSyntax(code, filePath) {
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages.filter((m) => m.ruleId === 'no-restricted-syntax').map((m) => m.message);
}

describe('tokens-only lint rule', () => {
  const rejected = [
    ['a hex color', "export const a = { color: '#1e1527' };", /hex value/],
    ['an rgba color', "export const a = { color: 'rgba(0,0,0,0.5)' };", /rgb\(\) or hsl\(\)/],
    ['a raw font size', 'export const a = { fontSize: 17 };', /raw font size/],
    ['a raw line height', 'export const a = { lineHeight: 24 };', /raw font size/],
    ['a font name', "export const a = { fontFamily: 'Georgia' };", /naming a font/],
  ];

  for (const [name, code, message] of rejected) {
    test(`rejects ${name} in a screen`, async () => {
      const messages = await restrictedSyntax(code, 'app/example.tsx');
      assert.equal(messages.length, 1);
      assert.match(messages[0], message);
    });
  }

  test('applies to src/ui too', async () => {
    const messages = await restrictedSyntax("export const a = '#fff';", 'src/ui/Example.tsx');
    assert.equal(messages.length, 1);
  });

  test('allows tokens, and literal values inside src/theme', async () => {
    const usesTokens =
      "import { typography } from '@/theme';\nexport const a = { ...typography.body };";
    assert.deepEqual(await restrictedSyntax(usesTokens, 'app/example.tsx'), []);
    assert.deepEqual(await restrictedSyntax("export const a = '#1e1527';", 'src/theme/x.ts'), []);
  });
});
