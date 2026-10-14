"""Nova's lines: what Nova actually says for each cue from the show runner.

- Approved lines (cue text) are spoken as written.
- Set pieces (the Doors intro, first questions, lightning questions, last words, the outro
  and Next up) are written before the date by prepare(). The model is never called for
  them during the date; anything missing uses its fallback.
- Live reactions (a silence, an audience question, talk to the room, a reply to a dater)
  call the model during the date, within live_timeout.

Every model line passes the guardrails or is replaced by a fallback line. The log records
events and timings only, never text.
"""

import asyncio
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from nova import prompts, script
from nova.cards import Pair, Seat
from nova.cues import Moment, RoomFacts, Say
from nova.events import Event, EventLog
from nova.guardrails import Violation, check, clean, question_is_off_limits
from nova.models import LineModel, ModelRefused

SET_PIECES = frozenset(
    {
        Moment.DOORS_INTRO,
        Moment.FIRST_QUESTION,
        Moment.LIGHTNING_QUESTION,
        Moment.LAST_WORD,
        Moment.OUTRO,
        Moment.NEXT_UP,
    }
)
LIVE_REACTIONS = frozenset({Moment.SILENCE, Moment.FROM_THE_ROOM, Moment.ROOM_TALK, Moment.REPLY})


class Source(StrEnum):
    FIXED = "fixed"  # an approved line from the show runner
    PREPARED = "prepared"  # a set piece the model wrote before the date
    MODEL = "model"  # a live reaction the model wrote during the date
    FALLBACK = "fallback"  # an approved fallback line


class ErrorKind(StrEnum):
    TIMEOUT = "timeout"
    REFUSED = "refused"
    ERROR = "error"
    NOT_PREPARED = "not_prepared"
    DECLINED = "declined"  # an audience question Nova won't read


@dataclass(frozen=True)
class Rules:
    max_chars: int = 200
    max_sentences: int = 3


def rules_for(moment: Moment) -> Rules:
    if moment is Moment.DOORS_INTRO:
        return Rules(max_chars=320, max_sentences=4)
    if moment in (Moment.NEXT_UP, Moment.FROM_THE_ROOM):
        return Rules(max_chars=260)
    return Rules()


def names_for(moment: Moment, pair: Pair, next_pair: Pair | None) -> frozenset[str]:
    """The only names a line may contain: tonight's daters, Nova, and for Next up the next
    pair."""
    names = {pair.left.first_name, pair.right.first_name, "Nova"}
    if moment is Moment.NEXT_UP and next_pair is not None:
        names |= {next_pair.left.first_name, next_pair.right.first_name}
    return frozenset(names)


@dataclass(frozen=True)
class Line:
    text: str
    source: Source
    moment: Moment
    violations: tuple[Violation, ...] = ()  # what the rejected model line broke, if any
    error: ErrorKind | None = None
    ms: int = 0


@dataclass(frozen=True)
class Attempt:
    """One try at a model line, before any fallback."""

    text: str | None
    violations: tuple[Violation, ...]
    ms: int
    error: ErrorKind | None

    @property
    def ok(self) -> bool:
        return self.text is not None and not self.violations and self.error is None


def fallback_lines(pair: Pair, next_pair: Pair | None) -> list[tuple[Moment, str]]:
    """Every fixed fallback line for this pair, for sign-off and for tests."""
    lines = [
        (Moment.DOORS_INTRO, script.doors_fallback(pair)),
        (Moment.FIRST_QUESTION, script.first_question_fallback(pair.left)),
        (Moment.FIRST_QUESTION, script.first_question_fallback(pair.right)),
        *((Moment.LIGHTNING_QUESTION, q) for q in script.LIGHTNING_FALLBACKS),
        (Moment.LAST_WORD, script.last_word_fallback(pair.left)),
        (Moment.LAST_WORD, script.last_word_fallback(pair.right)),
        (Moment.OUTRO, script.outro_fallback(pair)),
        *((Moment.SILENCE, s) for s in script.silence_fallbacks(pair)),
        (Moment.FROM_THE_ROOM, script.from_the_room_fallback(pair)),
        (Moment.FROM_THE_ROOM, script.DECLINE_QUESTION),
        (Moment.ROOM_TALK, script.ROOM_TALK_FALLBACK),
        (Moment.REPLY, script.REPLY_FALLBACK),
    ]
    if next_pair is not None:
        lines.append((Moment.NEXT_UP, script.next_up_fallback(next_pair)))
    return lines


