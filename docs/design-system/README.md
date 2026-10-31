# Unseen

Voices first. Faces only when you both say yes.

Unseen is a live, audio-only dating show for iOS and Android: two verified singles talk on air, an AI host called Nova runs the show, and a city audience listens, votes and sends gestures. This system covers the mobile app. It is dark-first because the show happens at night, and because the product's one big visual moment, the reveal, is the lights coming up. "Unseen" and "Nova" are working names until a trademark search clears them.

## The idea: a members' club after dark

Posh, here, means manners rather than gatekeeping. Anyone can listen; going on air feels like being cast. A considered room sets the tone for how people behave in it, so restraint is part of the safety model, not decoration.

- Spend boldness in one place: the reveal. Every other screen stays quiet.
- Names are set like a printed programme, in italic Bodoni (`display-name`). Nothing else uses italic display type.
- Nothing shouts. No confetti, no top-gifter leaderboards, no timers invented to make someone spend. The only clocks are real ones: the date timer and the decision window.

## Voice

Nova is warm, quick and kind. Teasing is fine; humiliation is not. Nova never pretends to be human and is labelled "Nova (AI)" the first time it appears on any screen. Interface copy is sentence case, plain and calm, with no exclamation marks.

| Write | Not |
| --- | --- |
| Request a seat | Join the queue |
| Keep it unseen | Decline |
| Not a match this time | Rejected |
| Leave date | Quit |
| Only you can see this screen. | Your choice is confidential. |
| A rose, 25 coins ([PRICE]) | A rose, 25 |

Consent copy is explicit and symmetric: say who sees what, and what happens if the other person says no. Money is shown in the person's currency at the moment of spending. Errors say what happened, then what to do: "Your ID photo was too blurry to read. Try again in better light."

## Color

Two themes. Evening is the default for every screen. Daylight is used by the reveal and is ready for a light appearance later.

- One `lamplight` fill per screen, on the primary action, with `lamplight-ink` text. On consent screens (Reveal or Keep it unseen) neither option gets the fill.
- `on-air` means live and nothing else: the lamp and its label.
- `nova` marks the host, always beside the word Nova, never color alone.
- `danger` always comes with an icon and words.
- `seam` is decorative. A control's edge uses `seam-strong`, 3:1 or more.
- Elevation is surface steps, not shadows: `velvet`, then `plush`, then `plush-raised`. Sheets sit over `scrim`.

Every text token meant for `velvet`, `plush` and `plush-raised` is at least 4.5:1 on all three in both themes. Exact ratios are in each token's usage note.

## Type

Display: Bodoni Moda (open source, SIL OFL), with optical sizing on, only at 24px and up: screen titles, names, countdowns and the reveal. Text: the platform font (SF Pro on iOS, Roboto on Android), so Dynamic Type and font scaling work. Every style scales with the person's text size setting, and layouts reflow up to 200%. Live captions use `caption-live` (20px) because people read them at arm's length or on mute.

## Space and shape

A 4px base. Screen margins are `screen-margin` (20px), cards pad `space-m`, and sections sit `space-l` apart. Cards and sheets use `radius-l`, inputs `radius-m`, and buttons and chips are pills. The seat card is cut as a ticket, with two 12px notches at mid-height.

## Iconography

Phosphor Icons (MIT), Regular weight, 24px, outline; filled only for the selected tab. Two brand glyphs: the on-air lamp (an 8px `on-air` disc beside its label) and Nova's ring (a 2px circle). Nova is never drawn as a sparkle or a robot. Icon-only buttons carry an accessible name, such as "Minimize" or "Play Maya's voice intro".

## Motion

Quick and functional: 200ms ease-out for sheets and state changes. The speaking ring follows the speaker's voice. The reveal is the one orchestrated moment: `velvet` crossfades to Daylight over 1.2s while both photos fade in together, with a success haptic. With Reduce Motion on, everything swaps instantly.

## The show

One date runs about ten minutes on air, in a fixed running order, so Nova carries moments rather than a whole evening. The time marks are targets: Nova waits for conversational pauses, with up to sixty seconds of discretionary overrun, shortening optional content first. Safety holds and the full private decision window are protected (decision 0012).

| Time | Segment | What happens |
| --- | --- | --- |
| 0:00 | Doors | Nova introduces both daters from their blind cards, with a few seconds of each voice intro. |
| 0:45 | First question | Each dater gets one of their own prompts back. |
| 1:30 | Free talk | Nova stays quiet unless there's a long silence. |
| 4:00 | From the room | One or two audience questions, screened for kindness and personal details. |
| 5:30 | Lightning round | Only if the room unlocked it: three rapid questions. |
| 7:00 | Last word | Twenty seconds each: something they'd want the other to know. |
| 8:00 | Time | The daters decide privately while Nova talks to the room. |
| 9:00 | The result | Mutual, or "unseen tonight", never who chose what. Then, at most once a night per listener, the invitation to take a seat. |
| 9:30 | Next up | A teaser of the next pair, from their blind cards. |

## Nova on air

A late-night radio host at a members' club: warm, quick and curious, laughing with people and never at them. Nova keeps well under a fifth of the airtime, asks rather than remarks, and uses names and pronouns exactly as written.

| Moment | Nova says |
| --- | --- |
| Opening | "Good evening, room. On my left, Maya, 29, who says to ask about her worst haircut. On my right, Theo, 31, who claims he can parallel park anything." |
| A long silence | "Theo, you say you can parallel park anything. Defend that." |
| Talking over each other | "One at a time. I want both of those stories. Maya first." |
| A personal detail slips | "Let's keep street names off air. The story's better without them anyway." |
| An unkind remark | "Let's keep it kind." If it happens again, the date pauses: "We're taking a short pause." |
| Someone leaves | "Theo's had to step away, and that's always allowed. Maya, thank you." |
| Not mutual | "They're keeping it unseen tonight. Two brave people and a good conversation." |
| Mutual | "It's mutual. What happens next is theirs." |
| The invitation | "If that sounded like your kind of night, there are seats this week." |

Nova never claims to be human, pushes anyone to reveal, comments on looks or voices, guesses a decision, asks a named listener to spend, or reads out a question about where someone lives or works.

## Sound and haptics

An audio show needs a sonic identity as much as a visual one. Sounds are short and warm, and never play over someone speaking.

- On air: three rising notes, under a second, as a date starts.
- Time's up: two soft falling notes.
- The reveal: four rising notes over about 1.3 seconds, with a success haptic for the two daters.
- A gesture landing: one soft tick for the sender, rate-limited. Daters never hear gesture sounds during their date.
- Applause: a low murmur under the room, scaled to the applause rate and capped.
- Space: in stereo, the daters sit slightly left and right and Nova sits in the center, so a listener in headphones always knows who's talking.

Nova's voice is warm, mid-low, unhurried (around 150 words a minute), clear and playful, and never a soundalike of a real person. Every sound follows the system mute, and captions carry the same information for anyone who can't hear it.

## Accessibility

WCAG 2.2 AA is the floor: text at 4.5:1, control borders and focus rings at 3:1, targets at least 44 by 44px, a 2px `focus` ring offset 2px, captions available for every live date, and screen-reader labels that carry state ("Maya, 29, she/her, speaking").

## Components

Actions and forms: Button, Input, Chip. The show: Lineup card, Seat ticket, Live captions. People and conversation: Blind card, Chat bubble. Navigation and overlays: Tab bar, Sheet, Toast. Each has guidelines and a live preview.
