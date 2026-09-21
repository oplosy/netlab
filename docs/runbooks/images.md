# Reproducible lab images (IMG-020)

The image lock is in `versions.env`. All three Dockerfiles use the same
linux/amd64 Ubuntu 24.04 manifest digest and an immutable Ubuntu snapshot
(`UBUNTU_SNAPSHOT`). Package installation uses exact Noble package versions;
the Docker build records the installed `dpkg` versions in
`/usr/share/netlab/component-versions` and exposes the same values as image
labels. No `latest`, `stable`, or unqualified base image is permitted.

## Locked inputs

| Component | Exact package version | Official source |
|---|---|---|
| Ubuntu base | `ubuntu:24.04@sha256:a61567bd31828687156d735ea8eb01ba4e37636e225dd6a48ba94136a70d9d61` (linux/amd64 manifest) | [Docker Hub Ubuntu 24.04 layers](https://hub.docker.com/layers/library/ubuntu/24.04/images/sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254) |
| FRR | `8.4.4-1.1ubuntu6.7` | [Ubuntu Noble FRR package search](https://packages.ubuntu.com/search?arch=any&keywords=frr&searchon=names&suite=all) |
| Open vSwitch | `3.3.9-0ubuntu0.24.04.1` | [Ubuntu Noble Open vSwitch package search](https://packages.ubuntu.com/openvswitch-switch) |
| Keepalived | `1:2.2.8-1build2` | [Ubuntu Noble Keepalived package](https://packages.ubuntu.com/en/noble/amd64/admin/keepalived) |
| strongSwan control plane | `charon-systemd=5.9.13-2ubuntu4.24.04.4`, `strongswan-swanctl=5.9.13-2ubuntu4.24.04.4` | [Ubuntu Noble charon-systemd file list](https://packages.ubuntu.com/noble/amd64/charon-systemd/filelist), [Ubuntu Noble strongSwan package](https://packages.ubuntu.com/noble/strongswan) |
| nftables | `1.0.9-1build1` | [Ubuntu Noble nftables package](https://packages.ubuntu.com/noble/net/nftables) |

The package versions above are distribution package versions, not the latest
upstream release labels. This is intentional: the image installs from the
locked Ubuntu snapshot, so the label, installed `dpkg` version, and lock file
must agree. For upstream release history see [FRRouting releases](https://frrouting.org/release/),
[Open vSwitch downloads](https://www.openvswitch.org/download/),
[Keepalived downloads](https://www.keepalived.org/download/),
[strongSwan downloads](https://www.strongswan.org/download.html), and
[nftables releases](https://www.nftables.org/projects/nftables/downloads.html).

The network image intentionally does not install Ubuntu's `strongswan`
metapackage: that metapackage depends on both `strongswan-charon` and
`strongswan-starter`, which would introduce the legacy starter daemon. The
image installs `charon-systemd` (binary `/usr/sbin/charon-systemd`) and
`strongswan-swanctl`; their shared daemon/plugin dependencies are resolved by
APT. The recorded `strongswan=` component version is the installed
`charon-systemd` package version.

The base digest is a registry manifest digest, not merely a tag. Verify the
linux/amd64 mapping before changing it:

```sh
docker buildx imagetools inspect \
  ubuntu:24.04@sha256:a61567bd31828687156d735ea8eb01ba4e37636e225dd6a48ba94136a70d9d61
```

The output must include the same digest for `linux/amd64`. Update
`versions.env`, all Dockerfile defaults, and this table together when the base
is intentionally refreshed.

## Build and verify

Run these commands inside the dedicated WSL2 distribution after `make
preflight` succeeds. The build deliberately omits Docker `--pull`; the base
reference is already immutable.

```sh
make images
make verify-images
make image-report
```

`make image-report` prints the base digest, platform, apt snapshot, local image
IDs, repository digests, and component labels. It is read-only. A missing
Docker CLI or daemon is reported as `runtime=blocked` and is not a successful
runtime verification.

To perform two independent clean builds and compare the component labels and
installed package manifest, use a disposable Docker builder or remove only the
three task-tagged images between runs. The image ID itself is not an acceptance
criterion because builder metadata can vary while the locked component bytes
and labels remain the same:

```sh
IMAGE_NO_CACHE=1 make images
docker run --rm --entrypoint cat netlab/network-node:0.1.0 /usr/share/netlab/component-versions > /tmp/netlab-images-first.txt
IMAGE_NO_CACHE=1 make images
docker run --rm --entrypoint cat netlab/network-node:0.1.0 /usr/share/netlab/component-versions > /tmp/netlab-images-second.txt
diff -u /tmp/netlab-images-first.txt /tmp/netlab-images-second.txt
make verify-images
```

The repository does not run these commands automatically because they require
a reachable Docker daemon and download the locked package snapshot. Never
claim clean-build evidence when Docker is unavailable.

## Roles and health checks

`netlab/network-node:0.1.0` supports `NETLAB_NODE_ROLE=edge`,
`distribution`, `router`, and `access`. The edge role starts FRR,
`charon-systemd`, nftables, and OVS; distribution starts FRR and nftables and
starts Keepalived only when a meaningful `/etc/keepalived/keepalived.conf`
exists; router starts FRR, nftables, and OVS without strongSwan or Keepalived;
access starts OVS only. Startup is fail-fast when a required binary or daemon
cannot start. The health check verifies the OVS bridge and datapath, then the
role-specific FRR, strongSwan, Keepalived, and nftables state. A distribution
skeleton with no VRRP configuration remains healthy until L3 configuration is
rendered. An absent, empty, or comment-only Keepalived file is skipped; any
non-comment content makes Keepalived required, so an invalid present config
fails fast instead of being silently ignored.

OVS selection is explicit and never silently falls back:

```sh
docker run --rm --privileged \
  -e NETLAB_NODE_ROLE=access -e OVS_DATAPATH_MODE=kernel \
  netlab/network-node:0.1.0
docker exec <container> cat /run/netlab/ovs-datapath.json
docker exec <container> ovs-vsctl get Bridge netlab-br0 datapath_type
```

`OVS_DATAPATH_MODE=kernel` maps to OVS `datapath_type=system`; `userspace`
maps to `netdev`. The JSON file and `ovs-vsctl` output are the required
evidence. Kernel mode requires the host `CONFIG_OPENVSWITCH` capability from
`versions.env`; userspace mode is an explicit test choice, not an implicit
fallback.

`netlab/client:0.1.0` keeps a client process alive and checks the loopback
interface and local name resolution. `netlab/service:0.1.0` serves a
deterministic `/health` endpoint on port 8080 and checks it with curl. Both
images have Docker healthchecks and can be given an explicit command by
Containerlab.

## Current host limitation

The current Windows WSL environment has no usable Docker/Containerlab runtime
and lacks `CONFIG_XFRM_INTERFACE`. Therefore static lock, Dockerfile, script,
and negative configuration checks can run locally, but `make images`, registry
manifest inspection, two clean builds, and daemon health checks remain blocked
until the dedicated WSL2 environment is available. This limitation does not
change the committed lock or claim a build result.
