import type { ExpoConfig } from 'expo/config';
import { colors } from './src/theme/tokens.ts';

// Placeholder until the domain is chosen. Used as both the iOS bundle identifier and
// the Android package; changing it after a store release creates a different app.
const APP_ID = 'com.YOURDOMAIN.unseen';

const config: ExpoConfig = {
  name: 'Unseen',
  slug: 'unseen',
  scheme: 'unseen',
  version: '1.0.0',
  orientation: 'portrait',
  icon: './assets/icon.png',
  // Dark-first: the show happens at night. Daylight is applied in-app, never by the system.
  userInterfaceStyle: 'dark',
  // Root view behind every screen (expo-system-ui), so nothing flashes white.
  backgroundColor: colors.evening.velvet,
  ios: {
    bundleIdentifier: APP_ID,
    supportsTablet: true,
  },
  android: {
    package: APP_ID,
    adaptiveIcon: {
      backgroundColor: colors.evening.velvet,
      foregroundImage: './assets/android-icon-foreground.png',
      backgroundImage: './assets/android-icon-background.png',
      monochromeImage: './assets/android-icon-monochrome.png',
    },
    predictiveBackGestureEnabled: false,
  },
  plugins: [
    'expo-router',
    [
      'expo-font',
      {
        // Embedded at build time. Each file is named after its PostScript name, which
        // is the fontFamily in src/theme/tokens.ts on both platforms.
        fonts: [
          './assets/fonts/BodoniModa-Medium.ttf',
          './assets/fonts/BodoniModa-MediumItalic.ttf',
        ],
      },
    ],
  ],
  experiments: {
    typedRoutes: true,
  },
};

export default config;
