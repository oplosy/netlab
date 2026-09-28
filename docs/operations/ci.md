# Continuous integration

Pull requests to `main` run `.github/workflows/pull-request-policy.yml`.

| Job | Required | What it runs |
|---|---|---|
| `policy` | yes | Rejects tracked secrets and runtime paths, checks governance files, then runs `make ci-static`. |
| `topology-smoke` | no | Installs containerlab from the pinned `.deb` (SHA256 in `versions.env`), builds only the network-node image, and runs `scripts/ci/topology-smoke.sh`. |

## `make ci-static` (CI-050, AUTO-540)

No Docker, Containerlab, or lab runtime is needed; live tests stay skipped.

- Inventory schema and semantics (`make validate-inventory`).
- `ruff`, `yamllint --strict`, `ansible-playbook --syntax-check`, and `ansible-lint`
  for the change playbook.
- Offline tests: unit, plan, rendered-policy, NetBox projection, render, and
  drift and snapshot logic.
- The generated topology is current (`render_topology.py --check`).
- The render is deterministic, every generated file names its generator, and
  `automation/inventory/hosts.yml` is current (`render_all.py --check`).

## Topology smoke test (AUTO-540)

`lab/ci-smoke.clab.yml` holds two network-node routers on one link. The test
proves that FRR starts, the node limits are applied, and the link carries
ICMP. The lab is always destroyed afterwards.

| Bound | Value |
|---|---|
| Job timeout | 30 minutes (`timeout-minutes`) |
| Test timeout | `SMOKE_TIMEOUT`, 300 s by default |
| Per-node CPU and memory | 0.5 CPU, 512 MB (verified from the container) |
| Management network | `172.31.254.0/24`, separate from the project lab |

CI never needs committed credentials. NetBox, Suricata, and the live lab
suites are not part of CI.

## Local equivalent

```bash
make ci-static
NETLAB_BUILD_ONLY=network-node make images   # only if the image is missing
make ci-smoke                                # SUDO=sudo if containerlab needs root
```
