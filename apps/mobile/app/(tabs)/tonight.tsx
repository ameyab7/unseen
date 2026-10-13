import { StyleSheet, Text } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { space, typography, useTheme } from '@/theme';

// Placeholder until the Tonight slice (lineup, seat ticket, mini-player) is built.
export default function TonightScreen() {
  const { colors } = useTheme();
  return (
    <SafeAreaView edges={['top']} style={[styles.screen, { backgroundColor: colors.velvet }]}>
      <Text accessibilityRole="header" style={[typography.displayL, { color: colors.pearl }]}>
        Tonight
      </Text>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, paddingHorizontal: space.screenMargin, paddingTop: space.s, gap: space.m },
});
