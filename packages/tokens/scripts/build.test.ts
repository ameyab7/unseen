import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, test } from 'node:test';
import { pathToFileURL } from 'node:url';
import { buildTokensModule, camelCase, parseTokens, TokenError } from './build.ts';
import { SOURCE, TARGET } from './generate.ts';

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
      fontFamily: 'BodoniModa-MediumItalic',
      fontSize: 24,
      lineHeight: 30,
    });
    const displayXl = model.typography.find((t) => t.name === 'displayXl');
    assert.equal(displayXl?.fontFamily, 'BodoniModa-Medium');
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
      fontFamily: 'BodoniModa-Medium',
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