class LineWriter:
    def __init__(
        self,
        model: LineModel,
        pair: Pair,
        *,
        next_pair: Pair | None = None,
        live_timeout: float = 2.0,
        prep_timeout: float = 60.0,
        events: EventLog | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._model = model
        self._pair = pair
        self._next_pair = next_pair
        self._live_timeout = live_timeout
        self._prep_timeout = prep_timeout
        self._events = events or EventLog()
        self._rng = rng or random.Random()  # picks shapes and openings, so lines vary
        self._prepared: dict[tuple[Moment, Seat | None, str | None], Line] = {}
        self._silences = 0
        self._silence_lines: list[str] = []
        self._room_talk_lines: list[str] = []

    # --- Before the date ------------------------------------------------------------------

    async def prepare(self) -> None:
        """Writes every set piece, one at a time (lightning questions see the earlier ones)."""
        started = time.monotonic()
        for say in self._set_piece_cues():
            attempt = await self._attempt(say, self._prompt_for(say), self._prep_timeout)
            self._prepared[_key(say)] = self._settle(say, attempt, Source.PREPARED)
        fallbacks = sum(line.source is Source.FALLBACK for line in self._prepared.values())
        self._events.emit(
            Event.PREPARED,
            count=len(self._prepared),
            fallbacks=fallbacks,
            ms=_ms_since(started),
        )

    # --- During the date ------------------------------------------------------------------

    async def line_for(self, say: Say) -> Line:
        line = await self._line_for(say)
        self._events.emit(
            Event.LINE,
            moment=line.moment,
            source=line.source,
            ms=line.ms,
            error=line.error,
            violations=line.violations,
        )
        return line

    async def attempt_for(self, say: Say) -> Attempt:
        """One model attempt for a cue, with no fallback: for the evaluation script."""
        timeout = self._prep_timeout if say.moment in SET_PIECES else self._live_timeout
        return await self._attempt(say, self._prompt_for(say), timeout)

    async def _line_for(self, say: Say) -> Line:
        if say.text is not None:
            return Line(say.text, Source.FIXED, say.moment)
        if say.moment in SET_PIECES:
            prepared = self._prepared.get(_key(say))
            if prepared is not None:
                return prepared
            return self._fallback(say, Attempt(None, (), 0, ErrorKind.NOT_PREPARED))
        if say.moment not in LIVE_REACTIONS:
            raise ValueError(f"No line for {say.moment}: it needs approved text")

        if say.moment is Moment.SILENCE:
            self._silences += 1
        if say.moment is Moment.FROM_THE_ROOM and question_is_off_limits(say.detail or ""):
            return Line(
                script.DECLINE_QUESTION, Source.FALLBACK, say.moment, error=ErrorKind.DECLINED
            )

        attempt = await self._attempt(say, self._prompt_for(say), self._live_timeout)
        line = self._settle(say, attempt, Source.MODEL)
        if line.source is Source.MODEL:
            if say.moment is Moment.SILENCE:
                self._silence_lines = [*self._silence_lines, line.text][-5:]
            if say.moment is Moment.ROOM_TALK:
                self._room_talk_lines = [*self._room_talk_lines, line.text][-5:]
        return line

    # --- Internals ------------------------------------------------------------------------

    async def _attempt(self, say: Say, prompt: str, timeout: float) -> Attempt:
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                self._model.write(system=prompts.SYSTEM_PROMPT, prompt=prompt), timeout
            )
        except TimeoutError:
            return Attempt(None, (), _ms_since(started), ErrorKind.TIMEOUT)
        except ModelRefused:
            return Attempt(None, (), _ms_since(started), ErrorKind.REFUSED)
        except Exception:  # any model failure: the room hears a fallback, not an error
            return Attempt(None, (), _ms_since(started), ErrorKind.ERROR)
        text = clean(raw)
        return Attempt(text, self._check(say, text), _ms_since(started), None)

    def _check(self, say: Say, text: str) -> tuple[Violation, ...]:
        """The guardrails for this cue. What a dater or listener said for it (a reply or an
        audience question) is passed in so no proper noun from it is repeated."""
        rules = rules_for(say.moment)
        said = [say.detail] if say.moment in (Moment.REPLY, Moment.FROM_THE_ROOM) else []
        return check(
            text,
            names=names_for(say.moment, self._pair, self._next_pair),
            max_chars=rules.max_chars,
            max_sentences=rules.max_sentences,
            echo_from=[s for s in said if s],
        )

    def _settle(self, say: Say, attempt: Attempt, success: Source) -> Line:
        if attempt.ok and attempt.text is not None:
            return Line(attempt.text, success, say.moment, ms=attempt.ms)
        return self._fallback(say, attempt)

    def _fallback(self, say: Say, attempt: Attempt) -> Line:
        """The first fallback that passes the guardrails; the last one always does."""
        options = self._fallbacks_for(say)
        chosen = next((o for o in options[:-1] if not self._check(say, o)), options[-1])
        return Line(
            chosen,
            Source.FALLBACK,
            say.moment,
            violations=attempt.violations,
            error=attempt.error,
            ms=attempt.ms,
        )

    def _fallbacks_for(self, say: Say) -> Sequence[str]:
        pair = self._pair
        match say.moment:
            case Moment.DOORS_INTRO:
                return [script.doors_fallback(pair)]
            case Moment.FIRST_QUESTION:
                return [script.first_question_fallback(pair.card(_seat(say)))]
            case Moment.LIGHTNING_QUESTION:
                return [script.LIGHTNING_FALLBACKS[_lightning_number(say) - 1]]
            case Moment.LAST_WORD:
                return [script.last_word_fallback(pair.card(_seat(say)))]
            case Moment.OUTRO:
                return [script.outro_fallback(pair)]
            case Moment.NEXT_UP:
                return [script.next_up_fallback(self._next_pair or pair)]
            case Moment.SILENCE:
                options = script.silence_fallbacks(pair)
                return [options[(max(self._silences, 1) - 1) % len(options)]]
            case Moment.FROM_THE_ROOM:
                return [
                    script.from_the_room_verbatim(say.detail or ""),
                    script.from_the_room_fallback(pair),
                ]
            case Moment.ROOM_TALK:
                return [script.ROOM_TALK_FALLBACK]
            case Moment.REPLY:
                return [script.REPLY_FALLBACK]
        raise ValueError(f"No fallback for {say.moment}")

    def _prompt_for(self, say: Say) -> str:
        pair = self._pair
        match say.moment:
            case Moment.DOORS_INTRO:
                return prompts.doors_intro(pair)
            case Moment.FIRST_QUESTION:
                return prompts.first_question(pair, _seat(say))
            case Moment.LIGHTNING_QUESTION:
                earlier = [
                    line.text
                    for key, line in self._prepared.items()
                    if key[0] is Moment.LIGHTNING_QUESTION and line.source is Source.PREPARED
                ]
                return prompts.lightning_question(pair, _lightning_number(say), previous=earlier)
            case Moment.LAST_WORD:
                return prompts.last_word(pair, _seat(say))
            case Moment.OUTRO:
                return prompts.outro(pair)
            case Moment.NEXT_UP:
                shape = self._rng.choice(prompts.NEXT_UP_SHAPES)
                return prompts.next_up(self._next_pair or pair, shape=shape)
            case Moment.SILENCE:
                return prompts.silence(pair, previous=self._silence_lines)
            case Moment.FROM_THE_ROOM:
                return prompts.from_the_room(pair, say.detail or "")
            case Moment.ROOM_TALK:
                opening = self._rng.choice(prompts.ROOM_TALK_OPENINGS)
                return prompts.room_talk(
                    _room(say), opening=opening, previous=self._room_talk_lines
                )
            case Moment.REPLY:
                return prompts.reply(pair, _seat(say), say.detail or "")
        raise ValueError(f"No prompt for {say.moment}")

    def _set_piece_cues(self) -> list[Say]:
        pair = self._pair
        cues = [
            Say(Moment.DOORS_INTRO),
            *(
                Say(Moment.FIRST_QUESTION, about=seat, detail=pair.card(seat).prompts[0].text)
                for seat in Seat
            ),
            *(Say(Moment.LIGHTNING_QUESTION, detail=f"{n} of 3") for n in (1, 2, 3)),
            *(Say(Moment.LAST_WORD, about=seat) for seat in Seat),
            Say(Moment.OUTRO),
        ]
        if self._next_pair is not None:
            cues.append(Say(Moment.NEXT_UP, detail=self._next_pair.label))
        return cues


def _key(say: Say) -> tuple[Moment, Seat | None, str | None]:
    if say.moment is Moment.LIGHTNING_QUESTION:
        return (say.moment, None, say.detail)
    if say.moment in (Moment.FIRST_QUESTION, Moment.LAST_WORD):
        return (say.moment, say.about, None)
    return (say.moment, None, None)


def _seat(say: Say) -> Seat:
    if say.about is None:
        raise ValueError(f"{say.moment} needs a seat")
    return say.about


def _room(say: Say) -> RoomFacts:
    if say.room is None:
        raise ValueError(f"{say.moment} needs room facts")
    return say.room


def _lightning_number(say: Say) -> int:
    return int((say.detail or "1 of 3").split()[0])


def _ms_since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
