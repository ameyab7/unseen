# Sign-off: bigger fallback pools

Status: proposed. Waiting for the product owner. None of these lines is in
`services/nova/nova/script.py` until it's signed off; they live in
`services/nova/nova/sign_off.py`, where a test checks every one against the guardrails
for its moment (`tests/test_sign_off.py`).

## Why

A night that falls back often repeats the same lines on every date: today there are 3
lightning, 3 silence, 4 outro and 1 room-talk fallback, and decision 0005 says lines vary
across the night. Fallbacks take turns (the one least like what Nova has said tonight), so
a bigger pool means fewer repeats.

## The lines

In Nova's voice (brand book: Voice, Nova on air), built only from first names, never card
text. `{left}` and `{right}` are the daters' first names; lengths are with Maya and Theo.
Lightning questions name nobody, and the last-question ones only ever come last.

### Lightning questions (12)

| Line | Characters | Guardrails |
| --- | ---: | --- |
| Quick one: sunrise or sunset? | 29 | passes |
| Quick one: tea or coffee? | 25 | passes |
| Quick one: beach holiday or city break? | 39 | passes |
| Quick one: sweet or savoury? | 28 | passes |
| Quick one: cats or dogs? | 24 | passes |
| Quick one: books or films? | 26 | passes |
| Quick one: a perfect Sunday starts with... | 42 | passes |
| Quick one: the last song you sang along to? | 43 | passes |
| Quick one: this week in one word? | 33 | passes |
| Quick one: dancing or karaoke? | 30 | passes |
| Quick one: a skill you'd love to learn overnight? | 49 | passes |
| Quick one: breakfast in bed or breakfast out? | 45 | passes |

### Lightning questions, last only (3)

| Line | Characters | Guardrails |
| --- | ---: | --- |
| Last quick one: the best advice you've ever had, in three words? | 64 | passes |
| Last quick one: a small thing you're looking forward to? | 56 | passes |
| Last quick one: your go-to comfort food? | 40 | passes |

### Silence prompts to both daters (10)

| Line | Characters | Guardrails |
| --- | ---: | --- |
| {left}, {right}, one for you both: what's a smell that takes you straight back to childhood? | 87 | passes |
| {left}, {right}, here's one: what's the best meal you've had this year? | 66 | passes |
| {left}, {right}, one for you both: what would a perfect lazy Sunday look like? | 73 | passes |
| {left}, {right}, here's one: what's a film you could watch again tonight? | 68 | passes |
| {left}, {right}, one for you both: what's something small that made you laugh this week? | 83 | passes |
| {left}, {right}, here's one: what's a place you'd love to see one day? | 65 | passes |
| {left}, {right}, one for you both: what's a hobby you'd pick up if you had the time? | 79 | passes |
| {left}, {right}, here's one: what's the best compliment you've ever been given? | 74 | passes |
| {left}, {right}, one for you both: what's a song that always lifts your mood? | 72 | passes |
| {left}, {right}, here's one: what's a tradition you'd never give up? | 63 | passes |

### Outros (4 more)

| Line | Characters | Guardrails |
| --- | ---: | --- |
| Thank you, {left} and {right}, for a lovely conversation. | 52 | passes |
| {left} and {right}, thank you for sharing your evening with us. | 58 | passes |
| That was {left} and {right}, and we're grateful to you both. | 55 | passes |
| Thank you both, {left} and {right}, it's been a pleasure. | 52 | passes |

## Sign-off

- Product owner: ____________________
- Date: ____________________
- Approved lines (all, or list): ____________________
