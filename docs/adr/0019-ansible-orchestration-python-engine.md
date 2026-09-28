# ADR 0019: Ansible Orchestrates, Python Renders and Applies, NetBox Projects Intent

- Status: Accepted
- Date: 2026-09-28
- Supersedes: the execution clause of ADR 0010 ("Ansible renders and applies
  configuration from that intent")
- Related: ADR 0010, ADR 0011, ADR 0016

## Context

ADR 0010 says Ansible renders and applies configuration. In phases 1 to 4 the
rendering and convergence logic was built and live-verified as Python modules
(`config/**/apply.py`, `config/routing/**/render.py`, `automation/roles/**`).
The Ansible roles are thin wrappers that call them. Phase 5 adds NetBox,
generated inventory, and a safe change workflow. It needs one clear
execution model, and rewriting verified convergence code into Ansible modules
would discard evidence for no functional gain.

## Decision

- **Engine.** The Python renderers and idempotent apply modules remain the
  only code that computes and converges device state.
- **Orchestration.** Ansible playbooks under `automation/playbooks/` run the
  change workflow as explicit stages: `precheck`, `backup`, `diff`, `apply`,
  and `postcheck`. Each stage calls the Python engine and records its result.
  Ansible does not template device configuration.
- **Inventory.** `automation/inventory/hosts.yml` is generated from the
  validated Git intent and marked as generated. A dynamic inventory script
  reads the same hosts from NetBox. The two must be equal.
- **NetBox.** NetBox is a rebuildable projection of Git intent (ADR 0010).
  `automation/netbox/sync.py` seeds an empty NetBox and reconciles it
  idempotently. Every managed object carries the `netlab-intent` tag.
  A NetBox-only edit is reported as drift and is never read back into Git.
  NetBox runs as the `netlab-netbox` compose project on `127.0.0.1` with
  pinned images; its secrets are generated at runtime and never committed.
- **Drift.** Device drift is a difference between the normalized live state
  and the golden snapshot recorded by the last successful `postcheck`.
  Intent drift is a difference between Git intent and NetBox, or between the
  current render and the render recorded with the golden snapshot. Both fail
  the drift report.

## Consequences

- Existing live evidence stays valid, because the convergence code is unchanged.
- The Ansible layer is small and auditable; `ansible-lint` and syntax checks
  run in CI.
- The golden snapshot defines "no material change": a second apply must
  leave the normalized snapshot identical.
- Normalization must drop volatile state such as counters, uptimes, and VRRP
  virtual addresses that move on failover, or failover would read as drift.
