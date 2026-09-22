# L2 switching evidence procedure

Run these commands inside the dedicated WSL2 runtime, while holding the
exclusive lab runtime lock. The deployment lifecycle does not yet call the
switching role, so apply the inventory-derived policy after deploying:

```sh
python3 config/switching/apply.py
python3 tests/integration/l2/measure.py --site all
```

The apply command validates `inventory/inventory.yaml`, checks that every
switch container exists before making changes, then converges the VLAN, LACP,
and RSTP settings. Repeating it uses OVS `--may-exist` operations and explicit
port/bridge values, so it does not create additional bonds or ports.

The measurement runner performs three checks at each site:

- It gives the guest and user clients temporary addresses in the same test
  subnet and requires guest-to-user ping to fail with 100% packet loss.
- It removes one member from the access-to-distribution-1 LACP bundle and
  measures elapsed time from failure injection to the first successful ping
  while that member remains down; the limit is 1 second.
- It removes both members of that active uplink, waits for RSTP to move traffic
  through distribution 2, and measures elapsed time from failure injection to
  the first successful ping while the direct path remains down; the limit is 5
  seconds.

Traffic runs between the site user client and a temporary VLAN 10 OVS internal
port on distribution 1. The runner refuses to overwrite existing IPv4
configuration on the client data interface. Its `finally` cleanup restores
the failed links, removes the temporary addresses, terminates ping, and deletes
the temporary OVS port. Preserve the full command output with the phase run
artifacts; do not report live acceptance until the procedure succeeds.

`make test-l2` is the requested task-level command, but its Make target is
integration-only work outside this task's allowed paths. The phase orchestrator
must wire the static pytest checks and this runtime measurement into that
target and into the deploy/apply lifecycle.
