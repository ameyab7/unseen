import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, test } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { buildTokensModule, camelCase, DISPLAY_FACES, parseTokens, TokenError } from './build.ts';
import { SOURCE, TARGET } from './generate.ts';

const FONTS_DIR = fileURLToPath(new URL('../../../apps/mobile/assets/fonts/', import.meta.url));

// Reads the PostScript name (name ID 6, Windows platform) from a TrueType file.
function postScriptName(file: Buffer): string | undefined {
  const tables = file.readUInt16BE(4);
  for (let i = 0; i < tables; i++) {
    if (file.toString('ascii', 12 + i * 16, 16 + i * 16) !== 'name') continue;
    const name = file.readUInt32BE(20 + i * 16);
    const strings = name + file.readUInt16BE(name + 4);
    for (let r = 0; r < file.readUInt16BE(name + 2); r++) {
      const record = name + 6 + r * 12;
      if (file.readUInt16BE(record) !== 3 || file.readUInt16BE(record + 6) !== 6) continue;
      const start = strings + file.readUInt16BE(record + 10);
      return file
        .subarray(start, start + file.readUInt16BE(record + 8))
        .swap16()
        .toString('utf16le');
    }
  }
  return undefined;
}

const tokensJson: Record<string, any> = JSON.parse(readFileSync(SOURCE, 'utf8'));

function withChange(change: (json: Record<string, any>) => void): Record<string, any> {
  const copy = structuredClone(tokensJson);
  change(copy);
  return copy;
}

function style(json: Record<string, any>, name: string): Record<string, any> {
  for (const group of json.type.groups) {
    for (const s of group.styles) if (s.name === name) return s;
  }
  throw new Error(`no style ${name}`);
}

describe('display faces', () => {
  for (const face of Object.values(DISPLAY_FACES)) {
    test(`${face} is bundled under its PostScript name`, () => {
      const file = readFileSync(join(FONTS_DIR, `${face}.ttf`));
      assert.equal(postScriptName(file), face);
    });
  }
});

describe('parseTokens', () => {
  const model = parseTokens(tokensJson);

  test('turns kebab-case names into camelCase', () => {
    assert.equal(camelCase('plush-raised'), 'plushRaised');
    assert.equal(camelCase('lamplight-ink'), 'lamplightInk');
    assert.ok(model.colors.some((c) => c.name === 'onAir'));
  });

  test('keeps every theme for every color', () => {
    assert.deepEqual(model.themes, ['evening', 'daylight']);
    const plushRaised = model.colors.find((c) => c.name === 'plushRaised');
    assert.deepEqual(plushRaised?.values, { evening: '#362a45', daylight: '#ffffff' });
    const scrim = model.colors.find((c) => c.name === 'scrim');
    assert.equal(scrim?.values.evening, 'rgba(12,8,16,0.62)');
  });

  test('converts px to numbers and drops the group prefix from spacing and radius', () => {
    assert.deepEqual(
      model.space.map((t) => [t.name, t.value]),
      [
        ['xxs', 4],
        ['xs', 8],
        ['s', 12],
        ['m', 16],
        ['screenMargin', 20],
        ['l', 24],
        ['xl', 32],
        ['xxl', 48],
      ],
    );
    assert.deepEqual(
      model.radius.map((t) => [t.name, t.value]),
      [
        ['s', 10],
        ['m', 16],
        ['l', 24],
        ['pill', 999],
      ],
    );
    assert.deepEqual(
      model.size.map((t) => [t.name, t.value]),
      [
        ['touchTarget', 44],
        ['controlHeight', 52],
        ['avatarM', 56],
      ],
    );
  });

  test('maps display styles to a bundled Bodoni Moda face without weight or style', () => {
    const displayName = model.typography.find((t) => t.name === 'displayName');
    assert.deepEqual(displayName, {
      name: 'displayName',
      usage: style(tokensJson, 'display-name').usage,
      fontFamily: 'BodoniModa28pt-MediumItalic',
      fontSize: 24,
      lineHeight: 30,
    });
    const displayXl = model.typography.find((t) => t.name === 'displayXl');
    assert.equal(displayXl?.fontFamily, 'BodoniModa28pt-Medium');
  });

  test('leaves text styles on the platform font', () => {
    const body = model.typography.find((t) => t.name === 'body');
    assert.deepEqual(body, {
      name: 'body',
      usage: style(tokensJson, 'body').usage,
      fontSize: 17,
      lineHeight: 24,
      fontWeight: '400',
    });
  });

  const invalid: [string, (json: Record<string, any>) => void, RegExp][] = [
    [
      'a color missing a theme',
      (json) => delete json.color.tokens[0].value.daylight,
      /velvet has no daylight value/,
    ],
    [
      'a color for an unknown theme',
      (json) => (json.color.tokens[0].value.dusk = '#000000'),
      /unknown theme dusk/,
    ],
    [
      'a color that is not hex or rgba',
      (json) => (json.color.tokens[0].value.evening = 'purple'),
      /"purple" is not a hex or rgba\(\) color/,
    ],
    [
      'a size without px',
      (json) => (json.spacing.tokens[0].value = '4'),
      /spacing\.tokens\[0\]\.value: expected a px value/,
    ],
    [
      'a name that is not kebab-case',
      (json) => (json.radius.tokens[0].name = 'radiusSmall'),
      /"radiusSmall" is not kebab-case/,
    ],
    [
      'two names that collide after conversion',
      (json) => (json.spacing.tokens[1].name = 'xxs'),
      /two tokens are both named "xxs"/,
    ],
    [
      'display type under 24px',
      (json) => (style(json, 'display-name').fontSize = '20px'),
      /display type is only for 24px and up, got 20px/,
    ],
    [
      'a display weight with no bundled face',
      (json) => (style(json, 'display-l').fontWeight = 700),
      /no bundled Bodoni Moda face for 700 normal/,
    ],
    [
      'an italic text style',
      (json) => (style(json, 'body').fontStyle = 'italic'),
      /text styles are never italic/,
    ],
    [
      'an unknown font family',
      (json) => (json.type.groups[0].family = 'mono'),
      /unknown family "mono"/,
    ],
    [
      'a name that is not an identifier once its prefix is dropped',
      (json) => json.spacing.tokens.push({ name: 'space-2xl', value: '64px', usage: 'Huge.' }),
      /spacing\.tokens\[8\]\.name: "space-2xl" becomes "2xl", which is not a valid identifier/,
    ],
    [
      'a theme listed twice',
      (json) => json.color.themes.push({ id: 'evening', name: 'Evening again' }),
      /color\.themes\[2\]\.id: theme "evening" is listed twice/,
    ],
  ];

  for (const [name, change, message] of invalid) {
    test(`rejects ${name}`, () => {
      assert.throws(
        () => parseTokens(withChange(change)),
        (error: unknown) => error instanceof TokenError && message.test(error.message),
      );
    });
  }
});

