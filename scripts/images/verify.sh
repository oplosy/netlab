#!/usr/bin/env bash
# IMG-020 static and runtime verification. Runtime checks are explicitly
# reported as blocked when Docker is unavailable.
set -Eeuo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)
# shellcheck disable=SC1091
source "${REPO_ROOT}/versions.env"

failures=0
pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1" >&2; failures=$((failures + 1)); }

[[ ${UBUNTU_BASE_IMAGE} == ubuntu:24.04@sha256:* ]] && pass 'Ubuntu base image is immutable' || fail 'base image must be ubuntu:24.04@sha256:<digest>'
[[ ${UBUNTU_BASE_DIGEST} =~ ^sha256:[0-9a-f]{64}$ ]] && pass 'base digest format' || fail 'base digest has invalid format'
[[ ${UBUNTU_SNAPSHOT} =~ ^[0-9]{8}T[0-9]{6}Z$ ]] && pass 'Ubuntu apt snapshot is timestamp-pinned' || fail 'apt snapshot is not timestamp-pinned'

for image in network-node client service; do
  dockerfile=${REPO_ROOT}/images/${image}/Dockerfile
  [[ -f ${dockerfile} ]] || { fail "missing ${dockerfile}"; continue; }
  grep -Eq '^ARG UBUNTU_BASE_IMAGE=.*@sha256:[0-9a-f]{64}$' "${dockerfile}" && pass "${image} default base digest" || fail "${image} has no immutable default base"
  grep -Eq '^HEALTHCHECK ' "${dockerfile}" && pass "${image} healthcheck declared" || fail "${image} healthcheck missing"
  if grep -Eiq '^FROM .*(latest|stable|edge)([^a-zA-Z0-9]|$)' "${dockerfile}"; then fail "${image} uses a floating FROM tag"; fi
  grep -Fq "UBUNTU_SNAPSHOT}" "${dockerfile}" && pass "${image} uses the locked apt snapshot" || fail "${image} does not use UBUNTU_SNAPSHOT"
done

network_dockerfile=${REPO_ROOT}/images/network-node/Dockerfile
grep -Fq 'OVS_DATAPATH_MODE' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'OVS datapath selection is explicit' || fail 'OVS datapath selection missing'
grep -Fq 'ovs-datapath.json' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'OVS datapath evidence is written' || fail 'OVS datapath evidence missing'
grep -Fq 'OVS_DATAPATH_MODE must be kernel or userspace' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'invalid OVS mode is rejected (negative check)' || fail 'invalid OVS mode rejection missing'
grep -Fq 'netlab.component.frr="${FRR_VERSION}"' "${network_dockerfile}" && grep -Fq 'frr=${FRR_VERSION}' "${network_dockerfile}" && pass 'FRR package lock matches label' || fail 'FRR label/package lock mismatch'
grep -Fq 'netlab.component.openvswitch="${OVS_VERSION}"' "${network_dockerfile}" && grep -Fq 'openvswitch-switch=${OVS_VERSION}' "${network_dockerfile}" && pass 'OVS package lock matches label' || fail 'OVS label/package lock mismatch'
grep -Fq 'netlab.component.keepalived="${KEEPALIVED_VERSION}"' "${network_dockerfile}" && grep -Fq 'keepalived=${KEEPALIVED_VERSION}' "${network_dockerfile}" && pass 'Keepalived package lock matches label' || fail 'Keepalived label/package lock mismatch'
grep -Fq 'netlab.component.strongswan="${STRONGSWAN_VERSION}"' "${network_dockerfile}" && grep -Fq 'strongswan=${STRONGSWAN_VERSION}' "${network_dockerfile}" && pass 'strongSwan package lock matches label' || fail 'strongSwan label/package lock mismatch'
grep -Fq 'netlab.component.nftables="${NFTABLES_VERSION}"' "${network_dockerfile}" && grep -Fq 'nftables=${NFTABLES_VERSION}' "${network_dockerfile}" && pass 'nftables package lock matches label' || fail 'nftables label/package lock mismatch'
grep -Fq 'charon-systemd=${STRONGSWAN_VERSION}' "${network_dockerfile}" && pass 'charon-systemd is version-pinned' || fail 'charon-systemd package is not pinned'
[[ -x ${REPO_ROOT}/images/network-node/entrypoint.sh ]] && pass 'network startup is executable' || fail 'network startup is not executable'
[[ -x ${REPO_ROOT}/images/network-node/healthcheck.sh ]] && pass 'network healthcheck is executable' || fail 'network healthcheck is not executable'

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  for image in "${NETLAB_NETWORK_IMAGE}" "${NETLAB_CLIENT_IMAGE}" "${NETLAB_SERVICE_IMAGE}"; do
    docker image inspect "${image}" >/dev/null 2>&1 && pass "runtime image exists: ${image}" || fail "runtime image missing: ${image}"
  done
  printf 'runtime verification completed against local Docker images\n'
else
  printf '[BLOCKED] Docker CLI/daemon unavailable; runtime image inspection and clean-build comparison were not run.\n'
fi

if (( failures > 0 )); then
  printf 'image verification failed: %d static check(s) failed\n' "${failures}" >&2
  exit 1
fi
printf 'image verification passed: static checks complete\n'
