# Phase 2 failover evidence

- Result: **PASS**
- Run: `artifacts/runs/phase-2/test-240-20260927T190329Z.json`
- Finished (UTC): `2026-09-27T19:04:14+00:00`
- Runtime: existing project WSL2 Docker Engine and Containerlab lab
- Docker settings changed: **no**
- Convergence limit: **10 seconds**

## Measurements

| Failure | Convergence | Recovery | Return path evidence |
|---|---:|---:|---|
| provider failure | 0.772 s | 2.341 | provider route policy |
| edge failure | 0.971 s | 12.783 | forward_received=4; reverse_received=4 |
| tunnel failure | 0.378 s | 20.209 | forward_received=4; reverse_received=4 |

## Result

All three failures converged to their backup paths within 10 seconds. Primary path restoration was measured separately; bidirectional probes confirmed the secondary encrypted path for edge and tunnel failures.
