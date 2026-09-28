# Multi-Site Enterprise Network Lab

A reproducible enterprise network lab that joins campus switching, routed WAN,
security services, operations tooling, and evidence-driven failure testing in a
single portfolio project.

The first delivery contains one headquarters site, one branch, and one
simulated ISP. Later phases add a second ISP, a second branch, SecureEdge, and
advanced network automation.

## Project status

Phases 0 to 3 are complete and merged: HQ, two branches, and two simulated
ISPs with switching, routing, IPsec, services, security, and telemetry. The
curated acceptance reports are in [evidence/reports](evidence/reports), and
the operational procedure is in the [Phase 1 runbook](docs/runbooks/phase-1.md).

Next: a CI quality gate (`CI-050`) and Phase 4 (SecureEdge). Phase 5 (network
automation) is a stretch goal.

## Locked baseline

- Containerlab running in a dedicated WSL2 Linux environment
- Linux/FRR routing nodes and Open vSwitch switching
- RSTP, LACP, and VRRP for site resiliency
- Multi-area OSPF inside the enterprise
- eBGP at the simulated Internet boundary
- IKEv2 route-based IPsec between sites
- A physically separate Containerlab management network
- Git-managed design intent and Ansible-driven repeatability
- Prometheus, Grafana, Loki, SNMPv3, syslog, and IPFIX telemetry

See [architecture overview](docs/architecture/overview.md),
[architecture decisions](docs/adr/README.md), and the
[implementation plan](docs/plans/implementation-plan.md).

## Delivery rule

`main` is never pushed to directly. Each phase is built on one
`phase/<n>-<slug>` branch and delivered through one pull request. See the
[delivery workflow](docs/plans/workflow.md).
