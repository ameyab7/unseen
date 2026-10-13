const { defineConfig, globalIgnores } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

// Colors and type sizes come only from src/theme (generated from tokens.json).
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
    selector: 'Property[key.name=/^(fontSize|lineHeight|letterSpacing)$/] > Literal',
    message: 'Use a typography style from @/theme instead of a raw font size.',
  },
  {
    selector: 'Property[key.name="fontFamily"] > Literal',
    message: 'Use a typography style from @/theme instead of naming a font.',
  },
];

module.exports = defineConfig([
  globalIgnores(['dist/*', '.expo/*', 'expo-env.d.ts']),
  expoConfig,
  {
    files: ['app/**', 'src/**'],
    ignores: ['src/theme/**'],
    rules: { 'no-restricted-syntax': ['error', ...tokensOnly] },
  },
]);
