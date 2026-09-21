# Agent Operating Contract

These rules apply to every AI or human contributor in this repository.

## Authority and architecture

1. Read `docs/architecture/overview.md`, `docs/adr/README.md`, and the assigned
   task packet before changing files.
2. Accepted ADRs are constraints. Do not silently replace a technology,
   addressing block, protocol boundary, or security policy.
3. If an accepted decision must change, stop implementation and create a
   superseding ADR. Do not mix an architecture change into an implementation
   task.
4. Implement only the assigned task. Do not add adjacent features.

## Git isolation

1. Never commit or push directly to `main`.
2. One task equals one branch and one worktree.
3. Create task branches from the phase integration branch, not from another
   worker branch.
4. The orchestrator is the only writer to the phase integration branch.
5. Do not merge, rebase, reset, or delete another worker's branch or worktree.
6. Use atomic commits with `<type>(<task-id>): <summary>` messages.
7. Before handoff, report the commit SHA, upstream branch, verification results,
   and `git status --short` output.

## Implementation quality

1. Do not use floating container tags. Pin versions and record resolved image
   digests in the version lock file created by the environment task.
2. Never commit credentials, private keys, generated certificates, packet
   captures containing secrets, or local `.env` files.
3. Configuration must be idempotent. Running the documented apply command twice
   must not cause a second material change.
4. Every behavior change requires an automated verification or an executable
   evidence procedure in the same task.
5. Negative security tests are mandatory: prove denied traffic is denied, not
   only that allowed traffic works.
6. Generated files must identify their source. Do not hand-edit generated
   artifacts.

## Worker handoff

Return exactly these sections to the orchestrator:

1. `Outcome`
2. `Files changed`
3. `Commands run and results`
4. `Evidence produced`
5. `Commit and branch`
6. `Risks or follow-ups`

Do not claim live verification when a command was not run successfully.
