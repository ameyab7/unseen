import { DarkTheme, DefaultTheme, ThemeProvider as NavigationThemeProvider } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { colors, defaultTheme, type Palette, type ThemeName } from './tokens';

// Colors are only reachable through useTheme(), so a screen can switch to Daylight
// (the reveal) by wrapping itself in <ThemeProvider theme="daylight">. Each provider
// also sets the status bar and the navigation theme, so the nearest one wins.
export { radius, size, space, typography, themeNames } from './tokens';
export type { ColorName, Palette, ThemeName, TypographyName } from './tokens';

export interface Theme {
  name: ThemeName;
  colors: Palette;
}

// Whether each theme is dark. Typed as a full record so a new theme in tokens.json
// fails the typecheck until it is listed here.
const appearance: Record<ThemeName, 'dark' | 'light'> = {
  evening: 'dark',
  daylight: 'light',
};

const ThemeContext = createContext<Theme>({
  name: defaultTheme,
  colors: colors[defaultTheme],
});

function navigationTheme({ name, colors: c }: Theme) {
  const dark = appearance[name] === 'dark';
  return {
    ...(dark ? DarkTheme : DefaultTheme),
    dark,
    colors: {
      primary: c.lamplight,
      background: c.velvet,
      card: c.velvet,
      text: c.pearl,
      border: c.seam,
      notification: c.lamplight,
    },
  };
}

export function ThemeProvider({
  theme = defaultTheme,
  children,
}: {
  theme?: ThemeName;
  children: ReactNode;
}) {
  const value = useMemo(() => ({ name: theme, colors: colors[theme] }), [theme]);
  const navigation = useMemo(() => navigationTheme(value), [value]);
  return (
    <ThemeContext.Provider value={value}>
      <NavigationThemeProvider value={navigation}>
        {/* Light content on dark themes, dark content on light ones. */}
        <StatusBar style={appearance[theme] === 'dark' ? 'light' : 'dark'} />
        {children}
      </NavigationThemeProvider>
    </ThemeContext.Provider>
  );
}

export function useTheme(): Theme {
  return useContext(ThemeContext);
}
