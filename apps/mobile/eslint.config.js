const { defineConfig, globalIgnores } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

// Colors and type sizes come only from src/theme (generated from tokens.json).
// Selectors are esquery; lint.test.mjs has a case for each.
const COLOR_KEY = '/^(color|[a-zA-Z]+Color)$/';
const TYPE_KEY = '/^(fontSize|lineHeight|letterSpacing)$/';
const FONT_KEY = '/^fontFamily$/';
// Strings the anywhere-rules below already report, so a color prop reports once.
const COLOR_STRING = '/^(#|rgba?\\(|hsla?\\()/';
// The same, plus 'transparent': the one color that may be written literally.
const SKIP = '/^(#|rgba?\\(|hsla?\\(|transparent$)/';

// Matches `color: …` and `'color': …` alike.
const key = (re) => `:matches([key.name=${re}], [key.value=${re}])`;

const colorMessage =
  "Use a color from useTheme().colors. Only 'transparent' may be written literally.";

const tokensOnly = [
  {
    selector: 'Literal[value=/^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/]',
    message: 'Use a color from useTheme().colors instead of a hex value.',
  },
  {
    selector: 'Literal[value=/^(rgb|hsl)a?\\(/]',
    message: 'Use a color from useTheme().colors instead of an rgb() or hsl() value.',
  },
  {
    // `#fff`, `rgba(0,0,0,${a})`, `${colors.pearl}80` and the like.
    selector: `TemplateElement[value.raw=${COLOR_STRING}]`,
    message: 'Use a color from useTheme().colors instead of building one in a template.',
  },
  {
    selector: `Property${key(COLOR_KEY)}[value.type="TemplateLiteral"][value.quasis.0.value.raw!=${COLOR_STRING}]`,
    message: 'Use a color from useTheme().colors instead of building one in a template.',
  },
  {
    selector: `Property${key(COLOR_KEY)}[value.type="Literal"][value.value!=${SKIP}]`,
    message: colorMessage,
  },
  {
    selector: `JSXAttribute[name.name=${COLOR_KEY}][value.type="Literal"][value.value!=${SKIP}]`,
    message: colorMessage,
  },
  {
    selector: `JSXAttribute[name.name=${COLOR_KEY}] > JSXExpressionContainer > Literal[value!=${SKIP}]`,
    message: colorMessage,
  },
  {
    selector: `Property${key(TYPE_KEY)}[value.type="Literal"]`,
    message: 'Use a typography style from @/theme instead of a raw font size.',
  },
  {
    selector: `Property${key(FONT_KEY)}[value.type=/^(Literal|TemplateLiteral)$/]`,
    message: 'Use a typography style from @/theme instead of naming a font.',
  },
];

module.exports = defineConfig([
  globalIgnores(['dist/*', '.expo/*', 'expo-env.d.ts', 'coverage/']),
  expoConfig,
  {
    files: ['app/**', 'src/**'],
    ignores: ['src/theme/**'],
    rules: { 'no-restricted-syntax': ['error', ...tokensOnly] },
  },
]);
