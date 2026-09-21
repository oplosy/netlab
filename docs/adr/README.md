# Architecture Decision Records

Accepted ADRs are implementation constraints. A later decision may supersede an
ADR, but existing files are not edited to erase decision history.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-lab-platform.md) | Containerlab in dedicated WSL2 | Accepted |
| [0002](0002-open-network-stack.md) | Open Linux network stack | Accepted |
| [0003](0003-failure-domain-boundaries.md) | Site-local Layer 2 boundaries | Accepted |
| [0004](0004-addressing-and-naming.md) | Hierarchical addressing and naming | Accepted |
| [0005](0005-ospf-design.md) | Multi-area OSPF with site summaries | Accepted |
| [0006](0006-ebgp-underlay-policy.md) | eBGP as the ISP underlay boundary | Accepted |
| [0007](0007-route-based-ipsec.md) | Certificate-based route-based IPsec | Accepted |
| [0008](0008-management-and-security.md) | Separate OOB management and default-deny zones | Accepted |
| [0009](0009-observability-stack.md) | Correlated metrics, logs, flows, and captures | Accepted |
| [0010](0010-source-of-truth-and-orchestration.md) | Git intent, Ansible execution, agent worktrees | Accepted |

## Status values

- `Proposed`: under review and not safe to implement against
- `Accepted`: binding for implementation
- `Superseded`: replaced by a newer ADR
- `Rejected`: considered but not selected
