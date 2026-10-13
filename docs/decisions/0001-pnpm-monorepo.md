# 0001: pnpm monorepo with a hoisted node_modules

Date: 2026-10-13. Status: accepted.

## Decision

One repository, pnpm workspaces (`apps/*`, `packages/*`), pnpm pinned through
`packageManager` and run with corepack. `nodeLinker: hoisted` in
`pnpm-workspace.yaml`. No Turborepo or Nx: root scripts run `pnpm -r`, which is
enough for two packages. services/nova is Python and stays outside the workspace.

## Why

- Expo has supported isolated installs since SDK 54, but hoisted is the layout
  React Native tooling (Metro, autolinking, Jest presets) has been tested against
  longest. We can try isolated later by deleting one line.
- Metro needs no custom config: Expo configures monorepos automatically since SDK 52.

## Things to know

- Install app packages from apps/mobile with
  `node ../../node_modules/expo/bin/cli install <pkg>`, not `npx expo install`, then
  check package.json put them in the right section. It is the same Expo CLI command,
  so versions still match the SDK, but it skips the `.bin` shim: a hoisted relink
  deletes that shim while it runs, which fails on Windows ("The batch file cannot be
  found"). It has also put dev tools under `dependencies`; move them if so.
- Node 24.2 or newer, enforced with `engineStrict`: the token generator relies on
  `import.meta.main`, and on older Node it would silently do nothing.
- pnpm auto-installs peer dependencies at their latest versions. expo-router and
  @expo/ui declare reanimated, worklets, gesture-handler and react-dom as `*` peers,
  which pulled in versions SDK 57 was not built against. All four are direct
  dependencies of apps/mobile so `expo install` pins them and `expo-doctor` checks them.
- ESLint is pinned to 9: eslint-plugin-react (inside eslint-config-expo) does not
  support ESLint 10 yet.
- pnpm 12 blocks dependency build scripts until approved. `unrs-resolver` is denied in
  `allowBuilds`: its script only downloads a native binary pnpm already installs.
- pnpm 12 holds back very new releases; it adds exact exceptions to
  `minimumReleaseAgeExclude` itself when Expo pins a fresh patch.
- `.gitattributes` forces LF so generated files compare byte for byte on every OS.
