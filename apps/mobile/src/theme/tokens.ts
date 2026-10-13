// Generated from packages/tokens/tokens.json by packages/tokens/scripts/generate.ts.
// Do not edit. Run `pnpm tokens` to regenerate.

export const themeNames = ['evening', 'daylight'] as const;
export type ThemeName = (typeof themeNames)[number];
export const defaultTheme: ThemeName = 'evening';

/** Colors per theme. Read them through useTheme() so a screen can switch to Daylight. */
export const colors = {
  evening: {
    /** Page background. Evening is the default for every screen; Daylight is the reveal, the lights coming up. */
    velvet: '#1e1527',
    /** Cards: lineup card, blind card, the other person's chat bubbles. */
    plush: '#2a1f36',
    /** Sheets, menus and initial discs: anything that sits above a card. */
    plushRaised: '#362a45',
    /** Decorative dividers only (1.53:1 on plush). Never the only edge of a control. */
    seam: '#4a3b5a',
    /** Control borders: inputs, secondary buttons, chips. 4.48:1 on velvet and 3.97:1 on plush (Evening); 3.55:1 and 3.95:1 (Daylight). */
    seamStrong: '#8a7a9c',
    /** Primary text and icons. 15.29:1 on velvet, 13.52:1 on plush, 11.58:1 on plush-raised (Evening); 15.17:1 on velvet and 16.91:1 on plush (Daylight). */
    pearl: '#f4eee6',
    /** Secondary text: meta, timestamps, helper text. 9.21:1 on velvet, 8.14:1 on plush, 6.97:1 on plush-raised (Evening); 6.79:1 and 7.57:1 (Daylight). */
    mist: '#c3b7cd',
    /** The primary action fill (one per screen), progress, selected chips and the speaking ring. Text on it is always lamplight-ink. */
    lamplight: '#f2b27c',
    /** Text and icons on lamplight: 9.59:1 (Evening), 8.25:1 (Daylight). */
    lamplightInk: '#1e1527',
    /** Accent text and quiet links on velvet or plush: 9.59:1 and 8.48:1 (Evening); 5.42:1 and 6.04:1 (Daylight). */
    lamplightText: '#f2b27c',
    /** Focus ring, 2px with a 2px offset. At least 5.42:1 against velvet and plush in both themes. */
    focus: '#f2b27c',
    /** The live signal only: the on-air lamp and its label. 6.46:1 on velvet, 5.71:1 on plush, 4.89:1 on plush-raised (Evening); 4.72:1 and 5.26:1 (Daylight). */
    onAir: '#ff6f61',
    /** Nova's voice: host lines, the AI label and the callback note in a chat. Always beside the word Nova. 9.92:1 and 8.77:1 (Evening); 6.19:1 and 6.90:1 (Daylight). */
    nova: '#b7beff',
    /** Errors, Report, Block and destructive actions, always with an icon and words. 8.80:1 and 7.78:1 (Evening); 5.87:1 and 6.54:1 (Daylight). */
    danger: '#ff9d92',
    /** Confirmations such as a confirmed seat. 10.71:1 and 9.47:1 (Evening); 4.77:1 and 5.32:1 (Daylight). */
    success: '#8fd9b6',
    /** Behind sheets and dialogs. Dims the screen without hiding it; never carries text. */
    scrim: 'rgba(12,8,16,0.62)',
  },
  daylight: {
    /** Page background. Evening is the default for every screen; Daylight is the reveal, the lights coming up. */
    velvet: '#f7f1f4',
    /** Cards: lineup card, blind card, the other person's chat bubbles. */
    plush: '#ffffff',
    /** Sheets, menus and initial discs: anything that sits above a card. */
    plushRaised: '#ffffff',
    /** Decorative dividers only (1.53:1 on plush). Never the only edge of a control. */
    seam: '#e5dbe3',
    /** Control borders: inputs, secondary buttons, chips. 4.48:1 on velvet and 3.97:1 on plush (Evening); 3.55:1 and 3.95:1 (Daylight). */
    seamStrong: '#8c7a92',
    /** Primary text and icons. 15.29:1 on velvet, 13.52:1 on plush, 11.58:1 on plush-raised (Evening); 15.17:1 on velvet and 16.91:1 on plush (Daylight). */
    pearl: '#24182c',
    /** Secondary text: meta, timestamps, helper text. 9.21:1 on velvet, 8.14:1 on plush, 6.97:1 on plush-raised (Evening); 6.79:1 and 7.57:1 (Daylight). */
    mist: '#5d4f66',
    /** The primary action fill (one per screen), progress, selected chips and the speaking ring. Text on it is always lamplight-ink. */
    lamplight: '#efa567',
    /** Text and icons on lamplight: 9.59:1 (Evening), 8.25:1 (Daylight). */
    lamplightInk: '#24182c',
    /** Accent text and quiet links on velvet or plush: 9.59:1 and 8.48:1 (Evening); 5.42:1 and 6.04:1 (Daylight). */
    lamplightText: '#9a4e1a',
    /** Focus ring, 2px with a 2px offset. At least 5.42:1 against velvet and plush in both themes. */
    focus: '#9a4e1a',
    /** The live signal only: the on-air lamp and its label. 6.46:1 on velvet, 5.71:1 on plush, 4.89:1 on plush-raised (Evening); 4.72:1 and 5.26:1 (Daylight). */
    onAir: '#c7362b',
    /** Nova's voice: host lines, the AI label and the callback note in a chat. Always beside the word Nova. 9.92:1 and 8.77:1 (Evening); 6.19:1 and 6.90:1 (Daylight). */
    nova: '#4b4fb0',
    /** Errors, Report, Block and destructive actions, always with an icon and words. 8.80:1 and 7.78:1 (Evening); 5.87:1 and 6.54:1 (Daylight). */
    danger: '#b3261e',
    /** Confirmations such as a confirmed seat. 10.71:1 and 9.47:1 (Evening); 4.77:1 and 5.32:1 (Daylight). */
    success: '#1e7a4e',
    /** Behind sheets and dialogs. Dims the screen without hiding it; never carries text. */
    scrim: 'rgba(36,24,44,0.40)',
  },
} as const;

