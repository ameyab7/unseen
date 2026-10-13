import { Tabs } from 'expo-router';
import { useTheme } from '@/theme';

// Placeholder tab bar: labels only. The design system's TabBar (with Phosphor icons and
// the mini-player slot) replaces it in its own task.
export default function TabsLayout() {
  const { colors } = useTheme();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.pearl,
        tabBarInactiveTintColor: colors.mist,
        tabBarStyle: { backgroundColor: colors.velvet, borderTopColor: colors.seam },
        tabBarIconStyle: { display: 'none' },
        sceneStyle: { backgroundColor: colors.velvet },
      }}
    >
      <Tabs.Screen name="tonight" options={{ title: 'Tonight' }} />
      <Tabs.Screen name="dates/index" options={{ title: 'Dates' }} />
      <Tabs.Screen name="you/index" options={{ title: 'You' }} />
    </Tabs>
  );
}
