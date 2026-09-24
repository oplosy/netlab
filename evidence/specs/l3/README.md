# L3 gateway evidence procedure

From the dedicated WSL2 runtime, start the Phase 1 topology and run the full
gateway acceptance target:

    make lab-up
    make test-l3
    make lab-down

The apply configures each distribution-to-edge /31 on its distribution-side
interface, enables IPv4 forwarding, creates an OVS system access port and a
Linux veth peer for each declared SVI, and assigns the distribution address to
the Linux endpoint. OVS enables RSTP on the system port and marks it as an edge
port. Keepalived advertises the inventory .1 address on each VLAN. Distribution
1 is preferred at priority 150, matching its RSTP root preference; distribution
2 uses priority 100. VRRP advertisements use the other distribution SVI address
as an explicit unicast peer.

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

OVS internal SVI ports do not participate in RSTP, so the gateway policy uses
RSTP edge system ports backed by Linux veth peers. The phase integration branch
exposes static and live checks, plus two apply runs, through `make test-l3`.

## Latest live run: 2026-09-24, `make test-l3` on `integration/phase-1`

Static plan tests pass (5 tests), Ruff passes, and the gateway policy applied
twice and converged on all four distribution nodes. Both sites pass the live
acceptance: the four routed /31 links are selected by their route tables; user
clients reach both distribution SVI addresses and the server VLAN through the
`.1` VRRP VIP; distribution 2 takes all four VIPs and forwards traffic after
distribution 1 is stopped; and guest VLAN 30 to user VLAN 10 is denied with
100% packet loss. The lab was destroyed and `make verify-clean` reported no
phase-1 containers or `netlab-mgmt` network.

DHCP relay remains untested because the inventory has no `svc-dhcp-1`
`service_address` and the topology has no runtime relay agent. No DHCP lease
acceptance is claimed.
