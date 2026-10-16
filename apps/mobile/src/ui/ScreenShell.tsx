import type { ReactNode } from 'react';
import { StyleSheet, Text } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { space, typography, useTheme } from '@/theme';

type Props = {
  title: string;
  children?: ReactNode;
};

// A tab's screen: the top safe area, the title as the screen's header, and its content.
// Not a design-system component; built from tokens for the placeholder tabs until their
// slices are designed.
export function ScreenShell({ title, children }: Props) {
  const { colors } = useTheme();
  return (
    <SafeAreaView edges={['top']} style={[styles.screen, { backgroundColor: colors.velvet }]}>
      <Text accessibilityRole="header" style={[typography.displayL, { color: colors.pearl }]}>
        {title}
      </Text>
      {children}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, paddingHorizontal: space.screenMargin, paddingTop: space.s, gap: space.m },
});
