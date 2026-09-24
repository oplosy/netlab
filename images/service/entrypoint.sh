#!/usr/bin/env bash
# IMG-020/SVC-160 source: service role startup.
set -Eeuo pipefail
if [[ $# -gt 0 ]]; then
  exec "$@"
fi

service=${NETLAB_SERVICE:-}
case "${service}" in
  dhcp) daemon=(/usr/sbin/kea-dhcp4 -c /etc/kea/kea-dhcp4.conf -d) ;;
  dns|internet-dns) daemon=(/usr/sbin/named -g -c /etc/bind/named.conf) ;;
  ntp|internet-ntp) daemon=(/usr/sbin/chronyd -d -f /etc/chrony/chrony.conf) ;;
  aaa) daemon=(/usr/sbin/freeradius -f -l stdout) ;;
  *) echo "unsupported NETLAB_SERVICE=${service}" >&2; exit 64 ;;
esac

mkdir -p /run/netlab /var/log/netlab
echo "waiting for inventory-rendered configuration: ${service}"
until [[ -f /run/netlab/services-configured ]]; do sleep 1; done
"${daemon[@]}" &
daemon_pid=$!
cleanup() { kill "${daemon_pid}" 2>/dev/null || true; wait "${daemon_pid}" 2>/dev/null || true; }
trap cleanup EXIT TERM INT
for _ in {1..50}; do
  kill -0 "${daemon_pid}" 2>/dev/null || {
    wait "${daemon_pid}"
    echo "${service} daemon exited during startup" >&2
    exit 1
  }
  sleep 0.1
done
echo "service=${service} pid=${daemon_pid}"
wait "${daemon_pid}"
