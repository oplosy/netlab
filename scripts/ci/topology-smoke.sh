#!/usr/bin/env bash
# AUTO-540 bounded topology smoke test. Deploys lab/ci-smoke.clab.yml (two
# network-node routers, 0.5 CPU and 512 MB each), proves FRR starts and the
# link carries ICMP, and always destroys the lab. The whole run is bounded by
# SMOKE_TIMEOUT seconds (default 300). Needs Docker, containerlab, and the
# network-node image named in versions.env.
set -Eeuo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
# shellcheck disable=SC1091
source "${ROOT}/versions.env"
export NETLAB_NETWORK_IMAGE
TOPOLOGY="${ROOT}/lab/ci-smoke.clab.yml"
SMOKE_TIMEOUT=${SMOKE_TIMEOUT:-300}
SUDO=${SUDO-}
started=$(date +%s)

# sudo resets the environment; pass the image variable the topology expands.
clab() { ${SUDO} env NETLAB_NETWORK_IMAGE="${NETLAB_NETWORK_IMAGE}" containerlab "$@"; }
cleanup() { clab destroy --topo "${TOPOLOGY}" --cleanup >/dev/null 2>&1 || true; }
trap cleanup EXIT
deadline() { (( $(date +%s) - started < SMOKE_TIMEOUT )) || { echo "smoke timeout after ${SMOKE_TIMEOUT}s" >&2; exit 124; }; }
node() { printf 'clab-netlab-ci-smoke-%s' "$1"; }

docker image inspect "${NETLAB_NETWORK_IMAGE}" >/dev/null
timeout "${SMOKE_TIMEOUT}" ${SUDO} env NETLAB_NETWORK_IMAGE="${NETLAB_NETWORK_IMAGE}" containerlab deploy --topo "${TOPOLOGY}" --reconfigure >/dev/null

for r in r1 r2; do
  until docker exec "$(node "$r")" vtysh -c "show version" >/dev/null 2>&1; do deadline; sleep 2; done
  # containerlab applies cpu 0.5 as a CFS quota/period and 512MB as decimal bytes.
  limit=$(docker inspect -f '{{.HostConfig.CpuQuota}} {{.HostConfig.CpuPeriod}} {{.HostConfig.Memory}}' "$(node "$r")")
  [[ ${limit} == "50000 100000 512000000" ]] || { echo "$r resource limits not applied: ${limit}" >&2; exit 1; }
done
docker exec "$(node r1)" ip address replace 192.0.2.1/31 dev eth1
docker exec "$(node r2)" ip address replace 192.0.2.0/31 dev eth1
docker exec "$(node r1)" ip link set eth1 up
docker exec "$(node r2)" ip link set eth1 up

probe='import socket,struct,sys
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_ICMP); s.settimeout(2)
for _ in range(5):
    s.sendto(struct.pack("!BBHHH",8,0,0,0,1)+b"netlab-smoke",("192.0.2.0",0))
    try: s.recvfrom(256); print("reply"); sys.exit(0)
    except OSError: pass
sys.exit(1)'
until docker exec "$(node r1)" python3 -c "${probe}" >/dev/null 2>&1; do deadline; sleep 1; done

printf 'topology smoke: PASS (2 routers, FRR up, link ICMP ok, limits 0.5 CPU/512MB) in %ss\n' \
  "$(( $(date +%s) - started ))"
