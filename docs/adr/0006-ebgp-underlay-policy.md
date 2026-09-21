# ADR 0006: Use eBGP Only at the Simulated ISP Boundary

- Status: Accepted
- Date: 2026-09-21

## Decision

Use private ASNs recorded in the addressing plan. Each enterprise edge peers
with the ISP over a documentation `/31` underlay link.

Phase 1 policy is intentionally narrow:

- accept only `0.0.0.0/0` from the ISP
- apply a maximum-prefix limit of one to the default-only session
- export only the site's explicitly listed public endpoint `/32`
- reject RFC1918 enterprise site routes and all unlisted prefixes
- never use eBGP to carry inter-site enterprise reachability

Inter-site routes remain in OSPF over IPsec. Phase 2 adds a second ISP, local
preference, AS-path policy, and failover testing without changing this boundary.
The stable endpoint `/32` is advertised through both providers in Phase 2, so
IPsec peer identity does not depend on a provider-facing link address.

## Consequences

- A route leak is both less likely and easy to test.
- BGP and OSPF have separate responsibilities.
- Phase 1 demonstrates policy enforcement but not multihoming.

## Reference

- <https://docs.frrouting.org/en/latest/bgp.html>
