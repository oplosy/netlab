#!/usr/bin/env bash
# Read-only host preflight for the dedicated WSL2 lab environment.
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)
VERSION_FILE=${PREFLIGHT_VERSIONS_FILE:-${REPO_ROOT}/versions.env}
if [[ ! -r ${VERSION_FILE} ]]; then
  printf '[FAIL] version policy: cannot read %s\n' "${VERSION_FILE}" >&2
  exit 2
fi
# shellcheck disable=SC1090
source "${VERSION_FILE}"

FAILED=0
CHECKS=0
pass() { CHECKS=$((CHECKS + 1)); printf '[PASS] %s: %s\n' "$1" "$2"; }
fail() {
  CHECKS=$((CHECKS + 1)); FAILED=$((FAILED + 1))
  printf '[FAIL] %s: %s\n' "$1" "$2"
  [[ -n ${3:-} ]] && printf '       action: %s\n' "$3"
}
version_ge() {
  [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" == "$2" ]]
}
extract_version() { sed -nE 's/[^0-9]*([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | head -n1; }
check_exact_version() {
  local label=$1 actual=$2 expected=$3 action=$4
  if [[ -z ${actual} ]]; then
    fail "${label}" "version could not be read (expected ${expected})" "${action}"
  elif [[ ${actual} == "${expected}" ]]; then
    pass "${label}" "${actual}"
  else
    fail "${label}" "found ${actual}; policy requires ${expected}" "${action}"
  fi
}

printf 'netlab environment preflight (ENV_POLICY_VERSION=%s)\n' "${ENV_POLICY_VERSION}"
printf 'repository: %s\n' "${REPO_ROOT}"

kernel_release=$(uname -r 2>/dev/null || true)
if [[ ${kernel_release,,} == *microsoft* || ${kernel_release,,} == *wsl2* ]]; then
  if [[ -n ${WSL_DISTRO_NAME:-} ]]; then
    pass 'WSL2' "${WSL_DISTRO_NAME} (${kernel_release})"
  else
    fail 'WSL2' 'WSL kernel detected but WSL_DISTRO_NAME is empty' \
      'run this command inside the dedicated WSL2 distribution'
  fi
else
  fail 'WSL2' "kernel is not identified as WSL2 (${kernel_release:-unavailable})" \
    'run the command inside the dedicated WSL2 distribution'
fi

if [[ -z ${kernel_release} ]]; then
  fail 'Linux kernel' 'uname -r returned no value' 'ensure the WSL2 kernel is running'
else
  kernel_version=$(printf '%s' "${kernel_release}" | extract_version)
  if [[ -n ${kernel_version} ]] && version_ge "${kernel_version}" "${WSL_KERNEL_MIN_VERSION}"; then
    pass 'Linux kernel version' "${kernel_version} (minimum ${WSL_KERNEL_MIN_VERSION})"
  else
    fail 'Linux kernel version' "found ${kernel_version:-unreadable}; minimum is ${WSL_KERNEL_MIN_VERSION}" \
      'update the WSL2 kernel, then restart the distribution'
  fi
fi

if command -v uv >/dev/null 2>&1; then
  uv_version=$(uv --version 2>/dev/null || true)
  uv_version=$(printf '%s' "${uv_version}" | extract_version)
  check_exact_version 'uv' "${uv_version}" "${UV_VERSION}" \
    'install the pinned uv release inside WSL2; see docs/runbooks/environment.md'
else
  fail 'uv command' 'uv is not installed or not on PATH' \
    'install the pinned uv release inside WSL2; see docs/runbooks/environment.md'
fi

if command -v docker >/dev/null 2>&1; then
  docker_server_version=$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)
  docker_server_version=$(printf '%s' "${docker_server_version}" | extract_version)
  check_exact_version 'Docker Engine' "${docker_server_version}" "${DOCKER_ENGINE_VERSION}" \
    'install the pinned Docker Engine inside WSL2'
  # docker version requires a reachable daemon even for the client field.
  # Read the CLI's own version first so a daemon outage is reported as a
  # daemon outage, not as a missing CLI.
  docker_client_version=$(docker --version 2>/dev/null || true)
  docker_client_version=$(printf '%s' "${docker_client_version}" | extract_version)
  check_exact_version 'Docker CLI' "${docker_client_version}" "${DOCKER_CLI_VERSION}" \
    'install the pinned Docker CLI inside WSL2'
  docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
  if [[ -n ${docker_root} && -d ${docker_root} ]]; then
    pass 'Docker daemon reachability' "root ${docker_root}"
  else
    # Some Docker wrappers print diagnostics to stdout. Never pass that text
    # to df as though it were a filesystem path.
    docker_root=''
    fail 'Docker daemon reachability' 'docker info failed or returned no usable Docker root' \
      'start Docker Engine inside WSL2 and ensure the current user can access its socket'
  fi
else
  fail 'Docker command' 'docker is not installed or not on PATH' \
    'install the pinned Docker Engine and CLI inside WSL2'
  fail 'Docker daemon reachability' 'cannot check without the docker command' \
    'install the pinned Docker Engine inside WSL2'
fi

if command -v containerlab >/dev/null 2>&1; then
  clab_output=$(containerlab version 2>/dev/null || true)
  clab_version=$(printf '%s\n' "${clab_output}" | extract_version)
  check_exact_version 'Containerlab' "${clab_version}" "${CONTAINERLAB_VERSION}" \
    'install the pinned Containerlab release inside WSL2'
else
  fail 'Containerlab command' 'containerlab is not installed or not on PATH' \
    'install the pinned Containerlab release inside WSL2'
fi

CONFIG_TEXT=''
if [[ -r /proc/config.gz ]] && command -v zcat >/dev/null 2>&1; then
  CONFIG_TEXT=$(zcat /proc/config.gz 2>/dev/null || true)
elif [[ -r /boot/config-${kernel_release} ]]; then
  CONFIG_TEXT=$(cat "/boot/config-${kernel_release}" 2>/dev/null || true)
fi
kernel_feature_available() {
  local feature=$1 module=${2:-}
  if [[ -n ${CONFIG_TEXT} ]] && grep -Eq "^${feature}=([ym])$" <<<"${CONFIG_TEXT}"; then
    return 0
  elif [[ -n ${module} && -d /sys/module/${module} ]]; then
    return 0
  elif [[ -n ${module} ]] && command -v modinfo >/dev/null 2>&1 &&
    modinfo "${module}" >/dev/null 2>&1; then
    # A built-in or available module need not be loaded in this namespace.
    # modinfo is read-only; preflight never calls modprobe or insmod.
    return 0
  elif [[ ${feature} == CONFIG_NET_NS && -e /proc/self/ns/net ]]; then
    return 0
  fi
  return 1
}
declare -A FEATURE_MODULES=(
  [CONFIG_NET_NS]='' [CONFIG_VETH]='veth' [CONFIG_BRIDGE]='bridge'
  [CONFIG_BRIDGE_VLAN_FILTERING]='bridge' [CONFIG_VLAN_8021Q]='8021q'
  [CONFIG_NETFILTER]='' [CONFIG_NF_TABLES]='nf_tables'
  [CONFIG_XFRM_INTERFACE]='xfrm_interface' [CONFIG_XFRM_USER]='xfrm_user'
  [CONFIG_OPENVSWITCH]='openvswitch'
)
while IFS= read -r feature; do
  [[ -z ${feature} ]] && continue
  module=${FEATURE_MODULES[${feature}]:-}
  if kernel_feature_available "${feature}" "${module}"; then
    pass "Kernel feature ${feature}" 'available (no module was loaded)'
  else
    detail='not enabled in kernel config and unavailable via /sys/module or modinfo'
    [[ -n ${CONFIG_TEXT} ]] && detail='not enabled in kernel config and unavailable via /sys/module or modinfo'
    fail "Kernel feature ${feature}" "${detail}" \
      'use a WSL2 kernel with the required feature enabled; preflight never loads it'
  fi
done <<<"${REQUIRED_KERNEL_FEATURES}"

cpu_count=$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)
if [[ ${cpu_count} =~ ^[0-9]+$ ]] && (( cpu_count >= MIN_CPU_COUNT )); then
  pass 'CPU' "${cpu_count} vCPU (minimum ${MIN_CPU_COUNT})"
