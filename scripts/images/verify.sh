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
  remove_line=$(grep -nF 'rm -f /etc/apt/sources.list.d/ubuntu.sources' "${dockerfile}" | head -n1 | cut -d: -f1 || true)
  source_line=$(grep -nF 'https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT}' "${dockerfile}" | head -n1 | cut -d: -f1 || true)
  bootstrap_update_line=$(grep -nF 'apt-get -o Acquire::https::Verify-Peer=false update' "${dockerfile}" | head -n1 | cut -d: -f1 || true)
  bootstrap_install_line=$(grep -nF 'apt-get -o Acquire::https::Verify-Peer=false install --no-install-recommends --yes ca-certificates' "${dockerfile}" | head -n1 | cut -d: -f1 || true)
  normal_update_line=$(grep -nF 'apt-get update' "${dockerfile}" | head -n1 | cut -d: -f1 || true)
  verify_peer_count=$(grep -Fc 'Acquire::https::Verify-Peer=false' "${dockerfile}" || true)
  forbidden_source=0
  if grep -Eiq 'http://snapshot\.ubuntu\.com|archive\.ubuntu\.com|security\.ubuntu\.com|trusted=yes|allow-unauthenticated' "${dockerfile}"; then forbidden_source=1; fi
  if [[ ${remove_line} =~ ^[0-9]+$ && ${source_line} =~ ^[0-9]+$ && ${bootstrap_update_line} =~ ^[0-9]+$ && ${bootstrap_install_line} =~ ^[0-9]+$ && ${normal_update_line} =~ ^[0-9]+$ && ${remove_line} -lt ${source_line} && ${source_line} -lt ${bootstrap_update_line} && ${bootstrap_update_line} -lt ${bootstrap_install_line} && ${bootstrap_install_line} -lt ${normal_update_line} && ${verify_peer_count} -eq 2 && ${forbidden_source} -eq 0 ]]; then
    pass "${image} scopes TLS peer bypass to pinned CA bootstrap only"
  else
    fail "${image} must use pinned HTTPS snapshot with scoped CA bootstrap TLS bypass"
  fi
done

network_dockerfile=${REPO_ROOT}/images/network-node/Dockerfile
metadata_dir_line=$(grep -nF 'mkdir -p /run/netlab /var/log/netlab /usr/share/netlab' "${network_dockerfile}" | head -n1 | cut -d: -f1 || true)
metadata_redirect_line=$(grep -nF '} > /usr/share/netlab/component-versions' "${network_dockerfile}" | head -n1 | cut -d: -f1 || true)
if [[ ${metadata_dir_line} =~ ^[0-9]+$ && ${metadata_redirect_line} =~ ^[0-9]+$ && ${metadata_dir_line} -lt ${metadata_redirect_line} ]]; then
  pass 'component evidence directory is created before metadata write'
else
  fail 'component evidence directory must be created before metadata write'
