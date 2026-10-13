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
- Display styles name a bundled Bodoni Moda face (`BodoniModa-Medium`,
  `BodoniModa-MediumItalic`, from the upstream static cuts, SIL OFL). Each file is
  named after its PostScript name, so one string works on iOS and Android. Display
  styles carry no `fontWeight` or `fontStyle`, which would make Android synthesize
  bold or italic on top of the face.
- The generator refuses display type under 24px and display weights with no bundled
  file, so the brand rule fails at generation time, not in review.
- React Native cannot drive the variable font's optical-size axis, so display type
  uses the static cuts at their default optical size. If the large sizes look too
  heavy, instance the variable font at about opsz 28 and swap the files.

## Enforcement

ESLint's `no-restricted-syntax` rejects hex, rgb() and hsl() literals and literal
`fontSize`, `lineHeight`, `letterSpacing` and `fontFamily` values in `app/` and `src/`,
outside `src/theme`.
