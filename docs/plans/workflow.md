# Delivery Workflow

Decision record: [ADR 0016](../adr/0016-simplified-delivery-workflow.md).

## Phase cycle

```bash
git switch main
git pull --ff-only origin main
git switch -c phase/4-secure-edge
```

1. Work through the phase's tasks in `plans/tasks.yaml` in dependency order.
2. Commit each task as `<type>(<task-id>): <summary>` after its `verify`
   commands pass. Set the task's `status` to `complete` in the same commit.
3. Run the phase evidence target (for example `make evidence-phase-4`) and
   commit the curated report under `evidence/reports/`.
4. Push the branch and open one pull request to `main`. Merge it with a merge
   commit once the `policy` check passes, then delete the branch.
5. Start the next phase only from the updated `main`.

## Worktrees

The main checkout is the default place to work. Create an extra worktree only
for parallel work that does not touch the running lab, and remove it as soon as
its branch is merged. Runtime lab tests always run one at a time.

## Definition of done

The project is complete when Phase 4 is merged and the `CI-050` quality gate
passes on `main`. Phase 5 tasks are stretch goals.
