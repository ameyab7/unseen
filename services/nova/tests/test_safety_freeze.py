"""The safety check is frozen (decision 0005, 2026-10-23): its main rules, schema, prompt,
instant-layer patterns and label parsing are byte-identical to the version measured before
the second question was added (2026-10-22), and the second question is as approved.

Changing either is a model change: it needs a new evaluation baseline, measured with the
frozen safety report (`uv run python -m nova.evaluation --safety`), and the product
owner's approval for any policy in it. Update the digest here only with that."""

import hashlib
import json

from nova import distress

# Measured equal to the pre-second-question version at 5dbf276 on 2026-10-23.
MAIN = "d5b9672a5103d52385c89b084cff442cdb7a16991dacb11d1ff365252ba16217"
SECOND_QUESTION = "d255c4a7c603bba4af67fb260719bae93ae8b5982150e335b9bb9e371f5ef24d"


def digest(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def test_the_main_check_is_byte_identical_to_the_frozen_version():
    d = distress
    now = digest(
        d.CHECK_SYSTEM,
        json.dumps(d.LABEL_SCHEMA, sort_keys=True),
        d.check_prompt("S", "E", "O"),
        d.INSTANT.pattern,
        d.FILLER.pattern,
        d.LABEL.pattern,
        str((d.JOINED, d.CONTEXT, d.MORE_WORDS)),
        d.FINAL_PUNCTUATION,
    )
    assert now == MAIN, "the main safety check changed: see this file's docstring"


def test_the_second_question_is_the_approved_one():
    d = distress
    now = digest(
        d.LIGHT_SYSTEM, json.dumps(d.LIGHT_SCHEMA, sort_keys=True), d.light_prompt("S", "E", "O")
    )
    assert now == SECOND_QUESTION, "the second question changed: see this file's docstring"
