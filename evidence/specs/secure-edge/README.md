# EDGE-410 HQ SecureEdge Firewall Tier

Design: [ADR 0017](../../../docs/adr/0017-hq-secure-edge-firewall-tier.md).
Command: `make test-secure-edge` (static policy tests, then
`tests/security/secure_edge/acceptance.py` against the running lab).
Latest machine-readable result: [`edge410-latest.json`](edge410-latest.json).

## Live run: 2026-09-28, WSL2, lab deployed by `make evidence-phase-3`

The same lab first passed the full Phase 3 regression (`evidence-phase-3`,
fresh deploy through live acceptance) with `hq-fw-1` in place.

| Acceptance criterion | Evidence |
|---|---|
| Bypass around the tier is not routable | HQ distribution routes to Branch 1 and the simulated Internet, and HQ edge routes to HQ users, all use `hq-fw-1` link addresses. No HQ edge-to-distribution link exists in the inventory (static test). |
| Permitted flows work through the tier | HQ↔Branch 1 client ICMP both ways: one firewall accept each, zero denies. Guest DNS to the simulated Internet gets an answer. |
| Zones fail closed | Outside→service ICMP, outside→management ICMP, inside TCP/80 to the Internet, guest→remote users ICMP, and management→remote users ICMP get no reply and each raises the firewall deny counter. |
| Tier failure fails closed | With `hq-fw-1` paused, HQ WAN traffic stops in 2.7 s, the firewall route is withdrawn with no alternate path, and Branch 1 cannot reach HQ clients; intra-HQ traffic continues. Recovery after resume: 23.5 s. |
| Boot fails closed | `hq-fw-1` starts with `net.ipv4.ip_forward=0` (observed live before policy apply); the security apply enables forwarding only after the drop-by-default table is loaded. |

Denied packets are logged with the prefix `EDGE410|hq-fw-1|<chain>|deny`.

## Limits

- `hq-fw-1` is a single point of failure for HQ WAN and inter-site traffic,
  as accepted in ADR 0017.
- Firewall logs and counters are not yet shipped to the observability stack;
  that correlation is EDGE-430.
