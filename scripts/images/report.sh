#!/usr/bin/env bash
# IMG-020 resolved image report. This does not mutate Docker state.
set -Eeuo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)
# shellcheck disable=SC1091
source "${REPO_ROOT}/versions.env"
printf 'base_image=%s\nbase_digest=%s\nplatform=%s\napt_snapshot=%s\n' \
  "${UBUNTU_BASE_IMAGE}" "${UBUNTU_BASE_DIGEST}" "${IMAGE_PLATFORM}" "${UBUNTU_SNAPSHOT}"
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  printf 'runtime=blocked (Docker CLI or daemon unavailable)\n'
  exit 2
fi
for image in "${NETLAB_NETWORK_IMAGE}" "${NETLAB_CLIENT_IMAGE}" "${NETLAB_SERVICE_IMAGE}"; do
  printf '\n[%s]\n' "${image}"
  docker image inspect "${image}" --format 'id={{.Id}} repo_digests={{json .RepoDigests}} labels={{json .Config.Labels}}'
done
