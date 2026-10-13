import { render, screen } from '@testing-library/react-native';
import { Text } from 'react-native';
import { ThemeProvider, useTheme } from './index';
import { colors } from './tokens';

function ThemeProbe() {
  const theme = useTheme();
  return <Text>{`${theme.name} ${theme.colors.velvet}`}</Text>;
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
