import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { colors, defaultTheme, type Palette, type ThemeName } from './tokens';

// Colors are only reachable through useTheme(), so a screen can switch to Daylight
// (the reveal) by wrapping itself in <ThemeProvider theme="daylight">.
export { radius, size, space, typography, themeNames } from './tokens';
export type { ColorName, Palette, ThemeName, TypographyName } from './tokens';

export interface Theme {
  name: ThemeName;
  colors: Palette;
}

const ThemeContext = createContext<Theme>({
  name: defaultTheme,
  colors: colors[defaultTheme],
});

export function ThemeProvider({
  theme = defaultTheme,
  children,
}: {
  theme?: ThemeName;
  children: ReactNode;
}) {
  const value = useMemo(() => ({ name: theme, colors: colors[theme] }), [theme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Theme {
  return useContext(ThemeContext);
}
