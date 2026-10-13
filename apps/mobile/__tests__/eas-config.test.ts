import eas from '../eas.json';
import root from '../../../package.json';

// EAS build machines install the tools named in eas.json before anything else, so a
// mismatch here fails the cloud build before our code is even looked at.
describe('eas.json', () => {
  const base = eas.build.base as Record<string, unknown>;

  it('installs the same pnpm version as packageManager', () => {
    expect(`pnpm@${base.pnpm}`).toBe(root.packageManager);
  });

  // With a pinned Node, corepack's pnpm shim makes EAS's own pnpm install fail
  // (EEXIST on the pnpm binary), so the pnpm version is set directly instead.
  it('does not enable corepack', () => {
    expect(base.corepack).toBeUndefined();
  });

  it('builds on a Node version the repo accepts', () => {
    const [major = 0, minor = 0] = String(base.node).split('.').map(Number);
    expect(major > 24 || (major === 24 && minor >= 2)).toBe(true);
  });

  it('builds every profile from the shared base', () => {
    for (const name of ['development', 'preview', 'production'] as const) {
      expect(eas.build[name].extends).toBe('base');
    }
  });
});
