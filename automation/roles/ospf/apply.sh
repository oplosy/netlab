#!/usr/bin/env bash
# Apply inventory-rendered OSPF and BFD configuration to Phase 1 routers.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
PYTHON=${PYTHON:-python3}
DOCKER=${DOCKER:-docker}
tmp=$(mktemp -d "${TMPDIR:-/tmp}/netlab-ospf.XXXXXX")
trap 'rm -rf -- "$tmp"' EXIT

"${PYTHON}" "${ROOT}/config/routing/ospf/render.py" --output-dir "${tmp}"
for node in hq-edge-1 br1-edge-1 hq-dist-1 hq-dist-2 br1-dist-1 br1-dist-2; do
  container="clab-netlab-phase-1-${node}"
  [[ -s "${tmp}/${node}.conf" ]] || { echo "missing rendered config for ${node}" >&2; exit 2; }
  "${DOCKER}" inspect --type container "${container}" >/dev/null || {
    echo "required lab router is not running: ${container}" >&2
    exit 2
  }
  "${DOCKER}" exec "${container}" sh -ec '
    daemons=/etc/frr/daemons
    reload=0
    for daemon in ospfd bfdd; do
      if grep -qx "${daemon}=yes" "$daemons"; then
        :
      elif grep -qx "${daemon}=no" "$daemons"; then
        sed -i "s/^${daemon}=no$/${daemon}=yes/" "$daemons"
        reload=1
      else
        echo "could not find an exact ${daemon} setting in $daemons" >&2
        exit 2
      fi
    done
    pgrep -x ospfd >/dev/null 2>&1 || reload=1
    pgrep -x bfdd >/dev/null 2>&1 || reload=1
    if [ "$reload" -eq 1 ]; then
      # FRR reload starts newly enabled daemons without restarting running ones.
      /usr/lib/frr/frrinit.sh reload
    fi
    grep -qx "ospfd=yes" "$daemons"
    grep -qx "bfdd=yes" "$daemons"
    pgrep -x ospfd >/dev/null
    pgrep -x bfdd >/dev/null
    # Keep the connected OOB subnet, but do not let its injected default win over OSPF.
    while ip route show default dev eth0 | grep -q .; do
      ip route del default dev eth0
    done
    if ip route show default dev eth0 | grep -q .; then
      echo "OOB management default route remains installed" >&2
      exit 1
    fi
  '
  "${DOCKER}" cp "${tmp}/${node}.conf" "${container}:/tmp/netlab-ospf.conf"
  "${DOCKER}" exec "${container}" vtysh -f /tmp/netlab-ospf.conf
  echo "applied OSPF/BFD policy: ${node}"
done
