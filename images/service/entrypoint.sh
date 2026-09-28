#!/usr/bin/env bash
# IMG-020/SVC-160 source: service role startup.
set -Eeuo pipefail
if [[ $# -gt 0 ]]; then
  exec "$@"
fi

service=${NETLAB_SERVICE:-}
case "${service}" in
  dhcp|dns|ntp|internet-dns|internet-ntp)
    data_interface=${NETLAB_DATA_INTERFACE:-eth1}
    until [[ -e "/sys/class/net/${data_interface}" ]]; do
      sleep 0.2
    done
    ;;
esac
if [[ "${service}" == dhcp ]]; then
  mkdir -p /run/kea /var/lib/kea
  chown _kea:_kea /run/kea /var/lib/kea
fi
if [[ "${service}" == observability ]]; then
  service_port=${SERVICE_PORT:-8080}
  service_root=/var/lib/netlab/service
  mkdir -p "${service_root}"
  printf 'netlab service healthy\n' > "${service_root}/health"
  exec python3 -m http.server "${service_port}" --bind 0.0.0.0 --directory "${service_root}"
fi
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
stopping=0
daemon_pid=
stop_daemon() {
  stopping=1
  [[ -z "${daemon_pid}" ]] || kill "${daemon_pid}" 2>/dev/null || true
}
trap stop_daemon TERM INT
while (( ! stopping )); do
  "${daemon[@]}" &
  daemon_pid=$!
  for _ in {1..50}; do
    kill -0 "${daemon_pid}" 2>/dev/null || break
    sleep 0.1
  done
  if ! kill -0 "${daemon_pid}" 2>/dev/null; then
    wait "${daemon_pid}" || true
    daemon_pid=
    echo "${service} daemon exited; retrying in 1s" >&2
    sleep 1
    continue
  fi
  echo "service=${service} pid=${daemon_pid}"
  wait "${daemon_pid}" || true
  daemon_pid=
  (( stopping )) && break
  echo "${service} daemon exited; retrying in 1s" >&2
  sleep 1
done
