import { ThemeProvider as NavigationThemeProvider, Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { ThemeProvider, useTheme } from '@/theme';
import { useNavigationTheme } from '@/theme/navigation';

function RootNavigator() {
  const { name, colors } = useTheme();
  const navigationTheme = useNavigationTheme();
  return (
    <NavigationThemeProvider value={navigationTheme}>
      <StatusBar style={name === 'evening' ? 'light' : 'dark'} />
      <Stack
        screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.velvet } }}
      />
    </NavigationThemeProvider>
  );
}

export default function RootLayout() {
  return (
    <ThemeProvider>
      <RootNavigator />
    </ThemeProvider>
  );
}
