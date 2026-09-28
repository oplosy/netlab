# Contributor Contract

These rules apply to every AI or human contributor in this repository.
The delivery workflow is described in `docs/plans/workflow.md` (ADR 0016).

## Architecture

1. Read `docs/architecture/overview.md` and `docs/adr/README.md` before
   changing files.
2. Accepted ADRs are constraints. Changing one requires a superseding ADR in
   its own commit; do not mix an architecture change into implementation.
3. Implement only the current task from `plans/tasks.yaml`. Do not add
   adjacent features.

## Git

1. Never commit or push directly to `main`.
2. Work on `phase/<n>-<slug>` created from `main`. One phase, one branch, one
   pull request, merged before the next phase starts.
3. Commit messages: `<type>(<task-id>): <summary>`, one task concern per commit.

## Quality

1. Pin versions; no floating container tags. Record resolved image digests in
   the version lock file.
2. Never commit credentials, private keys, generated certificates, packet
   captures containing secrets, or local `.env` files.
3. Apply commands are idempotent: a second run causes no material change.
4. Every behavior change ships with automated verification or an evidence
   target. Security changes include negative tests that prove denied traffic
   is denied.
5. Generated files identify their source and are not edited by hand.
6. Report only verification that actually ran.
