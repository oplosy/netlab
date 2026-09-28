# Phase 3 Branch 2 Acceptance

- Result: **PASS**
- Run: `artifacts/runs/phase-3/site-330-20260928T071338Z`
- Started (UTC): `2026-09-28T07:13:38+00:00`
- Docker/WSL daemon settings changed: **no**
- Runtime action: project-scoped Containerlab deploy/reconfigure and configuration apply

## Stages

| Stage | Result | Duration |
|---|---|---:|
| inventory | PASS | 0.213s |
| topology | PASS | 0.277s |
| static_suite | PASS | 3.686s |
| deploy_project_lab | PASS | 77.793s |
| apply_gateways | PASS | 73.445s |
| apply_ipsec | PASS | 22.722s |
| apply_ospf_bfd | PASS | 14.024s |
| apply_security | PASS | 38.926s |
| apply_services | PASS | 102.549s |
| reapply_services | PASS | 95.2s |
| reapply_gateways | PASS | 87.068s |
| reapply_ipsec | PASS | 22.109s |
| reapply_ospf_bfd | PASS | 9.277s |
| reapply_security | PASS | 21.791s |
| branch2_live_acceptance | PASS | 183.191s |

## Acceptance

- One Branch 2 summary route on HQ and a reciprocal HQ summary on Branch 2.
- Guest-to-corporate/OOB and non-OOB SSH denied with counters; guest DNS/NTP and OOB controls exercised.
- Branch 1 DHCP, DNS, NTP, corporate reachability, and guest-deny checks run in the same suite.
