import { Tabs } from 'expo-router';
import { useTheme } from '@/theme';

// Placeholder tab bar: labels only. The design system's TabBar (with Phosphor icons and
// the mini-player slot) replaces it in its own task. Backgrounds and the top border come
// from the navigation theme. The tints stay here because the TabBar spec asks for
// pearl and mist, and the navigation theme has no inactive color.
export default function TabsLayout() {
  const { colors } = useTheme();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.pearl,
        tabBarInactiveTintColor: colors.mist,
        tabBarIconStyle: { display: 'none' },
      }}
    >
      <Tabs.Screen name="tonight" options={{ title: 'Tonight' }} />
      <Tabs.Screen name="dates" options={{ title: 'Dates' }} />
      <Tabs.Screen name="you" options={{ title: 'You' }} />
    </Tabs>
  );
}
