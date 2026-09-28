#!/usr/bin/env bash
# IMG-020/SVC-160 source: service role health assertion.
set -Eeuo pipefail
if [[ "${NETLAB_SERVICE:-}" == observability ]]; then
  service_port=${SERVICE_PORT:-8080}
  curl --fail --silent --show-error "http://127.0.0.1:${service_port}/health" >/dev/null
  printf 'healthy service port=%s\n' "${service_port}"
  exit 0
fi
[[ -f /run/netlab/services-configured ]] || { echo 'service config not applied' >&2; exit 1; }
case "${NETLAB_SERVICE:-}" in
  dhcp) pgrep -x kea-dhcp4 >/dev/null ;;
  dns|internet-dns) pgrep -x named >/dev/null ;;
  ntp|internet-ntp) pgrep -x chronyd >/dev/null ;;
  aaa) pgrep -x freeradius >/dev/null ;;
  *) echo 'unknown service role' >&2; exit 1 ;;
esac
printf 'healthy service=%s\n' "${NETLAB_SERVICE}"
