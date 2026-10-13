import { Platform, Text } from 'react-native';
import { renderRouter, screen } from 'expo-router/testing-library';

// React Navigation gives tab buttons the "button" role on iOS and "tab" elsewhere, and
// a VoiceOver label with the position, such as "Tonight, tab, 1 of 3".
const tabRole = Platform.OS === 'ios' ? 'button' : 'tab';
const tab = (label: string) => ({ name: new RegExp(`^${label}(,|$)`) });

// renderRouter returns RNTL's render promise with router helpers attached, so await it
// in place (returning it from an async function would unwrap the helpers away). The
// toHavePathname matcher ships without types in SDK 57, so tests read getPathname().
describe('routes', () => {
  it('opens on Tonight', async () => {
    const router = renderRouter('./app', { initialUrl: '/' });
    await router;

    expect(await screen.findByRole('header', { name: 'Tonight' })).toBeOnTheScreen();
    expect(router.getPathname()).toBe('/tonight');
  });

  it('shows the three tabs with Tonight selected', async () => {
    await renderRouter('./app', { initialUrl: '/' });

    const tonight = await screen.findByRole(tabRole, tab('Tonight'));
    expect(tonight).toBeSelected();
    expect(screen.getByRole(tabRole, tab('Dates'))).not.toBeSelected();
    expect(screen.getByRole(tabRole, tab('You'))).not.toBeSelected();
  });

  it('deep links to the Dates tab', async () => {
    const router = renderRouter('./app', { initialUrl: '/dates' });
    await router;

    expect(await screen.findByRole('header', { name: 'Dates' })).toBeOnTheScreen();
    expect(router.getPathname()).toBe('/dates');
  });

  it('shows a way back for an unknown link', async () => {
    await renderRouter('./app', { initialUrl: '/no-such-screen' });

    expect(await screen.findByRole('header', { name: "This page isn't here." })).toBeOnTheScreen();
    expect(screen.getByRole('link', { name: 'Go to Tonight' })).toBeOnTheScreen();
  });
  // A screen added inside dates/ (a chat, say) must open within the Dates tab, not
  // become a tab of its own.
  const withChat = {
    appDir: './app',
    overrides: { '(tabs)/dates/[matchId]': () => <Text>Chat</Text> },
  };

  it('keeps screens inside Dates out of the tab bar', async () => {
    await renderRouter(withChat, { initialUrl: '/dates' });

    await screen.findByRole('header', { name: 'Dates' });
    expect(screen.getAllByRole(tabRole)).toHaveLength(3);
  });

  it('opens a screen inside Dates with the Dates tab selected', async () => {
    const router = renderRouter(withChat, { initialUrl: '/dates/123' });
    await router;

    expect(await screen.findByText('Chat')).toBeOnTheScreen();
    expect(router.getPathname()).toBe('/dates/123');
    expect(screen.getByRole(tabRole, tab('Dates'))).toBeSelected();
  });
});
