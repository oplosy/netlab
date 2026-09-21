# ADR 0002: Use an Open Linux Network Stack

- Status: Accepted
- Date: 2026-09-21

## Context

The required behaviors span routing, switching, gateway redundancy, firewalling,
IPsec, and flow export. A single freely redistributable network OS does not
provide every required classic enterprise feature with equal fidelity.

## Decision

Compose network functions from:

- FRR for OSPF, BGP, BFD, and route inspection
- Open vSwitch for VLANs, RSTP, LACP, and IPFIX
- Keepalived for VRRP
- strongSwan for IKEv2/IPsec
- nftables for stateful forwarding and control-plane rules

Build role-specific images from pinned packages. Access nodes enable only
switching components; distribution nodes combine switching, routing, and VRRP;
edge nodes combine routing, IPsec, and firewall/NAT functions.

## Consequences

- Every image can be rebuilt without a vendor image license.
- Protocol evidence uses standard Linux and component commands rather than a
  unified vendor CLI.
- Container privilege is required, so the dedicated WSL distribution is part of
  the security boundary.
- Feature interaction must be integration-tested; component support alone is
  not accepted as proof.

## References

- <https://containerlab.dev/manual/kinds/linux/>
- <https://containerlab.dev/manual/kinds/ovs-bridge/>
- <https://docs.openvswitch.org/>
- <https://docs.frrouting.org/en/latest/>