describe('buildTokensModule', () => {
  const dir = mkdtempSync(join(tmpdir(), 'unseen-tokens-'));
  after(() => rmSync(dir, { recursive: true, force: true }));

  test('renders a module that imports with the same values', async () => {
    const file = join(dir, 'tokens.ts');
    writeFileSync(file, buildTokensModule(tokensJson));
    const mod = await import(pathToFileURL(file).href);

    assert.deepEqual(mod.themeNames, ['evening', 'daylight']);
    assert.equal(mod.defaultTheme, 'evening');
    assert.equal(mod.colors.evening.velvet, '#1e1527');
    assert.equal(mod.colors.daylight.lamplightText, '#9a4e1a');
    assert.equal(mod.space.screenMargin, 20);
    assert.equal(mod.radius.pill, 999);
    assert.equal(mod.size.touchTarget, 44);
    assert.deepEqual(mod.typography.displayL, {
      fontFamily: 'BodoniModa28pt-Medium',
      fontSize: 32,
      lineHeight: 38,
    });
    assert.deepEqual(mod.typography.footnote, { fontSize: 13, lineHeight: 18, fontWeight: '400' });
  });

  test('is deterministic and marks the file as generated', () => {
    const output = buildTokensModule(tokensJson);
    assert.equal(output, buildTokensModule(structuredClone(tokensJson)));
    assert.match(output, /^\/\/ Generated from packages\/tokens\/tokens\.json/);
    assert.match(output, /Do not edit/);
    assert.ok(!output.includes('\r'));
  });

  test('matches the committed apps/mobile/src/theme/tokens.ts', () => {
    assert.equal(
      readFileSync(TARGET, 'utf8'),
      buildTokensModule(tokensJson),
      'tokens.ts is out of date. Run pnpm tokens.',
    );
  });
});

describe('generate.ts', () => {
  // generate.ts only runs when import.meta.main is true, which Node added in 24.2.0.
  // On older Node it would silently do nothing and exit 0, so the repo must refuse it.
  test('the repo requires a Node version with import.meta.main', () => {
    const root = JSON.parse(
      readFileSync(new URL('../../../package.json', import.meta.url), 'utf8'),
    );
    const match = /^>=(\d+)\.(\d+)(?:\.(\d+))?$/.exec(root.engines?.node ?? '');
    assert.ok(match, `engines.node should look like ">=24.2", got ${root.engines?.node}`);
    const [major, minor] = [Number(match[1]), Number(match[2])];
    assert.ok(major > 24 || (major === 24 && minor >= 2), `engines.node is ${root.engines.node}`);
  });
});
