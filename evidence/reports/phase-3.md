# Phase 3 Branch 2 Acceptance

- Result: **PASS**
- Run: `artifacts/runs/phase-3/site-330-20260928T113445Z`
- Started (UTC): `2026-09-28T11:34:45+00:00`
- Docker/WSL daemon settings changed: **no**
- Runtime action: project-scoped Containerlab deploy/reconfigure and configuration apply

## Stages

| Stage | Result | Duration |
|---|---|---:|
| inventory | PASS | 0.252s |
| topology | PASS | 0.346s |
| static_suite | PASS | 3.495s |
| deploy_project_lab | PASS | 80.414s |
| apply_gateways | PASS | 80.795s |
| apply_ipsec | PASS | 23.233s |
| apply_ospf_bfd | PASS | 15.774s |
| apply_security | PASS | 41.153s |
| apply_services | PASS | 114.237s |
| reapply_services | PASS | 105.923s |
| reapply_gateways | PASS | 95.148s |
| reapply_ipsec | PASS | 25.943s |
| reapply_ospf_bfd | PASS | 11.025s |
| reapply_security | PASS | 26.979s |
| branch2_live_acceptance | PASS | 189.048s |

## Acceptance

- One Branch 2 summary route on HQ and a reciprocal HQ summary on Branch 2.
- Guest-to-corporate/OOB and non-OOB SSH denied with counters; guest DNS/NTP and OOB controls exercised.
- Branch 1 DHCP, DNS, NTP, corporate reachability, and guest-deny checks run in the same suite.
