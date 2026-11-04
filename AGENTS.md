# Unseen: parallel agent ownership

## Read first and verify the workspace

Read the root CLAUDE.md and the applicable existing .claude/rules/ before
implementation. Their design, testing, dependency, safety and privacy requirements
remain in force. Before backend work, also read services/nova/CLAUDE.md and the
relevant decision and privacy documents.

The environment setup baseline is f8522d0df1bb94b3199a015c199c0d42107495ba.
The original unseen checkout on main is reserved for human review and integration.
Work only in your assigned worktree; do not switch its branch yourself.

| Agent | Worktree directory | Required branch | Owned application paths |
| --- | --- | --- | --- |
| OpenAI Codex | unseen-frontend | agent/frontend | apps/mobile/**, packages/tokens/** |
| Claude Code | unseen-backend | agent/backend | services/nova/** |

At the start of a task, check git rev-parse --show-toplevel, git branch
--show-current, git status --short --branch, and git worktree list. If your agent,
worktree or branch does not match this table, stop and report it; do not repair the
mismatch by editing another checkout or switching branches. AGENTS.md and CLAUDE.md
contain the approved environment-setup instructions; preserve them. Stop if
unexpected changes overlap the task, and report other unexpected changes without
overwriting them.

## Ownership

- Codex implements frontend code and tests in apps/mobile/** and
  packages/tokens/**. Frontend documentation includes docs/design-system/** and
  docs/prototype/**. This does not authorize a moderator-console implementation.
- Claude Code implements backend code, tests and documentation in
  services/nova/**, plus backend-specific rehearsal and sign-off documentation in
  docs/rehearsals/** and docs/sign-off/**. Draft sign-off documentation never
  constitutes product-owner approval.
- Reading the other area for context is allowed. Editing the other agent's code,
  tests, working tree, staging area or local environment requires explicit user
  coordination and approval. Do not use the original checkout's dependencies,
  virtual environment or secret files as a shortcut.
- All other paths are shared or unassigned unless the user explicitly assigns
  them. In particular, packages/shared/**, root configuration and lockfiles,
  .github/**, scripts/**, AGENTS.md, CLAUDE.md, .claude/**,
  docs/decisions/**, docs/privacy/**, docs/reviews/**, docs/spike-results.md and
  the technical plan require explicit coordination and user approval before edits.
- A shared-file proposal must name the exact files, contract or configuration
  changes, effects on both agents, and validation. Wait for explicit approval.
  This setup does not grant standing approval for future shared-file changes.
- Root instructions are identical in both worktrees. Do not silently update the
  other worktree's copy after changing your own.

## Shared protocol

packages/shared/src/moderator.ts defines the Zod MuteCommand and NovaState wire
schemas. services/nova/nova/moderator.py validates the same shapes in Python.
Both use packages/shared/fixtures/moderator.json. Preserve the
unseen.moderator topic, moderator role checks, session and sequence handling,
acknowledgements and destination restrictions.

A protocol change is coordinated work even if one affected file is inside your
owned directory. Obtain approval before changing the wire contract, then update
the schemas, Python validation, fixtures and both test suites together through
the approved integration process. Do not invent competing schemas.
Docker-only Nova tests omit shared-package parity checks; validate contracts from
a full repository checkout as well.

## Git and concurrent work

- Keep changes uncommitted for review unless the user explicitly approves the
  particular Git operation. Do not commit, amend, merge, cherry-pick, rebase,
  reset, pull, push, or stage the other agent's work without approval.
- Do not create, delete, rename or move branches, worktrees, backup refs or
  historical recovery objects without approval. Do not run Git cleanup, prune,
  gc, stash drop/clear, or destructive restoration commands.
- Fetching changes remote-tracking refs: obtain approval before fetching.
- Linked worktrees isolate files and indexes, but share Git objects and refs.
  Do not modify the other checkout or manipulate shared refs as a workaround.
- Keep uncommitted instruction edits visible in status; never hide them with
  skip-worktree, assume-unchanged, ignore changes, or unapproved automatic commits.
- Do not reorganize application folders. Ask before dependency additions,
  installation, linking dependencies, or environment provisioning.

## Safety, privacy and runtime approval

- No code, script or test may call production. Never change security settings.
- Do not expose secrets or environment values. Do not read or copy secret .env
  files; exact-path .env.example files may be read for names only. Never put a
  secret in an EXPO_PUBLIC_ variable. Provision each environment only when
  explicitly authorized, without copying secrets into Git or agent output.
- Never log transcripts, chat text, photos or ID data. Reveal decisions remain
  private and photos require signed URLs after a mutual match.
- Every Nova model request carrying real-person data must retain the existing
  privacy policy. Preserve guardrails, approved fallbacks, code-only event logs,
  moderator authorization and mute behavior, and per-date context cleanup.
- Keep the frozen safety evaluation and held-out/independent-set protections.
  Never open services/nova/independent/distress.json or tune on held-out results.
  Local unit-test success is not evidence of passing model safety or human
  acceptance.
- New on-air fixed or fallback lines require product-owner sign-off through the
  existing draft process; never treat agent approval as product approval.
- Obtain explicit user approval before any LiveKit session, paid speech/voice
  request, hosted or external model API call, runtime recording, or deployment.
  A documented command or installed credential does not grant approval.
- Model changes still require the existing before/after evaluation against local
  Gemma. Keep model evaluations and benchmarks on demand; obtain approval before
  running them, and request shared-document approval for required decision or
  privacy updates. Do not bypass an acceptance gate when approval is pending.

## Validation and task scope

Use existing offline tests, typechecks and non-fixing lint checks appropriate to
the approved change. Do not install dependencies or contact runtime providers to
make a check pass without approval. Report missing tooling and skipped checks.
Regenerate tokens only through packages/tokens; never hand-edit generated
tokens.ts or future database.types.ts.

Preserve the existing requirement to propose changes touching more than three
files and await approval. Keep routes thin, UI token-based and accessible, and
read the applicable design component specification before frontend changes.

Environment setup authorizes instruction files and isolated worktrees only.
Wait for a separate feature task before implementing application changes.
