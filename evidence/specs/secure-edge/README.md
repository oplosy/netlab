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

## EDGE-420 inline Suricata IPS

Design: [ADR 0018](../../../docs/adr/0018-inline-suricata-ips.md).
Command: `make test-suricata`.
Latest machine-readable result: [`edge420-latest.json`](edge420-latest.json).

Live run: 2026-09-28. Suricata 7.0.3 runs on `hq-fw-1`, bound to NFQUEUE 0,
with the local rules in `secure-edge/suricata/rules/netlab-local.rules`.
The same lab first passed `evidence-phase-3` and `test-secure-edge` with
every HQ north-south flow queued through the IPS.

| Acceptance criterion | Evidence |
|---|---|
| Curated signatures alert deterministically | From a DHCP-leased HQ guest client, a benign query raises no alert. Each `ids-test.netlab` query is answered and raises exactly one alert (sid 9420001, action `allowed`). Repeated twice. |
| Configured IPS traffic is blocked | Each `ips-block.netlab` query gets no answer and raises exactly one alert (sid 9420002, action `blocked`). Repeated twice. |
| Failure mode matches the documented policy (fail closed) | After Suricata is killed, queue 0 is released in 1.1 s and guest DNS stops. The idempotent security apply restarts it, and traffic recovers 3.3 s after the apply starts. |

Permitted-flow probes run from a real guest client: `hq-fw-1` reaches the
guest subnet over ECMP, so a reply to a distribution-SVI source can land on
the other distribution router, which has no connection state for it.

## Limits

- `hq-fw-1` is a single point of failure for HQ WAN and inter-site traffic,
  as accepted in ADR 0017.
- A Suricata outage isolates HQ from the WAN until it restarts (fail closed,
  ADR 0018). Suricata does not restart itself; the security apply restarts it.
- Firewall logs, counters, and Suricata `eve.json` alerts are not yet shipped
  to the observability stack; that correlation is EDGE-430.
