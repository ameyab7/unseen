// Proves the lint setup in eslint.config.js and the Prettier check do what CLAUDE.md
// promises. Runs under node --test, not Jest: ESLint loads its config with a dynamic
// import, which Jest's module sandbox does not support.
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { describe, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { ESLint } from 'eslint';
import prettier from 'prettier';

const appDir = fileURLToPath(new URL('.', import.meta.url));
const eslint = new ESLint({ cwd: appDir });

async function restrictedSyntax(code, filePath = 'app/example.tsx') {
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages.filter((m) => m.ruleId === 'no-restricted-syntax').map((m) => m.message);
}

describe('tokens-only lint rule', () => {
  const rejected = [
    ['a hex color', "export const a = { color: '#1e1527' };", /hex value/],
    ['an rgba color', "export const a = { color: 'rgba(0,0,0,0.5)' };", /rgb\(\) or hsl\(\)/],
    ['a named color', "export const a = { color: 'white' };", /Only 'transparent'/],
    [
      'a named background color',
      "export const a = { backgroundColor: 'rebeccapurple' };",
      /Only 'transparent'/,
    ],
    [
      'a named color in a JSX prop',
      'import { ActivityIndicator } from \'react-native\';\nexport const A = () => <ActivityIndicator color="white" />;',
      /Only 'transparent'/,
    ],
    [
      'a named color in a JSX expression',
      "import { ActivityIndicator } from 'react-native';\nexport const A = () => <ActivityIndicator color={'white'} />;",
      /Only 'transparent'/,
    ],
    ['a hex color in a template literal', 'export const a = { color: `#fff` };', /template/],
    [
      'an rgba color in a template literal',
      'export const f = (a) => `rgba(0,0,0,${a})`;',
      /template/,
    ],
    [
      'a token with alpha appended in a template literal',
      "import { useTheme } from '@/theme';\nexport const f = () => ({ color: `${useTheme().colors.pearl}80` });",
      /template/,
    ],
    [
      'a named color under a quoted key',
      "export const a = { 'color': 'white' };",
      /Only 'transparent'/,
    ],
    ['a raw font size', 'export const a = { fontSize: 17 };', /raw font size/],
    ['a raw font size under a quoted key', "export const a = { 'fontSize': 17 };", /raw font size/],
    ['a raw line height', 'export const a = { lineHeight: 24 };', /raw font size/],
    ['a font name', "export const a = { fontFamily: 'Georgia' };", /naming a font/],
    [
      'a font name under a quoted key',
      "export const a = { 'fontFamily': 'Georgia' };",
      /naming a font/,
    ],
    [
      'a font name in a template literal',
      'export const a = { fontFamily: `Georgia` };',
      /naming a font/,
    ],
  ];

  for (const [name, code, message] of rejected) {
    test(`rejects ${name}`, async () => {
      const messages = await restrictedSyntax(code);
      assert.equal(messages.length, 1, `expected one error, got ${JSON.stringify(messages)}`);
      assert.match(messages[0], message);
    });
  }

  test('applies to src/ui too', async () => {
    const messages = await restrictedSyntax("export const a = '#fff';", 'src/ui/Example.tsx');
    assert.equal(messages.length, 1);
  });

  const allowed = [
    ['transparent', "export const a = { backgroundColor: 'transparent' };"],
    [
      'transparent in a JSX prop',
      "import { View } from 'react-native';\nexport const A = () => <View style={{ borderColor: 'transparent' }} />;",
    ],
    [
      'colors from the theme',
      "import { useTheme } from '@/theme';\nexport const f = () => ({ color: useTheme().colors.pearl });",
    ],
    [
      'typography from the theme',
      "import { typography } from '@/theme';\nexport const a = { ...typography.body };",
    ],
    ['copy that happens to be a color word', "export const label = 'Gold';"],
  ];

  for (const [name, code] of allowed) {
    test(`allows ${name}`, async () => {
      assert.deepEqual(await restrictedSyntax(code), []);
    });
  }

  test('allows literal values inside src/theme', async () => {
    assert.deepEqual(await restrictedSyntax("export const a = '#1e1527';", 'src/theme/x.ts'), []);
  });
});

describe('lint ignores', () => {
  const coverageFile = join(appDir, 'coverage', 'lcov-report', 'prettify.js');

  test('ESLint skips coverage output', async () => {
    assert.equal(await eslint.isPathIgnored(coverageFile), true);
  });

  // Same ignore files the Prettier CLI reads by default from apps/mobile.
  test('Prettier skips coverage output', async () => {
    const info = await prettier.getFileInfo(coverageFile, {
      ignorePath: [join(appDir, '.gitignore'), join(appDir, '.prettierignore')],
    });
    assert.equal(info.ignored, true);
  });
});
