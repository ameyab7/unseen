import { Link } from 'expo-router';
import { StyleSheet, Text } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { size, space, typography, useTheme } from '@/theme';

export default function NotFoundScreen() {
  const { colors } = useTheme();
  return (
    <SafeAreaView style={[styles.screen, { backgroundColor: colors.velvet }]}>
      <Text accessibilityRole="header" style={[typography.title, { color: colors.pearl }]}>
        {"This page isn't here."}
      </Text>
      <Link
        href="/tonight"
        replace
        accessibilityRole="link"
        accessibilityLabel="Go to Tonight"
        style={[typography.body, styles.link, { color: colors.lamplightText }]}
      >
        Go to Tonight
      </Link>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, paddingHorizontal: space.screenMargin, paddingTop: space.xxl, gap: space.s },
  link: { minHeight: size.touchTarget, paddingVertical: space.s },
});
