# Multi-Site Enterprise Network Lab

A reproducible enterprise network lab that joins campus switching, routed WAN,
security services, operations tooling, and evidence-driven failure testing in a
single portfolio project.

The first delivery contains one headquarters site, one branch, and one
simulated ISP. Later phases add a second ISP, a second branch, SecureEdge, and
advanced network automation.

## Project status

The Phase 1 lab is implemented and integrated on `integration/phase-1`. Its
latest full acceptance run passed 23 required stages and all 25 quantitative
thresholds. The operational procedure is in the [Phase 1 runbook](docs/runbooks/phase-1.md),
and the [curated acceptance report](evidence/reports/phase-1.md) records the
verified results and limits.

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

`main` is never a working branch and is never pushed to directly. Each bounded
task runs in its own branch and worktree. A phase is delivered through a pull
request from its integration branch to `main`.
