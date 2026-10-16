# Unseen

Live, audio-only dating show for iOS and Android. Two verified strangers talk on air,
an AI host called Nova runs the show, a city audience listens, votes and sends gestures.
Photos unlock only if both daters choose reveal.

Expo app in apps/mobile, moderator console in apps/console, Supabase backend in
supabase/, AI host agent in services/nova (Python). Built so far: the pnpm monorepo,
the design tokens (packages/tokens), the Expo app's shell (three placeholder tabs and
routing), and Nova: its show runner, lines and guardrails, distress and conduct checks,
a measured rehearsal in LiveKit's Agents Playground and an evaluation against the real
model (services/nova/CLAUDE.md). Not built yet: the moderator console, Supabase, and
the app's real screens; the rest of the week 1-2 foundations is in
docs/technical-plan.pdf.

## Where the design lives (read before any UI work)
- docs/design-system/README.md: brand book. Color rules, type, motion, voice and
  copy, accessibility, the show's running order, Nova's on-air voice.
- packages/tokens/tokens.json: the only source of colors, type, spacing and radii.
- docs/design-system/components/<Name>/README.md: the spec for each design-system
  component. preview.html shows it rendered.
- docs/prototype/unseen-prototype.html: every screen and flow. Each screen is a
  <section class="scr"> with an id (welcome, dob, city, remind, tonight, live,
  getseat, verify, card, backstage, onair, decide, reveal, nomatch, recap, dates,
  chat). Sheets use data-sheet. Read the matching section and its CSS before
  building a screen.
- docs/design-system/prototype-only-components.md: components that exist only in
  the prototype. For these, the prototype's markup and CSS are the spec.
- docs/technical-plan.pdf (text copy in technical-plan.txt): stack, data model,
  API, roadmap.

When the design system and the prototype disagree, the design system wins. When
neither covers something (for example the You tab), build it from existing
components and say so in the pull request.

## Commands
- pnpm dev: Expo dev client
- pnpm typecheck, pnpm lint, pnpm test
- pnpm tokens: regenerate apps/mobile/src/theme/tokens.ts from tokens.json;
  pnpm tokens:check: fail if it is out of date
- pnpm check:env: fail if a .env file is committed or an .env.example holds a value
- supabase start: local stack; pnpm db:test: pgTAP tests; pnpm db:types: regenerate types
- services/nova (Python 3.14, uv): uv sync; uv run pytest; uv run ruff check;
  uv run ruff format. Settings come from services/nova/.env (see .env.example)
- services/nova: uv run python -m nova.evaluation [--repeat N] runs Nova's show moments,
  attacks and the distress test set against the real model in NOVA_LLM; --distress runs
  only the distress set; --night runs a full night of eight dates and reports repetition
  across it (the number we track). On demand only, never in CI; run it before any model
  change (docs/decisions/0005-nova-lines-and-guardrails.md)
- services/nova: uv run python -m nova.agent dev runs Nova in LiveKit for one measured
  rehearsal date in the Agents Playground; uv run python -m nova.voices writes voice
  samples. Both use LiveKit credit (docs/spike-results.md)

Install app packages from apps/mobile with
`node ../../node_modules/expo/bin/cli install <pkg>`, not `npx expo install`, then
check package.json put them in the right section (docs/decisions/0001-pnpm-monorepo.md).

On Windows, Smart App Control (Windows 11's reputation check for apps and DLLs) is on. It
blocks unsigned files that few machines run, and re-checks each file about once a day, so a
tool can work one day and be blocked the next (the Code Integrity event log names what it
blocks). It currently blocks PyAV's DLLs, so nothing that imports LiveKit runs natively
(Nova's agent and voice samples, tests/test_agent.py, tests/test_voices.py), and pnpm 12's
native binary. Never turn it off or change other security settings; use these instead:
- Nova: with Docker Desktop running, from the repository root, this runs a rehearsal
  (LiveKit credit), and the second runs every test (compose.yaml lists more):
  `docker compose -f services/nova/compose.yaml up --build`
  `docker compose -f services/nova/compose.yaml run --rm --build nova uv run pytest`
  Ollama on Windows is reached at host.docker.internal; the keys stay in
  services/nova/.env and never go in the image.
  Natively, the tests that don't import LiveKit still run:
  `uv run pytest --ignore=tests/test_agent.py --ignore=tests/test_voices.py`.
- The app: `node node_modules/expo/bin/cli start apps/mobile --dev-client` from the
  repository root does what `pnpm dev` does, without pnpm. Run pnpm itself (installs, root
  scripts) in WSL or a container while it is blocked.

If `pnpm` is not recognized at all, run once
`corepack enable --install-directory "$env:APPDATA\npm" pnpm` (no admin needed; that
folder is on the user PATH), then open a new terminal. Don't use `corepack pnpm <script>`:
the root scripts call pnpm themselves and fail when it is not on PATH.

## Architecture rules
- A write that touches more than one row is a Postgres function in a migration,
  never several client calls.
- Every table has RLS enabled and a pgTAP test in supabase/tests proving who can
  and cannot read it.
- Room events and API payloads are zod schemas in packages/shared. Import them;
  never redeclare.
- Screens in app/ stay thin. Data hooks live in src/features/<feature>/api.ts,
  visuals in src/ui.

## UI rules
- Only tokens from src/theme (generated from tokens.json): no hex values, raw font
  sizes or new colors.
- Component names match the design system. Read the component's README before
  building it.
- One lamplight-filled primary button per screen. On consent screens (Reveal or
  Keep it unseen, clip approval) both options look identical.
- Bodoni Moda only at 24px and up; platform font everywhere else.
- Every touchable has an accessibilityLabel and a 44 pt target, and supports
  Dynamic Type and Reduce Motion. Screen-reader labels carry state
  ("Maya, 29, she/her, speaking").
- Copy is sentence case, calm, no exclamation marks. Use the brand book's wording
  table. Nova is labelled "Nova (AI)" the first time it appears on a screen.

## Privacy rules (never break)
- Never log or send to analytics: transcripts, chat text, photos, ID data.
- No user can read another user's decision. Only resolve_date computes outcomes.
- Reveal photos are served only through signed URLs after a mutual match.
- No code, test or script calls production.
- Keys live only in untracked .env files, next to the .env.example that names them.
  Never put a secret in an EXPO_PUBLIC_ variable: those are built into the app.

## Workflow
- For changes touching more than 3 files, propose a plan and wait for approval.
- Write or update tests in the same change. Run typecheck, lint and tests before
  calling a task done.
- Ask before adding a dependency. Never edit generated files (tokens.ts,
  database.types.ts).
- Record architecture decisions in docs/decisions/, one short file each.
