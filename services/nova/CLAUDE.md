# Nova

The AI host: a deterministic show runner walks each date's running order, and a language
model only writes the lines inside its beats. Python 3.14, uv.

## Where Nova's decisions live

docs/decisions/0004 onwards: 0004 the show runner (running order, pauses, holds, the
result), 0005 lines, guardrails, fallbacks and the distress check, 0006 dater conduct, 0007
the listener delay (proposed). Read the one a change touches first, and update it in the
same change. Lines waiting for the product owner are in docs/sign-off/ and
nova/sign_off.py.

## Rules

- Every line the model writes passes the guardrails (nova/guardrails.py) or an approved
  fallback is said instead. A new on-air line, fixed or fallback, needs the product
  owner's sign-off: draft it in nova/sign_off.py and docs/sign-off/, never straight into
  nova/script.py.
- No text in logs: nova/events.py accepts codes, numbers and flags only. Never log what a
  dater, listener or Nova said.
- Before any model change (a prompt, the model, the guardrails it's measured against), run
  `uv run python -m nova.evaluation` against local Gemma, before and after, and put both
  summaries in the pull request. `--night` is the number we track and a gate;
  `--distress` and `--conduct` report dev and held-out sets apart: tune on dev, run
  held-out once.
- Nothing here calls production, and the tests never call LiveKit, Ollama or Claude.

## Running it on this machine

- `uv sync`, then `uv run ruff check`, `uv run ruff format` and the tests.
- Smart App Control blocks PyAV, so the tests that import LiveKit (tests/test_agent.py,
  tests/test_voices.py) run only in Docker:
  `docker compose -f services/nova/compose.yaml run --rm --build nova uv run pytest`
  (from the repository root). Natively:
  `uv run python -m pytest --ignore=tests/test_agent.py --ignore=tests/test_voices.py`
  (`python -m pytest`, since Smart App Control may also block the pytest launcher).
- The evaluation and rehearsals need services/nova/.env (see .env.example). A rehearsal
  and voice samples cost LiveKit credit; the evaluation with Ollama costs nothing.
