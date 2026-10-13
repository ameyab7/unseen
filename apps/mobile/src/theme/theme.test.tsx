import { render, screen } from '@testing-library/react-native';
import { useTheme as useNavigationTheme } from 'expo-router';
import { Text } from 'react-native';
import { ThemeProvider, useTheme } from './index';
import { colors } from './tokens';

// Renders each StatusBar as text so the test can read its style. Like React Native's
// StatusBar, the most recently mounted one wins.
jest.mock('expo-status-bar', () => {
  const React = jest.requireActual('react');
  const { Text: MockText } = jest.requireActual('react-native');
  return {
    StatusBar: ({ style }: { style: string }) =>
      React.createElement(MockText, { testID: 'status-bar' }, style),
  };
});

function ThemeProbe() {
  const theme = useTheme();
  return <Text>{`${theme.name} ${theme.colors.velvet}`}</Text>;
}

function NavigationProbe() {
  const { dark, colors: navigation } = useNavigationTheme();
  return <Text>{`navigation ${dark ? 'dark' : 'light'} ${String(navigation.background)}`}</Text>;
}

function activeStatusBarStyle() {
  return screen.getAllByTestId('status-bar').at(-1)?.props.children;
}

describe('useTheme', () => {
  it('uses Evening without a provider', async () => {
    await render(<ThemeProbe />);
    expect(screen.getByText(`evening ${colors.evening.velvet}`)).toBeOnTheScreen();
  });

  it('switches to Daylight inside a daylight provider', async () => {
    await render(
      <ThemeProvider>
        <ThemeProvider theme="daylight">
          <ThemeProbe />
        </ThemeProvider>
      </ThemeProvider>,
    );
    expect(screen.getByText(`daylight ${colors.daylight.velvet}`)).toBeOnTheScreen();
  });
});

describe('ThemeProvider', () => {
  it('gives Evening light status bar content and a dark navigation theme', async () => {
    await render(
      <ThemeProvider>
        <NavigationProbe />
      </ThemeProvider>,
    );
    expect(activeStatusBarStyle()).toBe('light');
    expect(screen.getByText(`navigation dark ${colors.evening.velvet}`)).toBeOnTheScreen();
  });

  it('gives a nested Daylight screen dark status bar content and its own navigation theme', async () => {
    await render(
      <ThemeProvider>
        <ThemeProvider theme="daylight">
          <NavigationProbe />
        </ThemeProvider>
      </ThemeProvider>,
    );
    expect(activeStatusBarStyle()).toBe('dark');
    expect(screen.getByText(`navigation light ${colors.daylight.velvet}`)).toBeOnTheScreen();
  });
});
