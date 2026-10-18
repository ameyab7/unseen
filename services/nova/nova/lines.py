"""Nova's lines: what Nova actually says for each cue from the show runner.

- Approved lines (cue text) are spoken as written.
- Set pieces (the Doors intro, first questions, lightning questions, last words, the outro
  and Next up) are written before the date by prepare(), three candidates each; the one
  kept passes the guardrails and is least like what Nova has said tonight. Different
  moments are written at the same time, within one budget for the whole date
  (NOVA_PREP_BUDGET_SECONDS). The model is never called for them during the date;
  anything missing uses its fallback.
- Live reactions (a silence, an audience question, talk to the room, a reply to a dater)
  call the model during the date, within live_timeout.

Every model line passes the guardrails or is replaced by a fallback line. Every prompt
sees what Nova has said tonight for that moment, across dates (nova/tonight.py), except
audience questions and replies, which are never remembered. The log records events and
timings only, never text.
"""

import asyncio
import contextlib
import random
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from nova import prompts, running_order, script
from nova.cards import BlindCard, Pair, Seat
from nova.config import Settings
from nova.cues import Moment, RoomFacts, Say, Situation
from nova.distress import has_warning, is_unmistakable
from nova.events import Event, EventLog
from nova.guardrails import (
    SENTENCE_END,
    SHOW_NAME,
    Violation,
    card_words,
    check,
    clean,
    question_is_off_limits,
)
from nova.measure import ms_since
from nova.models import WARM_UP_TIMEOUT, LineModel, ModelRefused, Warmed, warm_up
from nova.text import plain
from nova.tonight import SHOWN, SHOWN_LIVE, Remembered, Tonight

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
# Live reactions kept in tonight's memory. Not audience questions or replies: their lines
# carry what a listener or dater said, and the memory reaches every later date's prompts.
REMEMBERED_LIVE = frozenset({Moment.SILENCE, Moment.ROOM_TALK})
CANDIDATES = 3  # model lines written for each set piece; the best one is kept
# What Nova says while the show is held, or as a date ends early: any other line means the
# hold is over, and the next hold may use the room-talk fallback again.
HOLD_MOMENTS = frozenset(
    {
        Moment.ROOM_TALK,
        Moment.NEED_A_MOMENT,
        Moment.SHOW_PAUSE,
        Moment.BEAR_WITH_US,
        Moment.DATER_LEFT,
    }
)


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
    questions: bool = True
    open_with_names: bool = True


def rules_for(moment: Moment) -> Rules:
    if moment is Moment.DOORS_INTRO:
        return Rules(max_chars=320, max_sentences=4)
    if moment in (Moment.NEXT_UP, Moment.FROM_THE_ROOM):
        return Rules(max_chars=260)
    if moment is Moment.OUTRO:
        return Rules(max_sentences=1, questions=False)
    if moment is Moment.LIGHTNING_QUESTION:
        return Rules(max_sentences=1, open_with_names=False)
    return Rules()


def cards_for(moment: Moment, pair: Pair, next_pair: Pair | None) -> list[BlindCard]:
    """The daters a line is about: the pair on air, and for Next up the next pair too."""
    cards = [pair.left, pair.right]
    if moment is Moment.NEXT_UP and next_pair is not None:
        cards += [next_pair.left, next_pair.right]
    return cards


def names_for(moment: Moment, pair: Pair, next_pair: Pair | None) -> frozenset[str]:
    """The only first names a line may contain: the daters it's about, and Nova."""
    return frozenset({card.first_name for card in cards_for(moment, pair, next_pair)} | {"Nova"})