else
  fail 'CPU' "${cpu_count:-unreadable} vCPU (minimum ${MIN_CPU_COUNT})" \
    'assign at least the minimum processors to the WSL2 distribution'
fi
memory_kib=$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)
memory_required_kib=$((MIN_MEMORY_GIB * 1024 * 1024))
if [[ ${memory_kib} =~ ^[0-9]+$ ]] && (( memory_kib >= memory_required_kib )); then
  pass 'Memory' "$((memory_kib / 1024 / 1024)) GiB (minimum ${MIN_MEMORY_GIB} GiB)"
else
  fail 'Memory' "$((memory_kib / 1024 / 1024)) GiB (minimum ${MIN_MEMORY_GIB} GiB)" \
    'assign more memory to the WSL2 distribution'
fi
disk_kib=$(df -Pk "${REPO_ROOT}" 2>/dev/null | awk 'NR==2 {print $4}')
disk_required_kib=$((MIN_DISK_GIB * 1024 * 1024))
if [[ ${disk_kib} =~ ^[0-9]+$ ]] && (( disk_kib >= disk_required_kib )); then
  pass 'Repository filesystem' "$((disk_kib / 1024 / 1024)) GiB free (minimum ${MIN_DISK_GIB} GiB)"
else
  fail 'Repository filesystem' "${disk_kib:-unreadable} KiB free (minimum ${MIN_DISK_GIB} GiB)" \
    'free disk space on the WSL2 filesystem containing this repository'
fi
if [[ -n ${docker_root:-} ]]; then
  docker_disk_kib=$(df -Pk "${docker_root}" 2>/dev/null | awk 'NR==2 {print $4}')
  if [[ ${docker_disk_kib} =~ ^[0-9]+$ ]] && (( docker_disk_kib >= disk_required_kib )); then
    pass 'Docker filesystem' "$((docker_disk_kib / 1024 / 1024)) GiB free (minimum ${MIN_DISK_GIB} GiB)"
  else
    fail 'Docker filesystem' "${docker_disk_kib:-unreadable} KiB free (minimum ${MIN_DISK_GIB} GiB)" \
      'free disk space on the Docker data filesystem inside WSL2'
  fi
fi
printf '\n%d checks completed: %d passed, %d failed\n' "${CHECKS}" "$((CHECKS - FAILED))" "${FAILED}"
if (( FAILED > 0 )); then
  printf 'Preflight failed. No host software or runtime state was changed.\n' >&2
  exit 1
fi
printf 'Preflight passed. No host software or runtime state was changed.\n'
