// Turns tokens.json into the typed module the mobile app imports
// (apps/mobile/src/theme/tokens.ts). Pure functions only; generate.ts does the I/O.

export class TokenError extends Error {
  override name = 'TokenError';
}

export interface Token {
  name: string;
  usage: string;
}

export interface ColorToken extends Token {
  values: Record<string, string>;
}

export interface SizeToken extends Token {
  value: number;
}

export interface TypeStyle extends Token {
  // Undefined means the platform font (SF Pro, Roboto), so Dynamic Type applies.
  fontFamily?: string;
  fontSize: number;
  lineHeight: number;
  // Omitted for display faces: the font file already is the weight and style, and
  // setting both makes Android synthesize bold or italic on top.
  fontWeight?: string;
}

export interface TokenModel {
  themes: string[];
  colors: ColorToken[];
  typography: TypeStyle[];
  space: SizeToken[];
  radius: SizeToken[];
  size: SizeToken[];
}

// Bodoni Moda faces bundled in apps/mobile/assets/fonts. Each file is named after its
// PostScript name, which is the fontFamily React Native resolves on iOS and Android.
export const DISPLAY_FACES: Record<string, string> = {
  '500 normal': 'BodoniModa-Medium',
  '500 italic': 'BodoniModa-MediumItalic',
};

// Brand book: Bodoni Moda only at 24px and up.
export const DISPLAY_MIN_SIZE = 24;

const KEBAB = /^[a-z][a-z0-9]*(-[a-z0-9]+)*$/;
const HEX = /^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const RGBA = /^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,\s*(0|1|0?\.\d+)\s*)?\)$/;
const PX = /^(\d+(\.\d+)?)px$/;

export function camelCase(name: string): string {
  return name.replace(/-([a-z0-9])/g, (_, c: string) => c.toUpperCase());
}

