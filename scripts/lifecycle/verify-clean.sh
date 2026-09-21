#!/usr/bin/env bash
# TOP-040: prove this project's containers and management network are absent.
set -Eeuo pipefail

command -v docker >/dev/null 2>&1 || { echo 'runtime=blocked: Docker CLI is missing' >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo 'runtime=blocked: Docker daemon is unavailable' >&2; exit 2; }

containers=$(docker ps -aq --filter 'label=clab-lab-name=netlab-phase-1')
if [[ -n "${containers}" ]]; then
  printf 'project resources remain (containers):\n%s\n' "${containers}" >&2
  exit 1
fi

if docker network inspect netlab-mgmt >/dev/null 2>&1; then
  echo 'project resource remains: network netlab-mgmt' >&2
  exit 1
fi

echo 'clean: no netlab-phase-1 containers or netlab-mgmt network'
