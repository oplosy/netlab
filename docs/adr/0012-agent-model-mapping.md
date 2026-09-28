# ADR 0012: Update the Recommended Agent Model Mapping

- Status: Superseded by ADR 0016
- Date: 2026-09-23
- Supersedes: The model recommendation in ADR 0010 only

## Decision

Use `gpt-6-sol` with medium reasoning for the orchestrator and `gpt-6-luna`
with high reasoning for task workers. Keep the role boundaries, task packets,
worktree isolation, and verification contract from ADR 0010 unchanged.

The mapping is a recommended execution profile, not a dependency of the task
protocol. An external AI system may perform either role if it follows the same
contract.

## Consequences

- New task dispatches use the GPT-6 model IDs.
- Existing commits and completed task evidence remain valid; model changes do
  not trigger reimplementation.
- The orchestration document and machine-readable task manifest must agree on
  the mapping and reasoning levels.
