# Sign-off: more room-talk fallbacks

Status: proposed. Waiting for the product owner. Until then they are not in
`services/nova/nova/script.py`, and Nova has one room-talk fallback: "Thanks for keeping the
chat kind tonight, room."

## Why

While the show is held (a dater asked for a moment, or reported), Nova now talks to the
room every 10 seconds, at most three lines a hold, before the recorded fallback segment
plays (decision 0004). The model writes those lines. When it fails, Nova says the one
fallback once per hold, and then nothing, so a slow model leaves gaps. More fallbacks,
taken in turn like the silence fallbacks, would fill them without repeating within a hold.

## The lines

None names a dater, says who stepped away or why, mentions the outcome, spending or anyone's
looks, or describes a place. Each passes the guardrails for talk to the room (checked with
`guardrails.check` and `rules_for(Moment.ROOM_TALK)`, Maya and Theo on air).

| # | Line | Characters |
| --- | --- | --- |
| 1 | Room, thanks for staying with us. The chat has been lovely tonight. | 67 |
| 2 | While we wait, room, what would you ask next? Tell us in the chat. | 66 |
| 3 | Stay with us, room. Good conversations are worth a short wait. | 62 |
| 4 | Room, a quick thank-you for keeping the chat kind and curious. | 62 |
| 5 | We will be right back with you, room. Thanks for your patience. | 63 |
| 6 | Room, your questions keep this show going. Thank you for every one of them. | 75 |

Lines 2, 3 and 5 suit a hold; 1, 4 and 6 also suit the end of a date that ended early,
where Nova thanks the room without saying why.

## Sign-off

- Product owner: ____________________
- Date: ____________________
- Approved lines: ____________________
