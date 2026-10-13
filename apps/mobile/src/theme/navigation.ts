import { DarkTheme } from 'expo-router';
import { useMemo } from 'react';
import { useTheme } from './index';

// Maps the active Unseen theme onto the navigator's theme, so headers, tab bars and
// screen backgrounds never fall back to the library's own colors.
export function useNavigationTheme() {
  const { name, colors } = useTheme();
  return useMemo(
    () => ({
      ...DarkTheme,
      dark: name === 'evening',
      colors: {
        primary: colors.lamplight,
        background: colors.velvet,
        card: colors.velvet,
        text: colors.pearl,
        border: colors.seam,
        notification: colors.lamplight,
      },
    }),
    [name, colors],
  );
}
