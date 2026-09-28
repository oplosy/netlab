# ADR 0017: Insert a Routed SecureEdge Firewall Tier at Headquarters

- Status: Accepted
- Date: 2026-09-28
- Supersedes: None
- Related: ADR 0003, ADR 0004, ADR 0005, ADR 0008, ADR 0015

## Context

Phase 4 requires SecureEdge to be an explicit security service tier, not a
renamed edge router. Today, zone policy lives only in nftables tables on the
distribution and edge routers (SEC-170), and each HQ distribution router has a
direct routed link to each HQ edge router. That means no single enforcement
point exists, and nothing proves that traffic cannot bypass a security tier.

## Decision

Add one routed firewall node, `hq-fw-1`, between the HQ distribution pair and
the HQ edge pair.

- **Topology.** The four direct HQ edge-to-distribution links are removed.
  `hq-fw-1` gets one routed link to each HQ edge and one to each HQ
  distribution router. No other path exists between the HQ site and its
  edges, so a bypass is not routable.
- **Addressing.** The new links use `10.10.252.8/31` to `10.10.252.14/31`
  from the HQ point-to-point block. The retired `10.10.252.0/31` to
  `10.10.252.6/31` are not reused. `hq-fw-1` uses loopback `10.10.255.6/32`
  and OOB address `172.31.255.35`.
- **Routing.** `hq-fw-1` is an internal OSPF Area 10 router with BFD on all
  four links. It runs no BGP, no VRRP, and no redistribution. The HQ edges
  stay the Area 0/Area 10 boundary and keep originating the conditional
  default route and the HQ summary.
- **Zones.** Zones are bound to interfaces and site prefixes:

  | Zone | Membership |
  |---|---|
  | outside | links toward `hq-edge-1` and `hq-edge-2` |
  | inside | HQ USERS `10.10.10.0/24`, via the distribution links |
  | service | HQ SERVERS `10.10.20.0/24`, via the distribution links |
  | guest | HQ GUEST `10.10.30.0/24`, via the distribution links |
  | management | HQ INBAND-MGMT `10.10.99.0/24`; the node's own OOB `eth0` |

- **Policy.** The forward chain drops by default and fails closed. It admits
  only the HQ north-south flows that SEC-170 already allows on the HQ edges,
  derived from the same inventory intent, plus established return traffic.
  Every other forwarded packet is counted and logged with the prefix
  `EDGE410|hq-fw-1|<chain>|deny`. The input chain admits only OSPF and BFD
  from the four link peers, and SSH, ICMP, and SNMP from the OOB network.
- **Defense in depth.** SEC-170 tables on the distribution and edge routers
  stay unchanged. A flow must be allowed by both tiers.

## Consequences

- HQ has exactly one security enforcement point on the north-south path, and
  its policy is testable in isolation.
- `hq-fw-1` is a single point of failure for HQ's WAN and inter-site traffic.
  This is accepted for the lab: its failure isolates HQ from the WAN (fail
  closed) while intra-HQ traffic continues. A redundant pair would need a
  later ADR.
- HQ edge-failover evidence is now observed from `hq-fw-1` instead of from the
  distribution routers. Tests that assumed direct HQ edge-to-distribution
  links change accordingly. Branch sites are unchanged.
- The lab grows by one container.
