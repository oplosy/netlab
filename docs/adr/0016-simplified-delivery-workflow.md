# ADR 0016: Deliver Each Phase Through One Branch and One Pull Request

- Status: Accepted
- Date: 2026-09-28
- Supersedes: ADR 0012; the delivery and agent-orchestration part of ADR 0010
- Related: ADR 0010, ADR 0011

## Context

Phases 0 to 3 were delivered with an orchestrator/worker model: one branch and
one worktree per task, a phase integration branch, per-task `allowed_paths`,
and a recommended model per role.

In practice this cost more than it protected:

- `allowed_paths` was never machine-enforced; about fifteen commits only
  widened a task's path list so the next change could proceed.
- Runtime tests share one lab, so they were serialized anyway and parallel
  workers gave little speed-up.
- Phases 1 to 3 accumulated on one integration branch for days without
  reaching `main`, and about forty task branches and thirty worktrees built up.
- Model names are an execution preference, not an architecture decision.

## Decision

- Each phase is delivered on one branch, `phase/<n>-<slug>`, created from
  `main`, and reaches `main` through one pull request with a merge commit.
- A phase pull request is merged before the next phase starts.
- Tasks are separated by commits (`<type>(<task-id>): <summary>`), not by
  branches. A task is complete when its `verify` commands pass on the phase
  branch.
- Extra worktrees are optional and only used for genuinely parallel,
  non-runtime work. They are removed when their branch is merged.
- Scope and safety are enforced by CI on the pull request, not by per-task
  path lists. `plans/tasks.yaml` keeps only identity, status, dependencies,
  acceptance criteria, and verification commands.
- No model or agent-role mapping is recorded in the repository.
- Phase 5 (network automation) is a stretch goal. The project is complete when
  Phase 4 is merged and the CI quality gate from `CI-050` passes on `main`.

The rest of ADR 0010 is unchanged: Git-managed YAML remains the canonical
intent, and NetBox, if built, is a rebuildable projection.

## Consequences

- Finished work reaches `main` at every phase boundary.
- Git history contains task commits and one merge per phase, with no
  scope-authorization commits.
- Accidental out-of-scope edits are caught by review and CI instead of by an
  agent-maintained path list.
- The mismatch between ADR 0010 ("Ansible renders and applies") and the
  Python apply scripts that exist today is not resolved here. It must be
  settled by a separate ADR before any Phase 5 work starts.