fi
FRR_SNAPSHOT_VERSION=8.4.4-1.1ubuntu6.7
[[ ${FRR_VERSION} == "${FRR_SNAPSHOT_VERSION}" ]] && pass 'FRR lock matches the pinned Noble snapshot' || fail "FRR lock must be ${FRR_SNAPSHOT_VERSION} for ${UBUNTU_SNAPSHOT}"
grep -Fq "ARG FRR_VERSION=${FRR_SNAPSHOT_VERSION}" "${network_dockerfile}" && pass 'Dockerfile FRR default matches snapshot lock' || fail 'Dockerfile FRR default drifted from snapshot lock'
grep -Fq "| FRR | \`${FRR_SNAPSHOT_VERSION}\`" "${REPO_ROOT}/docs/runbooks/images.md" && pass 'runbook FRR pin matches snapshot lock' || fail 'runbook FRR pin drifted from snapshot lock'
NFTABLES_SNAPSHOT_VERSION=1.0.9-1ubuntu0.1
[[ ${NFTABLES_VERSION} == "${NFTABLES_SNAPSHOT_VERSION}" ]] && pass 'nftables lock matches the pinned Noble snapshot' || fail "nftables lock must be ${NFTABLES_SNAPSHOT_VERSION} for ${UBUNTU_SNAPSHOT}"
grep -Fq "ARG NFTABLES_VERSION=${NFTABLES_SNAPSHOT_VERSION}" "${network_dockerfile}" && pass 'Dockerfile nftables default matches snapshot lock' || fail 'Dockerfile nftables default drifted from snapshot lock'
grep -Fq "| nftables | \`${NFTABLES_SNAPSHOT_VERSION}\`" "${REPO_ROOT}/docs/runbooks/images.md" && pass 'runbook nftables pin matches snapshot lock' || fail 'runbook nftables pin drifted from snapshot lock'
grep -Fq 'OVS_DATAPATH_MODE' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'OVS datapath selection is explicit' || fail 'OVS datapath selection missing'
grep -Fq 'ovs-datapath.json' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'OVS datapath evidence is written' || fail 'OVS datapath evidence missing'
grep -Fq 'OVS_DATAPATH_MODE must be kernel or userspace' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'invalid OVS mode is rejected (negative check)' || fail 'invalid OVS mode rejection missing'
grep -Fq 'netlab.component.frr="${FRR_VERSION}"' "${network_dockerfile}" && grep -Fq 'frr=${FRR_VERSION}' "${network_dockerfile}" && pass 'FRR package lock matches label' || fail 'FRR label/package lock mismatch'
grep -Fq 'netlab.component.openvswitch="${OVS_VERSION}"' "${network_dockerfile}" && grep -Fq 'openvswitch-switch=${OVS_VERSION}' "${network_dockerfile}" && pass 'OVS package lock matches label' || fail 'OVS label/package lock mismatch'
grep -Fq 'netlab.component.keepalived="${KEEPALIVED_VERSION}"' "${network_dockerfile}" && grep -Fq 'keepalived=${KEEPALIVED_VERSION}' "${network_dockerfile}" && pass 'Keepalived package lock matches label' || fail 'Keepalived label/package lock mismatch'
grep -Fq 'netlab.component.strongswan="${STRONGSWAN_VERSION}"' "${network_dockerfile}" && grep -Eq '(^|[[:space:]])(charon-systemd|strongswan-swanctl)=\$\{STRONGSWAN_VERSION\}([[:space:]]|$)' "${network_dockerfile}" && pass 'strongSwan package lock matches label' || fail 'strongSwan label/package lock mismatch'
grep -Fq 'libstrongswan-standard-plugins=${STRONGSWAN_VERSION}' "${network_dockerfile}" && pass 'standard plugin package is version-pinned' || fail 'standard plugin package is missing or unpinned'
grep -Fq "dpkg-query -W -f='\${Version}' libstrongswan-standard-plugins" "${network_dockerfile}" && pass 'standard plugin version is checked during build' || fail 'standard plugin build-time version check missing'
grep -Fq "printf 'strongswan-standard-plugins=%s\\n'" "${network_dockerfile}" && pass 'standard plugin version is recorded in component manifest' || fail 'standard plugin component evidence missing'
grep -Fq 'netlab.component.nftables="${NFTABLES_VERSION}"' "${network_dockerfile}" && grep -Fq 'nftables=${NFTABLES_VERSION}' "${network_dockerfile}" && pass 'nftables package lock matches label' || fail 'nftables label/package lock mismatch'
grep -Fq 'charon-systemd=${STRONGSWAN_VERSION}' "${network_dockerfile}" && pass 'charon-systemd is version-pinned' || fail 'charon-systemd package is not pinned'
grep -Fq "dpkg-query -W -f='\${Version}' charon-systemd" "${network_dockerfile}" && pass 'component manifest records charon-systemd' || fail 'component manifest does not record charon-systemd'
grep -Fq '/usr/sbin/charon-systemd' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'charon-systemd uses the Noble binary path' || fail 'charon-systemd binary path is incorrect'
if grep -Eq '(^|[[:space:]])strongswan=\$\{STRONGSWAN_VERSION\}([[:space:]]|$)' "${network_dockerfile}"; then fail 'strongswan metapackage must not be installed'; else pass 'strongswan metapackage is excluded'; fi
grep -Fq 'edge|distribution|router|access' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'router role is accepted' || fail 'router role validation missing'
grep -Fq 'router)' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'router starts FRR and nftables path' || fail 'router startup path missing'
grep -Fq 'router)' "${REPO_ROOT}/images/network-node/healthcheck.sh" && pass 'router health path is present' || fail 'router health path missing'
grep -Fq 'has_keepalived_config' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'Keepalived startup is conditional on meaningful config' || fail 'conditional Keepalived startup missing'
grep -Fq 'if has_keepalived_config; then pgrep -x keepalived' "${REPO_ROOT}/images/network-node/healthcheck.sh" && pass 'Keepalived health is conditional on meaningful config' || fail 'conditional Keepalived health missing'
grep -Fq "[^#[:space:]]" "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'non-comment Keepalived config is treated as required' || fail 'Keepalived config meaningful-content check missing'
grep -Fq 'Keepalived config absent or empty' "${REPO_ROOT}/images/network-node/entrypoint.sh" && pass 'distribution skeleton skips absent Keepalived config' || fail 'distribution skeleton skip evidence missing'
if grep -Eq 'router\).*keepalived|router\).*charon-systemd' "${REPO_ROOT}/images/network-node/entrypoint.sh"; then fail 'router role has forbidden Keepalived/strongSwan startup'; else pass 'router role excludes Keepalived and strongSwan'; fi
[[ -x ${REPO_ROOT}/images/network-node/entrypoint.sh ]] && pass 'network startup is executable' || fail 'network startup is not executable'
[[ -x ${REPO_ROOT}/images/network-node/healthcheck.sh ]] && pass 'network healthcheck is executable' || fail 'network healthcheck is not executable'

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  for image in "${NETLAB_NETWORK_IMAGE}" "${NETLAB_CLIENT_IMAGE}" "${NETLAB_SERVICE_IMAGE}"; do
    docker image inspect "${image}" >/dev/null 2>&1 && pass "runtime image exists: ${image}" || fail "runtime image missing: ${image}"
  done
  docker run --rm --entrypoint sh "${NETLAB_NETWORK_IMAGE}" -ec \
    'test -f /usr/lib/ipsec/plugins/libstrongswan-openssl.so && openssl ecparam -name secp384r1 -genkey -noout >/dev/null' \
    && pass 'runtime image has the locked OpenSSL provider and ECP-384 support' \
    || fail 'runtime OpenSSL provider/ECP-384 check failed'
  printf 'runtime verification completed against local Docker images\n'
else
  printf '[BLOCKED] Docker CLI/daemon unavailable; runtime image inspection and clean-build comparison were not run.\n'
fi

if (( failures > 0 )); then
  printf 'image verification failed: %d static check(s) failed\n' "${failures}" >&2
  exit 1
fi
printf 'image verification passed: static checks complete\n'
