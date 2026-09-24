# ADR 0014: Give Site Services Explicit Data-Plane Addresses

- Status: Proposed
- Date: 2026-09-24
- Supersedes: ADR-0008 guest DNS/NTP reachability scope only; preserves OOB isolation

## Context

SVC-160 requires corporate clients and guests to use DHCP, DNS, and NTP. The
current service nodes have only Containerlab OOB interfaces. ADR-0008 forbids
routing data-plane VLANs to OOB, so the current inventory cannot satisfy those
acceptance criteria without violating the management-plane boundary.

## Proposed decision

Keep OOB addresses exclusively for administration. Give each site a local
service node with a second interface in that site's existing SERVERS VLAN
(VLAN 20), using a reserved static address from that subnet. Run Kea DHCP,
BIND 9, and chrony on both site nodes. Generate equivalent site-local DNS zone
content from the authoritative inventory so local name resolution does not
require inter-site routing. Each BIND instance forwards names outside the
internal zone to the simulated ISP DNS endpoint at `203.0.113.10`; each local
chrony instance synchronizes from the simulated ISP NTP endpoint at
`203.0.113.11`. Keep both simulated endpoints on the ISP-routed service network,
not on the management bridge. Keep FreeRADIUS on its OOB-only service address;
only infrastructure administration can reach it.

Configure each site's gateway pair to relay DHCP requests from the local VLANs
to the local Kea address. Permit users and servers to use their site's DNS and
NTP addresses. Permit guests only DNS and NTP to those explicit addresses;
other guest-to-server traffic remains denied. No VLAN receives a route to an
OOB address, and no service listener uses its OOB address for client traffic.

Extend SVC-160's task paths to include the inventory and generated topology
inputs required to add the site service nodes and VLAN attachments. Add tests
that show local lease, DNS, and time behavior at both sites, plus a negative
check that a client cannot reach an OOB address.

## Consequences

- Each site continues to provide core services when the inter-site overlay is
  unavailable.
- DNS data must be rendered consistently from Git intent at both sites.
- The service nodes are dual-homed, so service listeners and firewall rules
  must bind to the data-plane address explicitly.
- Service reachability and management reachability remain separate and can be
  verified independently.

## Alternatives considered

- Routing clients directly to OOB service addresses violates ADR-0008 and is
  rejected.
- Hosting all services centrally behind the HQ server VLAN makes branch
  bootstrap depend on the IPsec overlay and inter-site routing. It is not the
  baseline choice.

## Acceptance evidence

- Each site's client receives the site-specific subnet, gateway, DNS, and NTP
  settings from its local service node.
- Both sites resolve the inventory-derived internal zone and the simulated
  Internet zone.
- The configured local clock source is reachable from each site.
- A denied OOB reachability probe fails from corporate and guest VLANs.
- Guest DNS and NTP probes succeed while other guest-to-server probes fail.