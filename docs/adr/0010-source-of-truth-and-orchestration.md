# ADR 0010: Keep Intent in Git and Execute Work Through Isolated Agents

- Status: Accepted
- Date: 2026-09-21

## Decision

Git-managed YAML is the canonical design intent. Ansible renders and applies
configuration from that intent. Phase 1 uses static generated inventory; the
automation phase adds NetBox as a rebuildable query projection and dynamic
inventory source. A NetBox-only edit is drift, not desired state.

Delivery uses a phase integration branch. The orchestrator owns that branch and
delegates bounded tasks to workers. Each worker uses a dedicated branch and
worktree and may edit only its task's allowed paths. Workers never push to
`main`. The phase integration branch reaches `main` only through a reviewed pull
request.

Recommended agent mapping:

- orchestrator: `gpt-5.6-sol`, medium reasoning
- worker: `gpt-5.6-luna`, high reasoning

The task contract is model-neutral so another capable planner and worker model
can replace either mapping.

## Consequences

- Parallel work has explicit ownership and reduced edit collisions.
- Orchestration metadata is part of the project, not hidden in chat history.
- NetBox loss does not prevent a clean rebuild.
- The orchestrator must verify worker evidence rather than accepting summaries
  at face value.

## Reference

- <https://docs.ansible.com/projects/ansible/latest/network/user_guide/network_resource_modules.html>
