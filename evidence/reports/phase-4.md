# Phase 4 SecureEdge correlation evidence

- Result: **PASS**
- Run ID: `edge430-20260928t120624z`
- Bundle: `artifacts/runs/phase-4/edge430-20260928t120624z`
- Started (UTC): `2026-09-28T12:06:24+00:00`
- Probe clients (DHCP): `hq-client-guest-1` 10.10.30.100, `hq-client-users-1` 10.10.10.100
- IPFIX sampling on hq-access-1: 1 during the run, restored to `400`
- Docker settings changed: **no**

## Correlated cases

Every record is joined by the run ID and the probe flow key (UDP source
port or ICMP identifier). The run ID is also carried in each probe payload
(DNS query label or ICMP payload).

| Case | Client | Flow | Firewall decision | IDS alert | Packets with run ID | IPFIX sampler | Reply | Result |
|---|---|---|---|---|---:|---|---|---|
| allowed | 10.10.30.100 | UDP 40431 → 203.0.113.10:53 | queue (trace d2970396) | 9420001 allowed | 1 | 172.31.255.33 | yes | **PASS** |
| ips-blocked | 10.10.30.100 | UDP 40432 → 203.0.113.10:53 | queue (trace 227eeec6) | 9420002 blocked | 1 | 172.31.255.33 | no | **PASS** |
| fw-blocked | 10.10.10.100 | ICMP id 40433 → 10.20.20.2 | drop (trace 2f88a107) | none | 1 | 172.31.255.33 | no | **PASS** |

`fw-blocked` is HQ users' ICMP to the Branch 1 server subnet: the
distribution SEC-170 policy permits it and hq-fw-1 denies it before the
IPS queue, so it has no IDS event by design.
