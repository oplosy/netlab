#!/usr/bin/env bash
# TOP-040: destroy only the Phase 1 topology and its generated lab directory.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TOPOLOGY=${TOPOLOGY:-"${ROOT}/lab/phase-1.clab.yml"}
command -v containerlab >/dev/null 2>&1 || { echo 'runtime=blocked: containerlab is missing' >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo 'runtime=blocked: Docker CLI is missing' >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo 'runtime=blocked: Docker daemon is unavailable' >&2; exit 2; }
[[ -f "${TOPOLOGY}" ]] || { echo "topology missing: ${TOPOLOGY}" >&2; exit 2; }

# The topology path scopes removal to this lab. Never use containerlab
# destroy --all or broad docker prune/rm commands here.
containerlab destroy --topo "${TOPOLOGY}" --cleanup
printf 'destroyed lab=netlab-phase-1 topology=%s\n' "${TOPOLOGY}"
