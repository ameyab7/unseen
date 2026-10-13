import config from '../app.config';
import { colors } from '../src/theme/tokens';

type PluginEntry = string | [string, Record<string, unknown>];

function pluginOptions(name: string): Record<string, unknown> | undefined {
  const entry = (config.plugins as PluginEntry[] | undefined)?.find((p) =>
    Array.isArray(p) ? p[0] === name : p === name,
  );
  return Array.isArray(entry) ? entry[1] : undefined;
}

// Launch must be dark from the first frame: no white flash, light status bar content.
describe('app config', () => {
  it('forces dark mode', () => {
    expect(config.userInterfaceStyle).toBe('dark');
    expect(config.ios?.userInterfaceStyle).toBeUndefined();
    expect(config.android?.userInterfaceStyle).toBeUndefined();
  });

  it('paints the root view and launch screen velvet', () => {
    expect(config.backgroundColor).toBe(colors.evening.velvet);
    expect(pluginOptions('expo-splash-screen')).toMatchObject({
      backgroundColor: colors.evening.velvet,
    });
  });

  it('has no dark splash variant, which would switch iOS back to Automatic', () => {
    expect(pluginOptions('expo-splash-screen')).not.toHaveProperty('dark');
  });

  it('starts with a light status bar', () => {
    expect(pluginOptions('expo-status-bar')).toEqual({ style: 'light' });
  });

  // EAS links builds to the Expo project @ameyab7/unseen through owner, slug and ID.
  it('is linked to the Expo project', () => {
    expect(config.owner).toBe('ameyab7');
    expect(config.slug).toBe('unseen');
    expect(config.extra?.eas?.projectId).toBe('0894f1c2-1f30-49bd-8a98-c0e7dff1a9ef');
  });

  // The app ID is permanent once a store build ships, so a missing or placeholder ID
  // must fail here, not after the first production build.
  it('has a real app ID, the same on both platforms', () => {
    const id = config.ios?.bundleIdentifier;
    expect(id).toMatch(/^[a-zA-Z][a-zA-Z0-9]*(\.[a-zA-Z][a-zA-Z0-9]*)+$/);
    expect(id).not.toMatch(/YOURDOMAIN|MYREALDOMAIN|YOURCHOICE/);
    expect(config.android?.package).toBe(id);
  });
});
