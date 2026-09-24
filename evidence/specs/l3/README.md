# L3 gateway evidence procedure

From the dedicated WSL2 runtime, load Linux bonding if needed, then start the
Phase 1 topology and apply the inventory-derived gateway configuration:

    make lab-up
    python3 config/gateway/apply.py
    python3 config/gateway/apply.py
    python3 -m pytest -q -p no:cacheprovider tests/integration/l3/test_gateway_plan.py
    python3 tests/integration/l3/measure.py --site all
    make lab-down

The apply configures each distribution-to-edge /31 on its distribution-side
interface, enables IPv4 forwarding, creates an OVS system access port and a
Linux veth peer for each declared SVI, and assigns the distribution address to
the Linux endpoint. OVS enables RSTP on the system port and marks it as an edge
port. Keepalived advertises
the inventory .1 address on each VLAN. Distribution 1 is preferred at priority
150, matching its RSTP root preference; distribution 2 uses priority 100. VRRP
advertisements use the other distribution SVI address as an explicit unicast peer.

The live test assigns a temporary address to each site's user client, proves
that the client reaches its .1 default gateway, then stops Keepalived on
distribution 1 and proves that distribution 2 owns the VIP and forwards
traffic. It removes temporary client state afterward and leaves distribution 2
as the active owner after the injected failure. Run make lab-down immediately
after this test before starting other topology checks.

The inventory has no address for svc-dhcp-1. Apply writes per-VLAN relay hook
metadata to /run/netlab/dhcp-relay-hooks.json, with the target disabled until
the services task assigns a server address and runtime relay agent. This task
does not claim DHCP lease acceptance.

Preserve full command output and route/VIP inspection with the phase run
artifacts. Do not report live acceptance until both sites pass and teardown
reports a clean lab.

## Latest live run: 2026-09-24

Static plan tests pass (5 tests). A live BR1 run configured the four
distribution gateways and all four routed /31 links. The BR1 user client could
reach both distribution SVI addresses, and guest-to-user traffic was denied
with 100% packet loss. VRRP acceptance failed: both gateways owned the four
`.1` VIPs and both Keepalived instances entered MASTER. The peer routes selected
the expected VLAN interfaces, but each peer SVI neighbor remained `INCOMPLETE`
on VLANs 10, 20, 30, and 99. The inter-distribution LACP bond was up with two
members. A follow-up L2 probe showed the peer trunk learns distribution 1's
VLAN 10 source MAC, while distribution 2's internal VLAN port receives no
packets. The exact OVS forwarding cause is still open. The live VIP-to-server
positive check and failover check were not reached, and HQ has not been
measured.

The live failure was caused by OVS internal SVI ports, which do not participate
in RSTP and therefore could not forward unicast VRRP advertisements between
distribution nodes. The implementation now migrates those ports to RSTP edge
system ports backed by Linux veth peers.

## Latest live run: 2026-09-24, after SVI migration

Static plan tests pass (5 tests), Ruff passes, and applying gateway policy twice
converges on all four distribution nodes. Both sites pass the live acceptance:
the four routed /31 links are selected by their route tables; user clients
reach both distribution SVI addresses and the server VLAN through the `.1`
VRRP VIP; distribution 2 takes all four VIPs and forwards traffic after
distribution 1 is stopped; and guest VLAN 30 to user VLAN 10 is denied with
100% packet loss. The lab was destroyed and `make verify-clean` reported no
phase-1 containers or `netlab-mgmt` network.

DHCP relay remains untested because the inventory has no `svc-dhcp-1`
`service_address` and the topology has no runtime relay agent. No DHCP lease
acceptance is claimed.
