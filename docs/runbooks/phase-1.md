# Phase 1 build, acceptance, and recovery

This runbook builds and verifies the headquarters plus Branch 1 lab. It uses
the dedicated WSL2 distribution and the Docker Engine inside that distribution
as required by [ADR-0001](../adr/0001-lab-platform.md). Run the commands from
one persistent WSL shell at the repository root. Do not close that shell while
the lab is running; the WSL runtime can stop when its owning process exits.

For pinned tool versions and kernel prerequisites, follow the
[environment runbook](environment.md). For image inputs and image checks, see
the [image runbook](images.md).

## Prepare the environment

Open the dedicated WSL distribution, then enter the repository:

```powershell
wsl.exe -d Ubuntu -u atlas
```

```sh
cd /mnt/c/Users/<user>/Desktop/workspace/A-projects/netlab
```

Check the host and toolchain before changing runtime state:

```sh
make preflight
```

The check must end with `Preflight passed`. It is read-only. If the bonding
module is absent, load the kernel module before deployment:

```sh
test -d /sys/module/bonding || sudo modprobe bonding
test -d /sys/module/bonding
```

The preflight also checks the XFRM interface feature needed by the site tunnel.
Resolve a failed kernel check using the instructions in `environment.md`; do
not change Docker daemon settings to address a kernel capability failure.

Build and verify the pinned images, then check the generated topology:

```sh
make images
make verify-images
make image-report
make topology-check
```

`image-report` should show the locked component versions and immutable image
digests. `topology-check` must end with `topology current`. The full acceptance
runner checks image locks again but does not rebuild images.

## Run full Phase 1 acceptance

SEC-170 verifies kernel namespace logging. The test needs
`net.netfilter.nf_log_all_netns=1` temporarily in the WSL init namespace. Save
the prior value and restore it automatically when the shell exits, including
when the test fails:

```sh
set -Eeuo pipefail
prior_nf_log_all_netns=$(sysctl -n net.netfilter.nf_log_all_netns)
restore_nf_log_all_netns() {
  sudo sysctl -w "net.netfilter.nf_log_all_netns=${prior_nf_log_all_netns}" >/dev/null
}
trap restore_nf_log_all_netns EXIT
sudo sysctl -w net.netfilter.nf_log_all_netns=1
make evidence-phase-1
```

The runner owns the lab for the duration of the test. It deploys the topology,
applies routing and services, runs the live L2/L3, IPsec, security, and
observability checks, evaluates thresholds, and tears down the Phase 1 lab and
observability containers even when a test fails. It runs L3 before SVC-160:
service acceptance leases client addresses, while the L3 test needs to assign
its own temporary addresses.

A successful run prints a summary like this and exits with code 0:

```text
{"result": "PASS", "stages_passed": 23, "stages_failed": 0,
 "stages_warnings": 0, "thresholds_passed": 25, "thresholds_failed": 0}
```

Each run writes a timestamped bundle under
`artifacts/runs/phase-1/phase-1-<timestamp>/`. `result.json` contains the stage
and threshold results; adjacent files contain command output, measurements,
and the IPsec underlay capture. The directory is generated evidence and is
ignored by Git. The curated, tracked summary is
[`evidence/reports/phase-1.md`](../../evidence/reports/phase-1.md).

The current reference run passed all 23 required stages and all 25 thresholds.
The captured tunnel traffic contained ESP and IKE packets and no cleartext
enterprise packets. The reported sysctl value was restored to its saved value
after the run.

## Run focused checks manually

Use the full acceptance runner for release evidence. For diagnosis, start the
lab and run focused checks in this order:

```sh
make lab-up
make test-ipsec
make test-ospf
make test-l2
make test-l3
make test-services
make test-security
make observability-up
make test-observability
```

`test-ospf` uses the XFRM tunnel, so run `test-ipsec` first. Run `test-l3`
before `test-services`, which obtains DHCP leases on the client interfaces.
L3 failover leaves the secondary distribution switch active until teardown;
use a fresh lab if another test depends on the preferred VRRP owner.
`test-security` requires the temporary namespace-logging sysctl described
above. The focused checks do not create the complete timestamped report bundle.

## Troubleshoot

- **Preflight reports a version or kernel failure:** use the `action:` line in
  the output and the environment runbook. Preflight does not install software,
  load modules, or change the Docker daemon.
- **Deployment says the Docker daemon is unavailable:** run `docker info` in
  the same WSL distribution. Keep the WSL shell open through teardown; do not
  switch the project to Docker Desktop.
- **IPsec or OSPF is not ready:** verify that `make test-ipsec` passed before
  `make test-ospf`. The evidence runner waits for both site summary routes
  before measuring OSPF failover.
- **Security acceptance reports missing namespace logging:** check
  `sysctl -n net.netfilter.nf_log_all_netns`, set it to `1` only for the run,
  and restore the saved value using the trap above.
- **A repeated service apply changes the result:** rerun the complete evidence
  suite from a clean lab. Service and gateway apply commands are intended to be
  idempotent; the suite applies them twice and tests live behavior.
- **A run fails:** inspect the first failed stage in that run's `result.json`
  and its matching `.stdout.txt` and `.stderr.txt` files. The runner performs
  project-scoped cleanup in its `finally` path.

## Tear down and confirm cleanup

For a manually started lab, stop the project runtime and prove it is gone:

```sh
make lab-down
make verify-clean
```

`lab-down` destroys only the topology named by `lab/phase-1.clab.yml` and
stops the observability Compose project. `verify-clean` must print:

```text
clean: no netlab-phase-1 containers or netlab-mgmt network
```

Do not use global container or image prune commands for project cleanup.
