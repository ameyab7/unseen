import { Stack } from 'expo-router';

// Screens inside the You tab (wallet, safety center) push onto this stack instead of
// becoming tabs of their own.
export default function YouLayout() {
  return <Stack screenOptions={{ headerShown: false }} />;
}
