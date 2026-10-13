# 0002: Generated, committed theme tokens

Date: 2026-10-13. Status: accepted.

## Decision

`packages/tokens/tokens.json` is the only source of colors, type, spacing and radii.
`pnpm tokens` runs `packages/tokens/scripts/generate.ts` (plain Node 24, which runs
TypeScript directly) and writes `apps/mobile/src/theme/tokens.ts`. The output is
committed, so Metro and EAS never run the generator. `pnpm tokens:check` and the
tokens test suite fail if the committed file is out of date.

## Shape of the output

- Names become camelCase. Spacing and radius drop their group prefix
  (`space-m` → `space.m`, `radius-l` → `radius.l`; `screen-margin` → `space.screenMargin`).
- Px values become plain numbers (points on iOS, dp on Android). `rgba()` stays a string.
- Colors are keyed by theme (`colors.evening`, `colors.daylight`). Screens read them
  through `useTheme()`, never directly, so the reveal can switch to Daylight with a
  nested `<ThemeProvider theme="daylight">`.
- Each token's usage note becomes a doc comment, so it shows on hover.

## Fonts

- Text styles carry no `fontFamily`: the platform font (SF Pro, Roboto) is what makes
  Dynamic Type and Android font scaling work. The CSS stacks in tokens.json are for
  the web console.
- The brand book asks for Bodoni Moda with optical sizing on. React Native cannot
  drive a variable font's `opsz` axis, so we bundle a static optical-size cut instead.
- Google Fonts ships static cuts at 9, 18, 28, 48 and 72pt. We use **28pt** Medium and
  Medium Italic (`BodoniModa28pt-Medium`, `BodoniModa28pt-MediumItalic`, SIL OFL)
  for every display style. With automatic optical sizing a browser sets opsz to the
  px size, so our styles would ask for 24, 32 and 44; 28pt is the nearest cut to that
  range and fits the two most common styles (display-name at 24, display-l at 32)
  closely. display-xl (44px, countdowns and the reveal headline) sits between 28 and
  48; if it looks too heavy in its hairlines, add the 48pt cut for that one style.
- Each file is named after its PostScript name, so one `fontFamily` string works on
  iOS and Android. A test reads the name table of each bundled file to prove it.
- Display styles carry no `fontWeight` or `fontStyle`, which would make Android
  synthesize bold or italic on top of the face.
- The generator refuses display type under 24px and display weights with no bundled
  file, so the brand rule fails at generation time, not in review.

## Enforcement

ESLint's `no-restricted-syntax` rejects hex, rgb() and hsl() literals and literal
`fontSize`, `lineHeight`, `letterSpacing` and `fontFamily` values in `app/` and `src/`,
outside `src/theme`.
