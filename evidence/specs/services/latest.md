# SVC-160 runtime evidence

- Date: 2026-09-24
- Runtime: Containerlab 0.77.0 in the dedicated project WSL distribution
- Topology: `netlab-phase-1`, 25 nodes, 32 physical links
- Runtime health: all 25 project containers reported `Up (healthy)` after acceptance
- Endpoint isolation: client container uses Docker `network-mode=none`; only `lo` and the data-plane `eth1` were present
- Docker daemon/Desktop configuration: unchanged
- Command: `python3 tests/integration/services/acceptance.py`
- Result: `SVC-160 live acceptance: PASS`

The run verified one DHCP relay per active VRRP VLAN at both sites; correct
leases, gateway, DNS, and NTP options for HQ and BR1 users, servers, and guests;
internal and simulated Internet DNS answers; and a successful NTP query for
every client role. Guest probes to the server VLAN and OOB were denied with the
expected gateway counters. Client VLAN probes to OOB were denied at both sites.

Central AAA accepted the valid administrator credential and rejected an invalid
credential. SSH listeners were bound only to each infrastructure node's OOB
address. With RADIUS blocked on the test router, only the local break-glass
account reached the forced `vtysh` CLI. Runtime-generated credentials were
redacted and not written to the repository.

Endpoint clients and servers have no OOB management interface in the rendered
topology. Site DNS and NTP upstream requests use narrowly scoped SNAT rules for
the service source addresses and the simulated ISP DNS/NTP endpoints; the ISP
BGP policy continues to advertise only site public endpoint `/32` routes.
