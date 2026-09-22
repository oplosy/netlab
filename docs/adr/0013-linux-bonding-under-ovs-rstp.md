# ADR 0013: Put Linux LACP Bonds Below Open vSwitch RSTP

- Status: Proposed
- Date: 2026-09-23
- Supersedes on acceptance: ADR 0002's selection of Open vSwitch for LACP
- Clarifies on acceptance: ADR 0003's logical-bundle RSTP implementation

## Context

ADR 0002 assigned both LACP and RSTP to Open vSwitch. ADR 0003 requires RSTP
to resolve the loop among the site's two-member logical uplinks and
distribution peer link. The pinned Open vSwitch package does not include bond
ports in its RSTP tree. The Ubuntu Noble OVS schema states that bond ports do
not participate in RSTP.

The L2-110 diagnostic confirmed the conflict on the live Phase 1 topology:
LACP on both HQ access uplink bonds negotiated, but
`ovs-appctl rstp/show netlab-br0` listed only access ports. The HQ access bridge elected itself root
despite the lower priority configured on HQ distribution 1. A VLAN 10 probe
could not resolve ARP across the uplink. The lab was torn down cleanly. These
observations do not satisfy the L2 acceptance gate.

The dedicated WSL kernel has `CONFIG_BONDING=m`; an isolated network namespace
successfully created a Linux bond in `802.3ad` mode and was then removed. That
test establishes kernel support, but not yet compatibility with RSTP in the
full lab.

## Proposed decision

Use Linux bonding in IEEE 802.3ad mode for each two-member site-local LACP
bundle. Enslave the two physical Containerlab data interfaces to one Linux bond
interface. Add that bond interface to Open vSwitch as a *single-interface Port*.
Open vSwitch continues to own VLAN trunk/access policy, RSTP, and IPFIX. RSTP
must see and control the single-interface Port representing each logical link.

Keep the existing peer pairs, VLAN boundaries, root preferences, and failure
objectives. A bond still terminates on exactly one distribution node; this
decision does not introduce multi-chassis LAG or stretched VLANs.

Apply must be idempotent and must not forward a partially configured loop.
Physical members join the Linux bond before the logical Port is admitted to
the OVS bridge. Enabling the OVS Port requires RSTP to be configured on the
bridge. Runtime state remains ephemeral and is rebuilt from inventory.

## Acceptance gate before changing status to Accepted

On the pinned WSL kernel, network-node image, and OVS version:

1. The two physical members negotiate one Linux 802.3ad bond at both peers.
2. `ovs-appctl rstp/show` lists the bond's single-interface OVS Port and shows
   the intended site root and one loop-free alternate path.
3. VLAN 10 baseline traffic succeeds; guest-to-user traffic remains denied.
4. One-member LACP failure and active RSTP-path failure meet the measured
   1-second and 5-second interruption limits, respectively.
5. A second apply makes no material state change, and teardown leaves no lab
   containers, network, or namespace.

Until these checks pass, L2-110 remains unintegrated and the Phase 1 switching
design is not accepted.

## Considered alternative

Keep OVS LACP and add separate unbonded links for RSTP. That would require a
different physical loop and topology/inventory changes while leaving the
current bonded uplinks outside RSTP. The Linux-bond layering preserves the
chosen logical topology if the acceptance gate passes.

## References

- <https://manpages.ubuntu.com/manpages/noble/man5/ovs-vswitchd.conf.db.5.html>
- <https://docs.openvswitch.org/en/stable/topics/bonding/>