export type ColorName = keyof (typeof colors)['evening'];
export type Palette = Record<ColorName, string>;

/** Text styles. Display styles name a bundled Bodoni Moda face; the rest use the platform font. */
export const typography = {
  /** Countdown numerals and the reveal headline. At most one per screen. */
  displayXl: { fontFamily: 'BodoniModa28pt-Medium', fontSize: 44, lineHeight: 48 },
  /** Screen titles: Tonight, Dates, Backstage. */
  displayL: { fontFamily: 'BodoniModa28pt-Medium', fontSize: 32, lineHeight: 38 },
  /** A dater's first name and age, wherever it appears. The only italic display style. */
  displayName: { fontFamily: 'BodoniModa28pt-MediumItalic', fontSize: 24, lineHeight: 30 },
  /** Section headings inside a screen. */
  title: { fontSize: 20, lineHeight: 26, fontWeight: '600' },
  /** Live captions in the room, read at arm's length or on mute. */
  captionLive: { fontSize: 20, lineHeight: 28, fontWeight: '400' },
  /** Card titles and button labels. */
  headline: { fontSize: 17, lineHeight: 22, fontWeight: '600' },
  /** Default reading text and chat messages. */
  body: { fontSize: 17, lineHeight: 24, fontWeight: '400' },
  /** Supporting copy in cards and sheets. */
  callout: { fontSize: 15, lineHeight: 21, fontWeight: '400' },
  /** Meta such as listener counts and timestamps. The smallest text in the app. */
  footnote: { fontSize: 13, lineHeight: 18, fontWeight: '400' },
} as const;

export type TypographyName = keyof typeof typography;

/** Spacing on a 4px base. */
export const space = {
  /** Icon to label. */
  xxs: 4,
  /** Between related items: chips, an avatar pair. */
  xs: 8,
  /** Inside compact cards and between list rows. */
  s: 12,
  /** Card padding and the gap between cards. */
  m: 16,
  /** Left and right screen margins. */
  screenMargin: 20,
  /** Between sections on a screen. */
  l: 24,
  /** Around full-screen moments such as backstage and the reveal. */
  xl: 32,
  /** Top of a full-screen moment, below the status bar. */
  xxl: 48,
} as const;

/** Corner radii. */
export const radius = {
  /** Thumbnails and tags. */
  s: 10,
  /** Inputs and compact cards. */
  m: 16,
  /** Cards, sheets and the seat ticket. */
  l: 24,
  /** Buttons, chips and the on-air lamp. */
  pill: 999,
} as const;

/** Fixed sizes for controls and avatars. */
export const size = {
  /** Smallest tappable area on any control. */
  touchTarget: 44,
  /** Buttons and inputs. */
  controlHeight: 52,
  /** Initial discs on lineup cards and blind cards. */
  avatarM: 56,
} as const;
