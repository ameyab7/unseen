import { Stack } from 'expo-router';

// Screens inside the Dates tab (a match's chat, for example) push onto this stack
// instead of becoming tabs of their own.
export default function DatesLayout() {
  return <Stack screenOptions={{ headerShown: false }} />;
}
