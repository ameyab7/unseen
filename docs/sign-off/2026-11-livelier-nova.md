# Sign-off: the lightning round's intro, and an honest room-talk fallback

Status: approved by the product owner on 2026-11-02; in `services/nova/nova/script.py`.

## Why

- **The lightning round starts unannounced.** In the 2026-10-31 rehearsal the product owner
  "wasn't made aware that question was a lightning round question", and in the 2026-11-02
  simulated dates its first quick question came straight after free talk every time. An
  approved line said as the round starts would tell the daters and the room what's
  happening. ("The lightning round is unlocked. Three quick ones, coming up." is said only
  when the room unlocks it, often minutes before.)
- **The one room-talk fallback claims something Nova can't know.** "Thanks for keeping the
  chat kind tonight, room." is said whenever the model's talk to the room fails a check, and
  in one simulated date it came during a pause for an unkind remark. Nova never sees the
  chat (decision 0005, room talk; `show_state.ROOM_FICTION` now rejects model lines that
  claim it).

Each line passes the guardrails for its moment (`tests/test_sign_off.py`).

## The lightning round's intro (pick one, or several to take in turn)

| # | Line | Characters |
| --- | --- | --- |
| 1 | Lightning round. Three quick ones, first thing that comes to mind. | 66 |
| 2 | Quick-fire time. Three questions, no overthinking allowed. | 58 |
| 3 | Lightning round. Short answers, big honesty. Here we go. | 56 |

## The room-talk fallback (to replace "Thanks for keeping the chat kind tonight, room.")

| # | Line | Characters |
| --- | --- | --- |
| 1 | Thanks for staying with us, room. If a question for our daters comes to mind, send it in. | 89 |
| 2 | Room, thank you for being here with us tonight. | 47 |

Line 1 suits a hold; line 2 also suits the end of a date that ended early.

## Sign-off

- Product owner: approved in the Claude Code session of 2026-11-02
- Date: 2026-11-02
- Approved lines: all of the above, taken in turn
