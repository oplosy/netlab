#!/usr/bin/env bash
# IMG-020 reproducible image build. --pull is intentionally omitted because
# the base is a digest-pinned manifest.
set -Eeuo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)
# shellcheck disable=SC1091
source "${REPO_ROOT}/versions.env"

command -v docker >/dev/null 2>&1 || {
  printf '[BLOCKED] Docker CLI is unavailable; no image build was attempted.\n' >&2
  exit 2
}
docker info >/dev/null 2>&1 || {
  printf '[BLOCKED] Docker daemon is unavailable; no image build was attempted.\n' >&2
  exit 2
}
[[ ${UBUNTU_BASE_IMAGE} == *@sha256:* ]] || { echo 'base image is not digest-pinned' >&2; exit 1; }

build_image() {
  local name=$1 context=$2
  shift 2
  printf 'building %s from %s (%s)\n' "${name}" "${context}" "${IMAGE_PLATFORM}"
  local -a cache_args=()
  if [[ ${IMAGE_NO_CACHE:-0} == 1 ]]; then cache_args+=(--no-cache); fi
  docker build \
    "${cache_args[@]}" \
    --platform "${IMAGE_PLATFORM}" \
    --build-arg "UBUNTU_BASE_IMAGE=${UBUNTU_BASE_IMAGE}" \
    --build-arg "UBUNTU_SNAPSHOT=${UBUNTU_SNAPSHOT}" \
    "$@" \
    --tag "${name}" \
    --file "${REPO_ROOT}/${context}/Dockerfile" \
    "${REPO_ROOT}"
}

# NETLAB_BUILD_ONLY limits the build to named images (network-node, client,
# service); the CI topology smoke test builds only network-node (AUTO-540).
wants() { [[ -z ${NETLAB_BUILD_ONLY:-} || " ${NETLAB_BUILD_ONLY} " == *" $1 "* ]]; }

wants network-node && build_image "${NETLAB_NETWORK_IMAGE}" images/network-node \
  --build-arg "FRR_VERSION=${FRR_VERSION}" \
  --build-arg "OVS_VERSION=${OVS_VERSION}" \
  --build-arg "KEEPALIVED_VERSION=${KEEPALIVED_VERSION}" \
  --build-arg "STRONGSWAN_VERSION=${STRONGSWAN_VERSION}" \
  --build-arg "NFTABLES_VERSION=${NFTABLES_VERSION}" \
  --build-arg "ISC_DHCP_RELAY_VERSION=${ISC_DHCP_RELAY_VERSION}" \
  --build-arg "OPENSSH_SERVER_VERSION=${OPENSSH_SERVER_VERSION}" \
  --build-arg "LIBPAM_RADIUS_AUTH_VERSION=${LIBPAM_RADIUS_AUTH_VERSION}" \
  --build-arg "SURICATA_VERSION=${SURICATA_VERSION}"
wants client && build_image "${NETLAB_CLIENT_IMAGE}" images/client
wants service && build_image "${NETLAB_SERVICE_IMAGE}" images/service \
  --build-arg "KEA_VERSION=${KEA_VERSION}" \
  --build-arg "BIND9_VERSION=${BIND9_VERSION}" \
  --build-arg "BIND9_UTILS_VERSION=${BIND9_UTILS_VERSION}" \
  --build-arg "CHRONY_VERSION=${CHRONY_VERSION}" \
  --build-arg "FREERADIUS_VERSION=${FREERADIUS_VERSION}" \
  --build-arg "DHCP_CLIENT_VERSION=${DHCP_CLIENT_VERSION}" \
  --build-arg "OPENSSH_CLIENT_VERSION=${OPENSSH_CLIENT_VERSION}" \
  --build-arg "SSHPASS_VERSION=${SSHPASS_VERSION}"

printf 'images built with base %s\n' "${UBUNTU_BASE_IMAGE}"