function fail(path: string, message: string): never {
  throw new TokenError(`${path}: ${message}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (!isRecord(value)) fail(path, 'expected an object');
  return value;
}

function list(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) fail(path, 'expected an array');
  return value;
}

function text(value: unknown, path: string): string {
  if (typeof value !== 'string' || value.trim() === '') fail(path, 'expected a non-empty string');
  return value;
}

function px(value: unknown, path: string): number {
  const match = typeof value === 'string' ? PX.exec(value) : null;
  if (!match) fail(path, `expected a px value such as "16px", got ${JSON.stringify(value)}`);
  return Number(match[1]);
}

function tokenName(value: unknown, path: string, prefix = ''): string {
  const name = text(value, path);
  if (!KEBAB.test(name)) fail(path, `"${name}" is not kebab-case`);
  return camelCase(prefix && name.startsWith(prefix) ? name.slice(prefix.length) : name);
}

function unique<T extends Token>(tokens: T[], path: string): T[] {
  const seen = new Set<string>();
  for (const { name } of tokens) {
    if (seen.has(name)) fail(path, `two tokens are both named "${name}"`);
    seen.add(name);
  }
  return tokens;
}

function sizeGroup(json: Record<string, unknown>, key: string, prefix = ''): SizeToken[] {
  const tokens = list(record(json[key], key).tokens, `${key}.tokens`).map((raw, i) => {
    const path = `${key}.tokens[${i}]`;
    const token = record(raw, path);
    return {
      name: tokenName(token.name, `${path}.name`, prefix),
      value: px(token.value, `${path}.value`),
      usage: text(token.usage, `${path}.usage`),
    };
  });
  return unique(tokens, `${key}.tokens`);
}

function parseColors(json: Record<string, unknown>): Pick<TokenModel, 'themes' | 'colors'> {
  const color = record(json.color, 'color');
  const themes = list(color.themes, 'color.themes').map((raw, i) => {
    const id = text(record(raw, `color.themes[${i}]`).id, `color.themes[${i}].id`);
    if (!/^[a-z]+$/.test(id)) fail(`color.themes[${i}].id`, `"${id}" must be lowercase letters`);
    return id;
  });
  if (themes.length === 0) fail('color.themes', 'needs at least one theme');

  const colors = list(color.tokens, 'color.tokens').map((raw, i) => {
    const path = `color.tokens[${i}]`;
    const token = record(raw, path);
    const name = tokenName(token.name, `${path}.name`);
    const value = record(token.value, `${path}.value`);
    const extra = Object.keys(value).filter((theme) => !themes.includes(theme));
    if (extra.length > 0) fail(`${path}.value`, `unknown theme ${extra.join(', ')}`);
    const values: Record<string, string> = {};
    for (const theme of themes) {
      const v = value[theme];
      if (v === undefined) fail(`${path}.value`, `${name} has no ${theme} value`);
      if (typeof v !== 'string' || !(HEX.test(v) || RGBA.test(v))) {
        fail(`${path}.value.${theme}`, `${JSON.stringify(v)} is not a hex or rgba() color`);
      }
      values[theme] = v;
    }
    return { name, values, usage: text(token.usage, `${path}.usage`) };
  });
  return { themes, colors: unique(colors, 'color.tokens') };
}

function parseTypography(json: Record<string, unknown>): TypeStyle[] {
  const type = record(json.type, 'type');
  const families = record(type.families, 'type.families');
  const styles = list(type.groups, 'type.groups').flatMap((rawGroup, g) => {
    const group = record(rawGroup, `type.groups[${g}]`);
    const family = text(group.family, `type.groups[${g}].family`);
    if (!(family in families)) fail(`type.groups[${g}].family`, `unknown family "${family}"`);

    return list(group.styles, `type.groups[${g}].styles`).map((raw, s): TypeStyle => {
      const path = `type.groups[${g}].styles[${s}]`;
      const style = record(raw, path);
      const name = tokenName(style.name, `${path}.name`);
      const fontSize = px(style.fontSize, `${path}.fontSize`);
      const lineHeight = px(style.lineHeight, `${path}.lineHeight`);
      const usage = text(style.usage, `${path}.usage`);

      const weight = style.fontWeight;
      if (typeof weight !== 'number' || weight < 100 || weight > 900 || weight % 100 !== 0) {
        fail(`${path}.fontWeight`, 'expected 100, 200 … 900');
      }
      const fontStyle = style.fontStyle ?? 'normal';
      if (fontStyle !== 'normal' && fontStyle !== 'italic') {
        fail(`${path}.fontStyle`, 'expected "normal" or "italic"');
      }

      if (family === 'text') {
        if (fontStyle === 'italic') fail(path, 'text styles are never italic');
        return { name, usage, fontSize, lineHeight, fontWeight: String(weight) };
      }
      if (family === 'display') {
        if (fontSize < DISPLAY_MIN_SIZE) {
          fail(path, `display type is only for ${DISPLAY_MIN_SIZE}px and up, got ${fontSize}px`);
        }
        const fontFamily = DISPLAY_FACES[`${weight} ${fontStyle}`];
        if (!fontFamily) {
          fail(path, `no bundled Bodoni Moda face for ${weight} ${fontStyle}; add the file first`);
        }
        return { name, usage, fontFamily, fontSize, lineHeight };
      }
      return fail(`type.groups[${g}].family`, `no React Native mapping for family "${family}"`);
    });
  });
  return unique(styles, 'type.groups');
}

export function parseTokens(input: unknown): TokenModel {
  const json = record(input, 'tokens.json');
  return {
    ...parseColors(json),
    typography: parseTypography(json),
    space: sizeGroup(json, 'spacing', 'space-'),
    radius: sizeGroup(json, 'radius', 'radius-'),
    size: sizeGroup(json, 'size'),
  };
}

function quote(value: string): string {
  return `'${value.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
}

function doc(usage: string, indent: string): string {
  return `${indent}/** ${usage.replace(/\*\//g, '*\\/')} */\n`;
}

function sizeBlock(exportName: string, summary: string, tokens: SizeToken[]): string {
  const body = tokens.map((t) => `${doc(t.usage, '  ')}  ${t.name}: ${t.value},\n`).join('');
  return `/** ${summary} */\nexport const ${exportName} = {\n${body}} as const;\n`;
}

export function renderTokensModule(model: TokenModel): string {
  const [defaultTheme] = model.themes;
  const themeBlocks = model.themes
    .map((theme) => {
      const body = model.colors
        .map((c) => `${doc(c.usage, '    ')}    ${c.name}: ${quote(c.values[theme] ?? '')},\n`)
        .join('');
      return `  ${theme}: {\n${body}  },\n`;
    })
    .join('');

  const typeBody = model.typography
    .map((t) => {
      const fields = [
        t.fontFamily === undefined ? null : `fontFamily: ${quote(t.fontFamily)}`,
        `fontSize: ${t.fontSize}`,
        `lineHeight: ${t.lineHeight}`,
        t.fontWeight === undefined ? null : `fontWeight: ${quote(t.fontWeight)}`,
      ].filter((f) => f !== null);
      return `${doc(t.usage, '  ')}  ${t.name}: { ${fields.join(', ')} },\n`;
    })
    .join('');

  return [
    '// Generated from packages/tokens/tokens.json by packages/tokens/scripts/generate.ts.',
    '// Do not edit. Run `pnpm tokens` to regenerate.',
    '',
    `export const themeNames = [${model.themes.map(quote).join(', ')}] as const;`,
    'export type ThemeName = (typeof themeNames)[number];',
    `export const defaultTheme: ThemeName = ${quote(defaultTheme ?? '')};`,
    '',
    '/** Colors per theme. Read them through useTheme() so a screen can switch to Daylight. */',
    `export const colors = {\n${themeBlocks}} as const;`,
    '',
    `export type ColorName = keyof (typeof colors)[${quote(defaultTheme ?? '')}];`,
    'export type Palette = Record<ColorName, string>;',
    '',
    '/** Text styles. Display styles name a bundled Bodoni Moda face; the rest use the platform font. */',
    `export const typography = {\n${typeBody}} as const;`,
    '',
    'export type TypographyName = keyof typeof typography;',
    '',
    sizeBlock('space', 'Spacing on a 4px base.', model.space),
    sizeBlock('radius', 'Corner radii.', model.radius),
    sizeBlock('size', 'Fixed sizes for controls and avatars.', model.size).trimEnd(),
    '',
  ].join('\n');
}

export function buildTokensModule(input: unknown): string {
  return renderTokensModule(parseTokens(input));
}
