import { StyleSheet, Text } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { space, typography, useTheme } from '@/theme';

// Placeholder. The You tab was never designed; it will be built from design-system
// parts as standard settings screens (profile, wallet, membership, safety center).
export default function YouScreen() {
  const { colors } = useTheme();
  return (
    <SafeAreaView edges={['top']} style={[styles.screen, { backgroundColor: colors.velvet }]}>
      <Text accessibilityRole="header" style={[typography.displayL, { color: colors.pearl }]}>
        You
      </Text>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, paddingHorizontal: space.screenMargin, paddingTop: space.s, gap: space.m },
});
