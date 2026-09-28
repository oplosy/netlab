#!/usr/bin/env bash
# Apply inventory-rendered BGP policy to every ISP and edge router.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
PYTHON=${PYTHON:-python3}
DOCKER=${DOCKER:-docker}
tmp=$(mktemp -d "${TMPDIR:-/tmp}/netlab-bgp.XXXXXX")
trap 'rm -rf -- "$tmp"' EXIT

"${PYTHON}" "${ROOT}/config/routing/bgp/render.py" --output-dir "${tmp}"
for config in "${tmp}"/*.conf; do
  node=${config##*/}
  node=${node%.conf}
  container="clab-netlab-phase-1-${node}"
  [[ -s "${config}" ]] || { echo "missing rendered config for ${node}" >&2; exit 2; }
  "${DOCKER}" inspect --type container "${container}" >/dev/null || {
    echo "required lab router is not running: ${container}" >&2
    exit 2
  }
  "${DOCKER}" exec "${container}" sh -ec '
    daemons=/etc/frr/daemons
    start_bgpd=0
    if grep -qx "bgpd=yes" "$daemons"; then
      :
    elif grep -qx "bgpd=no" "$daemons"; then
      sed -i "s/^bgpd=no$/bgpd=yes/" "$daemons"
      start_bgpd=1
    else
      echo "could not find an exact bgpd setting in $daemons" >&2
      exit 2
    fi
    if ! pgrep -x bgpd >/dev/null 2>&1; then
      start_bgpd=1
    fi
    if [ "$start_bgpd" -eq 1 ]; then
      # FRR reload starts newly enabled daemons without restarting running ones.
      /usr/lib/frr/frrinit.sh reload
    fi
    # eth0 is OOB management only. Drop its injected default route while
    # preserving the connected management subnet; the imported ISP default
    # must be the only default used by the data plane.
    while ip route show default dev eth0 | grep -q .; do
      ip route del default dev eth0
    done
    if ip route show default dev eth0 | grep -q .; then
      echo "OOB management default route remains installed" >&2
      exit 1
    fi
    grep -qx "bgpd=yes" "$daemons"
    pgrep -x bgpd >/dev/null
  '
  "${DOCKER}" cp "${config}" "${container}:/tmp/netlab-bgp.conf"
  "${DOCKER}" exec "${container}" vtysh -f /tmp/netlab-bgp.conf
  echo "applied BGP policy: ${node}"
done
