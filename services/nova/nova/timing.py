"""When each beat of a date happens, in seconds from the start of the date.

FULL follows the brand book's running order (about ten minutes). REHEARSAL squeezes the
same order into about three minutes for testing in the Agents Playground.
"""

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class Timing:
    # Segment starts (brand book running order).
    first_question: float = 45.0
    free_talk: float = 90.0
    from_the_room: float = 240.0
    lightning: float = 330.0
    last_word: float = 420.0
    time: float = 480.0

    # Gaps inside segments. Assumptions: the brand book gives the segments, not these.
    second_prompt_after: float = 22.5  # the right-hand dater's prompt, after the left's
    second_question_after: float = 45.0  # the second audience question
    lightning_gap: float = 30.0  # between the three lightning questions
    second_last_word_after: float = 25.0  # 20 s each, plus a short hand-over

    # The decision window (prototype: 60 seconds) and the teaser after the result.
    decision_window: float = 60.0
    next_up_after_result: float = 30.0

    # Need a moment: the clock holds until the dater is back. After 30 seconds (prototype:
    # "Nova is covering for 30 seconds") the moderator is alerted and decides what's next.
    moment_alert_after: float = 30.0
    # While the show is held, Nova talks to the room every cover_every seconds, at most
    # cover_lines lines; then the recorded fallback segment plays (at the latest at the
    # alert), so the room never sits in silence. Assumptions, like the gaps above.
    cover_every: float = 10.0
    cover_lines: int = 3
    # After a dater leaves: one line to the room, then Next up.
    next_up_after_leave: float = 12.0

    # Long silence in free talk. Assumptions: the brand book says "a long silence".
    silence_after: float = 8.0
    silence_cooldown: float = 20.0

    # A beat's lines and sounds wait while a dater is mid-sentence (the brand book: sounds
    # never play over someone speaking), but never longer than this.
    talk_over_grace: float = 5.0
    # A dater's turn (a question, a last word, "That's time") never comes until the dater
    # asked before had this long to start answering, from when Nova finished asking them.
    # Only when Nova's lines are reported played (ShowRunner paced); the schedule gives far
    # more in a full date, so this matters when Nova runs late or in a rehearsal.
    answer_window: float = 6.0
    # ... and until nobody has spoken for this long: people pause between sentences,
    # and the voice detector hears each pause as the end of their speech.
    turn_gap: float = 1.5

    def __post_init__(self) -> None:
        starts = [
            self.first_question,
            self.free_talk,
            self.from_the_room,
            self.lightning,
            self.last_word,
            self.time,
        ]
        if starts != sorted(starts) or len(set(starts)) != len(starts):
            raise ValueError("Segment starts must be in running-order sequence")

    def scaled(self, factor: float) -> Timing:
        """Shrinks every beat by factor, except the silence rule, the talk-over grace and
        the answer window: people need the same few seconds to fill a silence, finish a
        sentence or start an answer whether or not it's a rehearsal."""
        keep = {
            "silence_after",
            "silence_cooldown",
            "talk_over_grace",
            "answer_window",
            "turn_gap",
            "cover_lines",
        }
        return replace(
            self,
            **{
                f.name: round(getattr(self, f.name) * factor, 3)
                for f in fields(self)
                if f.name not in keep
            },
        )


FULL = Timing()
REHEARSAL = FULL.scaled(0.3)
