# Lab Topology Notes

Orientation notes for the lab directories in netlab.

## Areas

- `lab/` — topologies and fixtures used to exercise network scenarios.
- `automation/` — the glue that builds, boots, and checks the lab.
- `services/` — services that run inside or beside the lab.
- `observability/` — the collection and view layer for lab metrics.
- `evidence/` and `artifacts/` — outputs produced by runs, kept for traceability.

## Rules

- A lab change that touches topology must be checkable end-to-end from `automation/`.
- Artifacts are append-only within a run; don't overwrite history.

Newcomers should be able to reproduce the latest evidence folder with the current automation alone.
