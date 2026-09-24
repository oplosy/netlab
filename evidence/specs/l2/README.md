# L2 switching evidence procedure

Run these commands inside the dedicated WSL2 runtime, while holding the
exclusive lab runtime lock. Load the kernel bonding module from Windows before
entering the unprivileged WSL shell:

```powershell
wsl.exe -d Ubuntu -u root -- modprobe bonding
```

The Phase 1 make lab-up lifecycle deploys the topology, applies BGP, then applies the inventory-derived switching policy. To recheck idempotency manually, run:

```sh
python3 config/switching/apply.py
python3 config/switching/apply.py
python3 tests/integration/l2/measure.py --site all
```

The apply command validates `inventory/inventory.yaml`, checks that every
switch container exists before making changes, then enables RSTP on every
switch before creating Linux 802.3ad bonds. Each physical member joins its
site-local Linux bond before that bond is admitted as one OVS trunk Port.
Repeating the command leaves already enslaved members up and reuses the same
bond and OVS Port.

The measurement runner performs three checks at each site:

- It gives the guest and user clients temporary addresses in the same test
  subnet and requires guest-to-user ping to fail with 100% packet loss.
- It removes one member from the access-to-distribution-1 LACP bundle and
  measures the longest ping reply gap while that member remains down; the
  limit is 1 second.
- It removes both members of that active uplink, waits for RSTP to move traffic
  through distribution 2, and measures the longest reply gap while the direct
  path remains down; the limit is 5 seconds.

Traffic runs between the site user client and a temporary veth probe attached
to VLAN 10 on distribution 1. The probe keeps a stable MAC across cases. The
runner waits for its RSTP Port to reach Forwarding before starting the baseline
ping, and refuses to overwrite existing client IPv4 configuration. Its
`finally` cleanup restores failed links, removes temporary addresses, stops
ping, and deletes the probe. Preserve full command output with the phase run
artifacts; do not report live acceptance until the procedure succeeds.

`make test-l2` runs the static policy checks and live failure measurements.
`make test-l2-peer` checks bidirectional ARP between temporary distribution
ports on VLANs 10, 20, 30, and 99. Start the lab with `make lab-up` first and
preserve full output as task evidence.

## Peer adjacency investigation: 2026-09-24

The first BR1 live attempt failed on VLAN 10. Distribution 1 emitted ARP
requests, and OVS learned the source MAC on distribution 2's peer trunk, while
the receiving probe showed zero packets. That attempt used an OVS `internal`
port as its probe. The pinned Ubuntu Noble OVS schema says internal ports do
not work with RSTP and are excluded from RSTP by default, so the failed probe
did not prove a production peer-trunk defect.

The probe now uses a veth pair: the OVS-facing end is a `system` Port in the
declared access VLAN, with RSTP explicitly enabled and the edge flag set; the
other end carries the temporary test address. The runner waits for both peer
trunks and every probe Port to report RSTP Forwarding before it sends ARP. On
failure it reports the probe interface counters, OVS Port state, RSTP state,
port counters, and MAC table from both distribution nodes. The updated live
acceptance has not yet been run. The lab was torn down and `make verify-clean`
passed after the previous attempt.
