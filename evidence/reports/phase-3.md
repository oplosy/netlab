# Phase 3 Branch 2 Acceptance

- Result: **PASS**
- Run: `artifacts/runs/phase-3/site-330-20260928T074159Z`
- Started (UTC): `2026-09-28T07:41:59+00:00`
- Docker/WSL daemon settings changed: **no**
- Runtime action: project-scoped Containerlab deploy/reconfigure and configuration apply

## Stages

| Stage | Result | Duration |
|---|---|---:|
| inventory | PASS | 0.274s |
| topology | PASS | 0.358s |
| static_suite | PASS | 4.981s |
| deploy_project_lab | PASS | 78.668s |
| apply_gateways | PASS | 74.184s |
| apply_ipsec | PASS | 27.04s |
| apply_ospf_bfd | PASS | 16.673s |
| apply_security | PASS | 38.747s |
| apply_services | PASS | 105.194s |
| reapply_services | PASS | 98.704s |
| reapply_gateways | PASS | 107.73s |
| reapply_ipsec | PASS | 22.834s |
| reapply_ospf_bfd | PASS | 14.897s |
| reapply_security | PASS | 21.847s |
| branch2_live_acceptance | PASS | 181.909s |

## Acceptance

- One Branch 2 summary route on HQ and a reciprocal HQ summary on Branch 2.
- Guest-to-corporate/OOB and non-OOB SSH denied with counters; guest DNS/NTP and OOB controls exercised.
- Branch 1 DHCP, DNS, NTP, corporate reachability, and guest-deny checks run in the same suite.
