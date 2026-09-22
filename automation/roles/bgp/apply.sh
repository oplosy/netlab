#!/usr/bin/env bash
# Apply inventory-rendered BGP policy to the three running Phase 1 routers.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
PYTHON=${PYTHON:-python3}
DOCKER=${DOCKER:-docker}
tmp=$(mktemp -d "${TMPDIR:-/tmp}/netlab-bgp.XXXXXX")
trap 'rm -rf -- "$tmp"' EXIT

"${PYTHON}" "${ROOT}/config/routing/bgp/render.py" --output-dir "${tmp}"
for node in hq-edge-1 br1-edge-1 isp1-core-1; do
  container="clab-netlab-phase-1-${node}"
  [[ -s "${tmp}/${node}.conf" ]] || { echo "missing rendered config for ${node}" >&2; exit 2; }
  "${DOCKER}" inspect --type container "${container}" >/dev/null || {
    echo "required lab router is not running: ${container}" >&2
    exit 2
  }
  "${DOCKER}" cp "${tmp}/${node}.conf" "${container}:/tmp/netlab-bgp.conf"
  "${DOCKER}" exec "${container}" vtysh -f /tmp/netlab-bgp.conf
  echo "applied BGP policy: ${node}"
done
