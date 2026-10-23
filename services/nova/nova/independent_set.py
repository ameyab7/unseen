"""The independent held-out set (decision 0005): distress-check sentences written by someone
other than the developer, who has never seen the check's rules or its dev set, so that it
measures the check rather than how well the developer guessed its weaknesses.

Its contents never enter the repository: the file lives at services/nova/independent/
(git-ignored except its README, which tells the author what to write) or at a path given to
`uv run python -m nova.evaluation --independent PATH`. Nothing reads it but that run, which
prints counts only, never a sentence, and first checks that no sample paraphrases anything
the check was built or tuned on (separation). Errors and the separation report name a
sample's position in the file, never its words.

The file is JSON:

    {"author": "...", "written": "YYYY-MM-DD", "samples": [
        {"said": "what the dater says", "expected": "pause"},
        {"said": ["first final", "second final"], "expected": "nothing"},
        {"said": "...", "other": "what the other dater said first", "expected": "alert"}
    ]}

expected is what decision 0005 says the show does: pause (the date pauses and the moderator
is alerted), alert (the moderator is alerted, no pause: a refusal or calm boundary) or
nothing (ordinary talk). "said" as a list is the sentence as speech-to-text splits it at a
pause; "earlier" is what the same dater said just before, if anything.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from nova import conduct_set
from nova.distress import CHECK_SYSTEM
from nova.distress_dev import DEV_SET
from nova.distress_set import DISTRESS_SET, Sample
from nova.tonight import similarity

DEFAULT_PATH = Path(__file__).parents[1] / "independent" / "distress.json"
# Word similarity at or above which a sample paraphrases something the check was built or
# tuned on, as the guards on the dev set measure it (tests/test_distress.py).
NEAR = 0.6
# What each expected outcome is as a sample: (distress, boundary). An alert is scored as a
# refusal is: alerted is as hoped, paused is worse, missed is worst.
EXPECTED = {"pause": (True, False), "alert": (True, True), "nothing": (False, False)}


class IndependentSetError(ValueError):
    """The file can't be used. The message names a position and a field, never words."""


@dataclass(frozen=True)
class TooClose:
    """A sample that paraphrases something the check was built or tuned on: its position
    in the file (from 1) and its closest score."""

    position: int
    score: float


def fingerprint(path: Path) -> str:
    """A short hash of the file as run, so a report says which version it measured."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def load(path: Path) -> tuple[Sample, ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise IndependentSetError(f"{path.name} isn't readable JSON") from error
    if not isinstance(data, dict) or not data.get("author") or not data.get("written"):
        raise IndependentSetError("the file needs an author and a written date")
    raw = data.get("samples")
    if not isinstance(raw, list) or not raw:
        raise IndependentSetError("the file has no samples")
    return tuple(_sample(k, item) for k, item in enumerate(raw, start=1))


def _sample(k: int, item: object) -> Sample:
    if not isinstance(item, dict):
        raise IndependentSetError(f"sample {k} isn't an object")
    expected = item.get("expected")
    if expected not in EXPECTED:
        raise IndependentSetError(f"sample {k}: expected must be pause, alert or nothing")
    said = item.get("said")
    finals = (said,) if isinstance(said, str) else tuple(said) if isinstance(said, list) else ()
    if not 1 <= len(finals) <= 2 or not all(isinstance(f, str) and f.strip() for f in finals):
        raise IndependentSetError(f"sample {k}: said must be one sentence or two finals")
    lines = {}
    for field in ("other", "earlier"):
        value = item.get(field, "")
        if not isinstance(value, str):
            raise IndependentSetError(f"sample {k}: {field} must be a sentence")
        lines[field] = (value,) if value.strip() else ()
    distress, boundary = EXPECTED[expected]
    return Sample(
        " ".join(finals),
        distress,
        earlier=lines["earlier"],
        split=finals if len(finals) == 2 else (),
        other=lines["other"],
        boundary=boundary,
    )


def known_lines() -> list[str]:
    """Everything the check was built or tuned on, or measured with before: its rules'
    examples, the distress dev set and the first held-out set, and both conduct halves,
    with every line their samples answer or follow."""
    examples = re.findall(r'"([^"]+)"', CHECK_SYSTEM.split("Examples")[1].split("Also label")[0])
    lines = set(examples)
    for sample in (*DEV_SET, *DISTRESS_SET):
        lines.update((sample.sentence, *sample.earlier, *sample.other))
    for conduct in (*conduct_set.DEV_HALF, *conduct_set.HELD_OUT_HALF):
        lines.update((conduct.sentence, *conduct.other, *conduct.earlier))
    return sorted(line for line in lines if line.strip())


def separation(samples: tuple[Sample, ...]) -> list[TooClose]:
    """The samples whose words, or the lines they answer or follow, paraphrase a known line
    (NEAR or more): an empty list means the set is separate."""
    known = known_lines()
    close = []
    for k, sample in enumerate(samples, start=1):
        mine = (sample.sentence, *sample.earlier, *sample.other)
        score = max(similarity(line, other) for line in mine for other in known)
        if score >= NEAR:
            close.append(TooClose(k, round(score, 2)))
    return close
