# Phase 2 failover evidence

- Result: **PASS**
- Run: `artifacts/runs/phase-2/test-240-20260928T081529Z.json`
- Finished (UTC): `2026-09-28T08:16:26+00:00`
- Runtime: existing project WSL2 Docker Engine and Containerlab lab
- Docker settings changed: **no**
- Convergence limit: **10 seconds**

## Measurements

| Failure | Convergence | Recovery | Return path evidence |
|---|---:|---:|---|
| provider failure | 1.04 s | 2.04 | provider route policy |
| edge failure | 0.903 s | 19.574 | forward_received=4; reverse_received=4 |
| tunnel failure | 5.357 s | 20.143 | forward_received=4; reverse_received=4 |

## Result

All three failures converged to their backup paths within 10 seconds. Primary path restoration was measured separately; bidirectional probes confirmed the secondary encrypted path for edge and tunnel failures.