@dataclass(frozen=True)
class Line:
    """What Nova says for a cue. An empty text: Nova says nothing for it."""

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
        *((Moment.LIGHTNING_QUESTION, q) for q in script.LIGHTNING_LAST_FALLBACKS),
        (Moment.LAST_WORD, script.last_word_fallback(pair.left)),
        (Moment.LAST_WORD, script.last_word_fallback(pair.right)),
        *((Moment.OUTRO, o) for o in script.outro_fallbacks(pair)),
        *((Moment.SILENCE, s) for s in script.silence_fallbacks(pair)),
        (Moment.FROM_THE_ROOM, script.from_the_room_fallback(pair)),
        (Moment.ROOM_TALK, script.ROOM_TALK_FALLBACK),
        (Moment.REPLY, script.REPLY_FALLBACK),
        (Moment.REPLY, script.TAKE_YOUR_TIME),
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
        live_timeout: float = Settings.live_timeout,
        prep_timeout: float = Settings.prep_timeout,
        prep_budget: float = Settings.prep_budget,
        prep_parallel: int | None = None,
        events: EventLog | None = None,
        rng: random.Random | None = None,
        tonight: Tonight | None = None,
    ) -> None:
        """prep_parallel: at most this many requests for set pieces in flight at once
        (NOVA_PREP_PARALLEL); None, no limit."""
        self._model = model
        self._prep_slots = asyncio.Semaphore(prep_parallel) if prep_parallel else None
        self._pair = pair
        self._next_pair = next_pair
        self._live_timeout = live_timeout
        self._prep_timeout = prep_timeout
        self._prep_budget = prep_budget
        self._events = events or EventLog()
        self._rng = rng or random.Random()  # picks shapes and openings, so lines vary
        # This date's lightning questions: one of each format, in a random order.
        self._lightning_forms = self._rng.sample(
            prompts.LIGHTNING_FORMS, len(prompts.LIGHTNING_FORMS)
        )
        self._tonight = tonight or Tonight()  # shared by every date's writer tonight
        # The daters this writer's lines may be about, masked in tonight's memory.
        self._names = {card.first_name for card in cards_for(Moment.NEXT_UP, pair, next_pair)}
        self._prepared: dict[_Key, Line] = {}
        # Set pieces in tonight's memory as planned: kept once spoken, dropped by finish().
        self._planned: dict[_Key, Remembered] = {}
        self.budget_exhausted = False  # the prep budget ran out before every piece was ready
        self._fallback_in_hold = False  # the room-talk fallback was said in this hold

    # --- Before the date ------------------------------------------------------------------

    async def warm_up(self, *, timeout: float = WARM_UP_TIMEOUT) -> Warmed:
        """One tiny request (nova/models.py, warm_up), so a cold model loads now and not
        inside the prep budget: how long it took, and whether it failed."""
        warmed = await warm_up(self._model, timeout=timeout)
        self._events.emit(Event.WARMUP, ms=warmed.ms, error=warmed.error, classifier=False)
        return warmed

    async def prepare(self) -> None:
        """Writes every set piece within the prep budget. Different moments are written at
        the same time; the pieces of one moment (the two first questions, the three
        lightning questions, the two last words) are written in order, so each sees the ones
        chosen before it (tonight's memory includes them as soon as they're chosen). When
        the budget runs out, every piece not yet chosen gets its fallback."""
        started = time.monotonic()
        cues = self._set_piece_cues()
        chains: dict[Moment, list[Say]] = {}
        for say in cues:
            chains.setdefault(say.moment, []).append(say)
        try:
            async with asyncio.timeout(self._prep_budget):
                await asyncio.gather(*(self._prepare_chain(chain) for chain in chains.values()))
        except TimeoutError:
            self.budget_exhausted = True
        for say in cues:  # in running order, so fallbacks take turns as they would anyway
            if _key(say) not in self._prepared:
                self._keep(say, self._fallback(say, Attempt(None, (), 0, ErrorKind.TIMEOUT)))
        fallbacks = sum(line.source is Source.FALLBACK for line in self._prepared.values())
        self._events.emit(
            Event.PREPARED,
            count=len(self._prepared),
            fallbacks=fallbacks,
            ms=ms_since(started),
            budget_exhausted=self.budget_exhausted,
        )

    # --- During the date ------------------------------------------------------------------

    async def line_for(self, say: Say) -> Line:
        """The line for a cue. Tonight's memory hears of it only once it's played
        (played()), so a line cut by a stop or lost to the voice never steers later ones."""
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

    def played(self, say: Say, line: Line) -> None:
        """The room heard this line: a set piece stays in tonight's memory, and silences and
        room talk are remembered, so later prompts and fallbacks don't repeat them."""
        planned = self._planned.get(_key(say)) if say.text is None else None
        if planned is not None and say.moment in SET_PIECES:
            self._tonight.heard(planned)
        if line.text and line.source is not Source.FIXED and line.moment in REMEMBERED_LIVE:
            self._tonight.remember(line.moment, line.text, names=self._names)

    async def attempt_for(self, say: Say) -> Attempt:
        """One model attempt for a cue, with no fallback: for the evaluation script. An
        audience question Nova declines never reaches the model, as in line_for."""
        if self._declines(say):
            return Attempt(None, (), 0, ErrorKind.DECLINED)
        timeout = self._prep_timeout if say.moment in SET_PIECES else self._live_timeout
        return await self._attempt(say, self._prompt_for(say), timeout)

    async def _line_for(self, say: Say) -> Line:
        if say.moment not in HOLD_MOMENTS or (say.moment is Moment.ROOM_TALK and say.number == 1):
            self._fallback_in_hold = False  # the hold is over, or a new one starts
        if say.text is not None:
            return Line(say.text, Source.FIXED, say.moment)
        if say.moment in SET_PIECES:
            prepared = self._prepared.get(_key(say))
            if prepared is not None and say.away is not None:
                # Written before the date, for both daters; one is away now. Checked again
                # as for the one who's here, so it never names the other.
                found = self._check(say, prepared.text) if prepared.text else ()
                if found:
                    return self._fallback(say, Attempt(prepared.text, found, prepared.ms, None))
            if prepared is not None:
                return prepared
            return self._fallback(say, Attempt(None, (), 0, ErrorKind.NOT_PREPARED))
        if say.moment not in LIVE_REACTIONS:
            raise ValueError(f"No line for {say.moment}: it needs approved text")

        if self._declines(say):  # the runner skips these; if one gets here, say nothing
            return Line("", Source.FALLBACK, say.moment, error=ErrorKind.DECLINED)

        attempt = await self._attempt(say, self._prompt_for(say), self._live_timeout)
        return self._settle(say, attempt, Source.MODEL)

    def lightning_form(self, number: int) -> str:
        """The format this date's lightning question number asks for (prompts.LIGHTNING_FORMS)."""
        return self._lightning_forms[(number - 1) % len(self._lightning_forms)]

    # --- After the date -------------------------------------------------------------------

    def finish(self) -> None:
        """The date is over: the set pieces it never spoke (it ended early) leave tonight's
        memory, so lines the room never heard don't steer later dates' lines."""
        self._tonight.drop(self._planned.values())

    # --- Internals ------------------------------------------------------------------------

    async def _prepare_chain(self, chain: Sequence[Say]) -> None:
        """One moment's set pieces, in order."""
        for say in chain:
            self._keep(say, await self._prepare_one(say))

    def _keep(self, say: Say, line: Line) -> None:
        self._prepared[_key(say)] = line
        self._planned[_key(say)] = self._tonight.plan(say.moment, line.text, names=self._names)

    def _declines(self, say: Say) -> bool:
        """Audience questions the screen holds back (personal details, looks, instructions
        to Nova) never reach the model. The show runner skips them on air; Nova never
        declines one aloud, which would tell the room a listener asked something off
        limits."""
        return say.moment is Moment.FROM_THE_ROOM and question_is_off_limits(say.detail or "")

    async def _prepare_one(self, say: Say) -> Line:
        """Three candidates at once; keeps the one that passes the guardrails and is least
        like what Nova has said tonight for this moment. If none passes, the fallback."""
        attempts = await asyncio.gather(*(self._prepare_attempt(say) for _ in range(CANDIDATES)))
        passing = [attempt for attempt in attempts if attempt.ok]
        best = min(
            passing,
            key=lambda a: self._tonight.closeness(say.moment, a.text or "", names=self._names),
            default=attempts[0],
        )
        return self._settle(say, best, Source.PREPARED)

    async def _prepare_attempt(self, say: Say) -> Attempt:
        """One candidate, taking a slot if NOVA_PREP_PARALLEL limits them, so a distress
        check during prep never queues behind a dozen requests on the same model."""
        slot = self._prep_slots or contextlib.nullcontext()
        async with slot:
            return await self._attempt(say, self._prompt_for(say), self._prep_timeout)

    async def _attempt(self, say: Say, prompt: str, timeout: float) -> Attempt:
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                self._model.write(system=prompts.SYSTEM_PROMPT, prompt=prompt), timeout
            )
        except TimeoutError:
            return Attempt(None, (), ms_since(started), ErrorKind.TIMEOUT)
        except ModelRefused:
            return Attempt(None, (), ms_since(started), ErrorKind.REFUSED)
        except Exception:  # any model failure: the room hears a fallback, not an error
            return Attempt(None, (), ms_since(started), ErrorKind.ERROR)
        text = clean(raw)
        return Attempt(text, self._check(say, text), ms_since(started), None)

    def _check(self, say: Say, text: str) -> tuple[Violation, ...]:
        """The guardrails for this cue. Names and places on the blind cards of the daters
        it's about may be said; what a dater or listener said for it (a reply or an
        audience question) is passed in so no proper noun from it is repeated."""
        rules = rules_for(say.moment)
        said = [say.detail] if say.moment in (Moment.REPLY, Moment.FROM_THE_ROOM) else []
        away = _away(say)
        if away is not None:
            cards = [self._pair.card(away.other)]  # one dater is away: only the one who's here
        else:
            cards = cards_for(say.moment, self._pair, self._next_pair)
        found = check(
            text,
            names=frozenset({card.first_name for card in cards} | {"Nova"}),
            max_chars=rules.max_chars,
            max_sentences=rules.max_sentences,
            echo_from=[s for s in said if s],
            card_words=card_words(" ".join(p.text for p in card.prompts) for card in cards),
            questions=rules.questions,
            open_with_names=rules.open_with_names,
        )
        # The dater who's away is never named, even to open a sentence, where a capitalised
        # word passes as an ordinary one.
        if away is not None:
            absent = self._pair.card(away).first_name
            if re.search(rf"\b{re.escape(absent)}\b", text):
                found = tuple(v for v in Violation if v in {*found, Violation.UNKNOWN_NAME})
        return found

    def _settle(self, say: Say, attempt: Attempt, success: Source) -> Line:
        if attempt.ok and attempt.text is not None:
            return Line(attempt.text, success, say.moment, ms=attempt.ms)
        return self._fallback(say, attempt)

    def _fallback(self, say: Say, attempt: Attempt) -> Line:
        return Line(
            self._fallback_for(say),
            Source.FALLBACK,
            say.moment,
            violations=attempt.violations,
            error=attempt.error,
            ms=attempt.ms,
        )

    def _fallback_for(self, say: Say) -> str:
        """An approved fallback line, never built from what a dater or listener said (one
        exception is proposed, behind a switch that's off: an approved audience question,
        framed)."""
        pair = self._pair
        match say.moment:
            case Moment.DOORS_INTRO:
                if self._card_line(say, pair, script.doors_from_cards):
                    return script.doors_from_cards(pair)
                return script.doors_fallback(pair)
            case Moment.FIRST_QUESTION:
                return script.first_question_fallback(pair.card(_seat(say)))
            case Moment.LIGHTNING_QUESTION:
                options = script.LIGHTNING_FALLBACKS
                if say.number == say.of:  # the last-question fallbacks only ever come last
                    options = (*options, *script.LIGHTNING_LAST_FALLBACKS)
                return self._least_like_tonight(say, options)
            case Moment.LAST_WORD:
                return script.last_word_fallback(pair.card(_seat(say)))
            case Moment.OUTRO:
                return self._least_like_tonight(say, script.outro_fallbacks(pair))
            case Moment.NEXT_UP:
                upcoming = self._next_pair or pair
                if self._card_line(say, upcoming, script.next_up_from_cards):
                    return script.next_up_from_cards(upcoming)
                return script.next_up_fallback(upcoming)
            case Moment.SILENCE:
                if say.about is not None:
                    # One dater is away, and every silence fallback names both: say nothing
                    # (one-dater fallbacks wait for sign-off: docs/sign-off/2026-10-one-dater.md).
                    return ""
                return self._least_like_tonight(say, script.silence_fallbacks(pair))
            case Moment.FROM_THE_ROOM:
                # Proposed, off until sign-off: the approved question, framed, if that
                # passes the guardrails (docs/sign-off/2026-10-audience-questions.md). As
                # typed, quotes straightened (iOS) and line breaks made spaces.
                if script.READ_APPROVED_QUESTION_ON_FAILURE and say.detail:
                    framed = script.from_the_room_framed(plain(say.detail))
                    if not self._check(say, framed):
                        return framed
                if say.away is not None:
                    # The fallback names both daters: say nothing (one-dater lines wait for
                    # sign-off: docs/sign-off/2026-10-one-dater.md).
                    return ""
                return script.from_the_room_fallback(pair)
            case Moment.ROOM_TALK:
                ended = say.room is not None and say.room.situation is Situation.ENDED_EARLY
                if say.number is not None or ended:  # a hold's cover, or the close after one
                    if self._fallback_in_hold:
                        return ""  # never the same fallback twice in one hold
                    self._fallback_in_hold = True
                return script.ROOM_TALK_FALLBACK
            case Moment.REPLY:
                words = say.detail or ""
                if is_unmistakable(words) or has_warning(words):
                    return script.TAKE_YOUR_TIME  # never "I'll stay out of that one"
                if say.away is not None:
                    return ""  # the fallback hands back to both daters
                return script.REPLY_FALLBACK
        raise ValueError(f"No fallback for {say.moment}")

    def _card_line(self, say: Say, pair: Pair, build: Callable[[Pair], str]) -> bool:
        """Proposed, off until signed off (script.CARD_FALLBACKS_ON): a fallback quoting
        the daters' cards, only if a person approved both cards for air and the line passes
        the guardrails for its moment, card words allowed as for a model line."""
        if not script.CARD_FALLBACKS_ON:
            return False
        if not all(card.approved_for_air and _quotable(card) for card in (pair.left, pair.right)):
            return False
        return not self._check(say, build(pair))

    def _least_like_tonight(self, say: Say, options: Sequence[str]) -> str:
        """The option said least often tonight for this moment, then the one said longest
        ago, then the one least like anything said, so fixed fallbacks take turns across the
        night even once every option has been used; ties go to the first."""
        tonight, names = self._tonight, self._names

        def turn(option: str) -> tuple[int, int, float, int]:
            count, last = tonight.uses(say.moment, option, names=names)
            close = tonight.closeness(say.moment, option, names=names)
            return count, last, close, options.index(option)

        return min(options, key=turn)

    def _prompt_for(self, say: Say) -> str:
        limit = SHOWN_LIVE if say.moment in LIVE_REACTIONS else SHOWN
        pair, tonight = self._pair, self._tonight.said(say.moment, limit=limit)
        match say.moment:
            case Moment.DOORS_INTRO:
                return prompts.doors_intro(pair, tonight=tonight)
            case Moment.FIRST_QUESTION:
                return prompts.first_question(pair, _seat(say), tonight=tonight)
            case Moment.LIGHTNING_QUESTION:
                n, total = _place(say)
                form = self.lightning_form(n)
                return prompts.lightning_question(pair, n, total=total, form=form, tonight=tonight)
            case Moment.LAST_WORD:
                return prompts.last_word(pair, _seat(say), tonight=tonight)
            case Moment.OUTRO:
                return prompts.outro(pair, tonight=tonight)
            case Moment.NEXT_UP:
                shape = self._rng.choice(prompts.NEXT_UP_SHAPES)
                return prompts.next_up(self._next_pair or pair, shape=shape, tonight=tonight)
            case Moment.SILENCE:
                if say.about is not None:
                    return prompts.silence_to_one(pair, say.about, tonight=tonight)
                return prompts.silence(pair, tonight=tonight)
            case Moment.FROM_THE_ROOM:
                to = say.away.other if say.away is not None else None
                return prompts.from_the_room(pair, say.detail or "", to=to, tonight=tonight)
            case Moment.ROOM_TALK:
                opening = self._rng.choice(prompts.ROOM_TALK_OPENINGS)
                return prompts.room_talk(_room(say), opening=opening, tonight=tonight)
            case Moment.REPLY:
                away = say.away is not None
                words = say.detail or ""
                return prompts.reply(pair, _seat(say), words, other_away=away, tonight=tonight)
        raise ValueError(f"No prompt for {say.moment}")

    def _set_piece_cues(self) -> list[Say]:
        return running_order.set_pieces(self._pair, self._next_pair)


