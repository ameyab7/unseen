import { Stack } from 'expo-router';
import { ThemeProvider } from '@/theme';

// ThemeProvider also sets the status bar and the navigation theme, which colors every
// navigator's backgrounds and borders.
export default function RootLayout() {
  return (
    <ThemeProvider>
      <Stack screenOptions={{ headerShown: false }} />
    </ThemeProvider>
  );
}
