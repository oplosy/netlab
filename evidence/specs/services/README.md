# SVC-160 service verification

This task adds inventory-derived Kea, BIND 9, chrony, and FreeRADIUS configs,
site-local data-plane service addresses, gateway DHCP relays, guest DNS/NTP
exceptions, and pinned image packages.

## Static verification

Run from the repository root:

```powershell
python scripts/validate/validate_inventory.py
python scripts/lifecycle/render_topology.py --check
python -B -m unittest tests.unit.inventory.test_inventory tests.integration.services.test_plan -v
python automation/roles/services/apply.py --plan
python config/gateway/apply.py --plan
```

The plan commands are read-only. `apply.py` without `--plan` changes container
network state and service configuration; do not run it while another task owns
the shared lab.

## Live acceptance

After the orchestrator releases the shared runtime, run:

```powershell
python tests/integration/services/acceptance.py
```

The procedure starts temporary helpers from the already-built pinned service
image in each client network namespace. It requests leases on the HQ and BR1
users, servers, and guest VLANs, checks the lease gateway/DNS/NTP options,
resolves each site's internal name and `www.internet.test`, and runs a chrony
query to the site's NTP address. It checks central FreeRADIUS accept and reject
responses while keeping generated credentials out of the output. For guest
denials it samples the distribution gateway's nftables counters before and
after probes to a server VLAN address and the AAA OOB address.

The procedure also tests a PAM/RADIUS-backed OOB SSH login, rejects an invalid
central password, verifies port 22 listens only on the OOB address, then
temporarily blocks RADIUS egress on one router and verifies that only the local
break-glass account still reaches the forced vtysh CLI. The temporary nftables
table is deleted in a `finally` cleanup path.

Expected final output is `SVC-160 live acceptance: PASS`; the output must also
show per-client DHCP/DNS/NTP success, guest drop counters, and central AAA
accept/reject. The procedure has not been run while OSPF-130 owns the runtime,
so no live result is claimed by this specification.

## Administrative fallback

If central AAA is unreachable, an operator can use the Docker host's privileged
local console to reach a network node and run its local FRR CLI, for example:

```powershell
docker exec --user 0 clab-netlab-phase-1-hq-edge-1 vtysh
```

The generated `netlab_breakglass` account is local to each network node and is
restricted by `sshd` to `/usr/bin/vtysh`; root SSH, shell access, and forwarding
are disabled. Its password and each node's RADIUS client secret are generated
at runtime and excluded from Git. The Docker host console remains an emergency
recovery path if the OOB service itself is unavailable.