type _Key = tuple[Moment, Seat | None, int | None]


def _key(say: Say) -> _Key:
    if say.moment is Moment.LIGHTNING_QUESTION:
        return (say.moment, None, _place(say)[0])
    if say.moment in (Moment.FIRST_QUESTION, Moment.LAST_WORD):
        return (say.moment, say.about, None)
    return (say.moment, None, None)


def _away(say: Say) -> Seat | None:
    """The dater a line must not name: the one away (the moderator resumed without them)."""
    if say.away is not None:
        return say.away
    if say.moment is Moment.SILENCE and say.about is not None:
        return say.about.other  # a silence prompt for the one who's here
    return None


def _quotable(card: BlindCard) -> bool:
    """A card answer Nova may quote in its own voice: one sentence, since on audio a second
    would sound like Nova's own words, and never naming Nova or the show."""
    answer = plain(card.prompts[0].answer).rstrip(".?!")
    host = rf"\b(?:Nova|{SHOW_NAME})\b"
    return not (SENTENCE_END.search(answer) or re.search(host, answer, re.IGNORECASE))


def _seat(say: Say) -> Seat:
    if say.about is None:
        raise ValueError(f"{say.moment} needs a seat")
    return say.about


def _room(say: Say) -> RoomFacts:
    if say.room is None:
        raise ValueError(f"{say.moment} needs room facts")
    return say.room


def _place(say: Say) -> tuple[int, int]:
    """A question's place in its round: (number, of)."""
    if say.number is None or say.of is None:
        raise ValueError(f"{say.moment} needs its place in the round")
    return say.number, say.of
