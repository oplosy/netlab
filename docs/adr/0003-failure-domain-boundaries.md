# ADR 0003: Bound Layer 2 to Each Site

- Status: Accepted
- Date: 2026-09-21

## Context

Stretching VLANs between sites enlarges broadcast and spanning-tree failure
domains, couples WAN availability to Layer 2 behavior, and weakens route-policy
boundaries.

## Decision

Use identical VLAN numbers across sites but create independent site-local VLANs.
Terminate VLANs on a redundant distribution pair. Use routed links between the
distribution layer and the site edge, and use a routed encrypted overlay between
sites.

Within a site:

- two-link LACP bundles protect individual uplinks
- a bundle terminates on exactly one distribution node
- RSTP resolves loops among logical bundles
- VRRP provides the first-hop virtual address
- RSTP root and VRRP master preferences are aligned

Phase 1 intentionally has a single edge at each site and one ISP. Tests may
claim link and distribution-path resilience, but not edge or provider
resilience.

## Consequences

- Broadcast faults and STP reconvergence remain local.
- Inter-site traffic always crosses a visible Layer 3 and policy boundary.
- Multi-chassis LAG is not simulated or falsely implied.
- Full edge resilience is deferred to the second-ISP phase.
