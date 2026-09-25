# ADR 0015: Dual-Provider Full-Mesh Edge Topology

- Status: Accepted
- Date: 2026-09-25
- Supersedes: None
- Related: ADR 0004, ADR 0005, ADR 0006, ADR 0007

## Context

WAN-210 adds a second provider and a second edge at each active enterprise
site. WAN-220 must be able to exercise provider choice and local preference
from either edge. A single-homed secondary edge would not provide that test
surface and would leave provider failure behavior dependent on one router.

## Decision

- HQ and BR1 each have edge-1 and edge-2.
- Each of the four enterprise edges forms eBGP with both ISP-1 (AS 65000) and
  ISP-2 (AS 65001). ISP-2's ASN and underlay pool `198.51.100.0/24` are now
  active; allocate distinct `/31` links from that already reserved pool.
- Each edge has routed links to both distribution nodes at its site. OSPF
  carries the site-internal edge-to-distribution reachability.
- ISP routers remain isolated from one another. eBGP remains only at the ISP
  boundary; inter-site enterprise reachability remains OSPF over IPsec.
- WAN-210 keeps each site's stable public endpoint `/32` and Phase 1 IPsec
  transport on edge-1. Edge-2 must not originate that endpoint until WAN-230
  installs and verifies the secondary XFRM overlay path.
- WAN-220 owns local-preference and provider path-selection policy. WAN-210
  establishes filtered sessions and validates both providers without enabling
  failover policy prematurely.

## Consequences

- BGP, OSPF, gateway planning, and IPsec planning must tolerate a second edge
  while preserving Phase 1's edge-1 overlay behavior.
- WAN-220 can test primary and backup provider paths independently on each
  edge, and WAN-230 can add a second overlay endpoint without changing the
  provider topology.
- The full mesh adds eight enterprise-to-provider eBGP sessions across the
  two active sites.

## Verification

- Inventory validation proves all eight eBGP adjacencies use allocated `/31`
  links and valid active ASNs.
- `make test-bgp` proves both providers establish filtered sessions and only
  explicit stable endpoint prefixes are accepted/exported.
- Phase 1 OSPF, IPsec, and service acceptance remains green with edge-1 as the
  active overlay endpoint.
