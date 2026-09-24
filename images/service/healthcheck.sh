#!/usr/bin/env bash
# IMG-020/SVC-160 source: service role health assertion.
set -Eeuo pipefail
[[ -f /run/netlab/services-configured ]] || { echo 'service config not applied' >&2; exit 1; }
case "${NETLAB_SERVICE:-}" in
  dhcp) pgrep -x kea-dhcp4 >/dev/null ;;
  dns|internet-dns) pgrep -x named >/dev/null ;;
  ntp|internet-ntp) pgrep -x chronyd >/dev/null ;;
  aaa) pgrep -x freeradius >/dev/null ;;
  *) echo 'unknown service role' >&2; exit 1 ;;
esac
printf 'healthy service=%s\n' "${NETLAB_SERVICE}"
