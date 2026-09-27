# Phase 3 Branch 2 Acceptance

- Result: **PASS**
- Run: `artifacts/runs/phase-3/site-330-20260927T202957Z`
- Started (UTC): `2026-09-27T20:29:57+00:00`
- Docker/WSL daemon settings changed: **no**
- Runtime action: project-scoped Containerlab deploy/reconfigure and configuration apply
- Reused passing deploy/apply evidence from: `artifacts/runs/phase-3/site-330-20260927T200750Z`

## Stages

| Stage | Result | Duration |
|---|---|---:|
| inventory | PASS | 0.672s |
| topology | PASS | 0.713s |
| static_suite | PASS | 5.206s |
| deploy_project_lab | PASS | 78.349s |
| apply_gateways | PASS | 74.317s |
| apply_ipsec | PASS | 22.672s |
| apply_ospf_bfd | PASS | 14.407s |
| apply_security | PASS | 37.85s |
| apply_services | PASS | 104.078s |
| reapply_services | PASS | 93.663s |
| reapply_gateways | PASS | 84.242s |
| reapply_ipsec | PASS | 22.424s |
| reapply_ospf_bfd | PASS | 9.128s |
| reapply_security | PASS | 19.839s |
| branch2_live_acceptance | PASS | 143.408s |

## Acceptance

- One Branch 2 summary route on HQ and a reciprocal HQ summary on Branch 2.
- Guest-to-corporate/OOB and non-OOB SSH denied with counters; guest DNS/NTP and OOB controls exercised.
- Branch 1 DHCP, DNS, NTP, corporate reachability, and guest-deny checks run in the same suite.
