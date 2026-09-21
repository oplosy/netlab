# Agent Orchestration Protocol

## Roles

### Orchestrator

Recommended runtime: `gpt-5.6-sol` with medium reasoning.

The orchestrator owns planning, dependency resolution, task dispatch, review,
integration, and the phase pull request. It does not perform worker tasks merely
to save delegation overhead. It may make small integration-only edits when they
do not change task scope.

The orchestrator must:

1. read the accepted ADRs and current integration-branch state
2. select only tasks whose dependencies are complete
3. reject parallel tasks with overlapping `allowed_paths`
4. create one branch and external worktree per task
5. give the worker the exact task object plus required context files
6. inspect the diff and rerun the task's verification
7. integrate only complete, scoped, and evidence-backed commits
8. update task status after integration, not after a worker merely reports done
9. open one reviewed phase PR to `main`

### Worker

Recommended runtime: `gpt-5.6-luna` with high reasoning.

A worker receives one task. It may inspect the repository and its task
dependencies but may write only to `allowed_paths`. It must stop and report a
contract conflict instead of changing an ADR or unrelated area.

The model mapping is a recommendation, not a protocol dependency. Another AI
system can act as orchestrator or worker if it follows the same contracts.

## Branch and worktree layout

For a phase named `phase-1`:

```text
main
└── integration/phase-1
    ├── feat/l2-110-site-switching
    ├── feat/ospf-130-multi-area
    └── test/test-190-phase-1-evidence
```

Use worktrees outside the repository:

```text
C:\Users\mesut\Desktop\workspace\A-projects\netlab
C:\Users\mesut\Desktop\workspace\A-projects\netlab-worktrees\l2-110
C:\Users\mesut\Desktop\workspace\A-projects\netlab-worktrees\ospf-130
```

The primary checkout remains on `main` after bootstrap. The orchestrator may use
one dedicated integration worktree. Workers never share a worktree.

## Git procedure

The orchestrator uses this sequence after `origin/main` exists:

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git branch integration/phase-1 origin/main
git worktree add ../netlab-worktrees/phase-1 integration/phase-1
git -C ../netlab-worktrees/phase-1 switch integration/phase-1
```

For a worker task:

```bash
git -C ../netlab-worktrees/phase-1 branch feat/l2-110-site-switching
git worktree add ../netlab-worktrees/l2-110 feat/l2-110-site-switching
```

The worker commits only on its task branch. The orchestrator reviews the commit,
reruns verification in the task worktree, and merges it into the integration
branch with a non-fast-forward merge so the task boundary remains visible.
The phase pull request also uses a merge commit rather than squash merge, so
accepted task commits remain visible on `main`.

Do not execute these examples blindly. Resolve the current absolute paths and
verify the target branch and worktree before any create, move, or removal
operation.

## Concurrency

Use at most three workers concurrently when the orchestrator is active. Parallel
tasks must satisfy all of these conditions:

- all dependencies are integrated
- their `allowed_paths` do not overlap
- they do not mutate the same runtime environment
- neither task produces an input consumed by the other

Runtime lab tests are serialized because they share Docker, Linux networking,
and fixed address space.

## Dispatch packet

Every worker prompt must contain:

```text
Role: worker
Task: <task ID and title>
Base: <integration branch and commit SHA>
Worktree: <absolute path>
Read first: AGENTS.md and listed context files
Allowed paths: <exact globs>
Deliverables: <from task manifest>
Acceptance criteria: <from task manifest>
Verification: <exact commands or explicit discovery task>
Commit: <required commit subject>
Stop conditions: ADR conflict, missing dependency, secret exposure, or required
write outside allowed paths
```

Do not send a worker a vague request such as "implement networking." One packet
must be finishable and reviewable as one unit.

## Worker completion contract

A worker is complete only after it returns:

```text
Outcome:
Files changed:
Commands run and results:
Evidence produced:
Commit and branch:
Risks or follow-ups:
```

The orchestrator independently checks:

- the commit exists on the expected branch
- the diff is confined to allowed paths
- no secrets or generated runtime state were committed
- acceptance and negative tests ran successfully
- the worktree is clean

## Integration gates

### Task gate

- task dependencies are integrated
- diff is in scope
- automated checks pass
- required evidence exists
- documentation matches actual commands

### Phase gate

- every required task is integrated
- clean rebuild succeeds
- teardown succeeds
- cross-feature acceptance suite passes
- current limitations are documented
- integration branch is pushed and a PR targets `main`
- CI and review state are reported accurately
- merged task branches and worktrees are removed only after the merge commit and
  clean state are verified

## Empty-repository bootstrap

The remote currently has no commit on `main`, so GitHub cannot accept a normal
feature-to-main pull request. The no-direct-push rule remains in force.

Before implementation begins, the repository owner must establish the first
`main` commit through an explicitly approved bootstrap action. The preferred
method is for the owner to create the initial protected `main` commit in GitHub,
then the orchestrator fetches it and rebases this architecture branch. A
one-time direct bootstrap push is not implied by this plan and requires explicit
owner authorization if chosen.
