#!/usr/bin/env bash
# TOP-040: deploy only the rendered Phase 1 topology.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TOPOLOGY=${TOPOLOGY:-"${ROOT}/lab/phase-1.clab.yml"}
LAB_NAME=netlab-phase-1

need() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'runtime=blocked: required command is missing: %s\n' "$1" >&2
    exit 2
  }
}

need docker
need containerlab
[[ -f "${TOPOLOGY}" ]] || { printf 'topology missing: %s (run make topology)\n' "${TOPOLOGY}" >&2; exit 2; }
docker info >/dev/null 2>&1 || {
  printf 'runtime=blocked: Docker daemon is unavailable\n' >&2
  exit 2
}

# --reconfigure makes repeated applies converge without deleting unrelated
# containers, networks, or images.
containerlab deploy --topo "${TOPOLOGY}" --reconfigure
printf 'deployed lab=%s topology=%s\n' "${LAB_NAME}" "${TOPOLOGY}"
