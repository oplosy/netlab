#!/usr/bin/env bash
# IMG-020 source: network-node role startup. Runtime state is ephemeral.
set -Eeuo pipefail

ROLE=${NETLAB_NODE_ROLE:-edge}
OVS_DATAPATH_MODE=${OVS_DATAPATH_MODE:-kernel}
OVS_BRIDGE=${OVS_BRIDGE:-netlab-br0}
RUN_DIR=/run/netlab

case "${ROLE}" in
  edge|distribution|access) ;;
  *) printf 'unsupported NETLAB_NODE_ROLE=%s\n' "${ROLE}" >&2; exit 64 ;;
esac
case "${OVS_DATAPATH_MODE}" in
  kernel) OVS_DATAPATH_TYPE=system ;;
  userspace) OVS_DATAPATH_TYPE=netdev ;;
  *) printf 'OVS_DATAPATH_MODE must be kernel or userspace\n' >&2; exit 64 ;;
esac

mkdir -p "${RUN_DIR}" /var/log/netlab /var/run/openvswitch /etc/openvswitch
if [[ ! -s /etc/openvswitch/conf.db ]]; then
  ovsdb-tool create /etc/openvswitch/conf.db /usr/share/openvswitch/vswitch.ovsschema
fi
ovsdb-server /etc/openvswitch/conf.db \
  --remote=punix:/var/run/openvswitch/db.sock \
  --remote=db:Open_vSwitch,Open_vSwitch,manager_options \
  --pidfile=/run/openvswitch/ovsdb-server.pid \
  --detach --log-file=/var/log/netlab/ovsdb-server.log
for _ in {1..50}; do
  [[ -S /var/run/openvswitch/db.sock ]] && break
  sleep 0.1
done
[[ -S /var/run/openvswitch/db.sock ]] || { echo 'OVSDB socket did not appear' >&2; exit 1; }
ovs-vsctl --no-wait init
ovs-vswitchd --pidfile=/run/openvswitch/ovs-vswitchd.pid \
  --detach --log-file=/var/log/netlab/ovs-vswitchd.log
ovs-vsctl --may-exist add-br "${OVS_BRIDGE}" \
  -- set bridge "${OVS_BRIDGE}" datapath_type="${OVS_DATAPATH_TYPE}"
printf '{"role":"%s","requested":"%s","ovs_datapath_type":"%s","bridge":"%s"}\n' \
  "${ROLE}" "${OVS_DATAPATH_MODE}" "${OVS_DATAPATH_TYPE}" "${OVS_BRIDGE}" \
  > "${RUN_DIR}/ovs-datapath.json"

start_required() {
  local name=$1
  shift
  command -v "${name}" >/dev/null 2>&1 || {
    printf 'required command %s is missing for role %s\n' "${name}" "${ROLE}" >&2
    exit 127
  }
  "$@"
}
start_background_required() {
  local name=$1
  shift
  "$@" >"/var/log/netlab/${name}.log" 2>&1 &
  local pid=$!
  sleep 0.2
  kill -0 "${pid}" 2>/dev/null || {
    printf '%s failed during startup; see /var/log/netlab/%s.log\n' "${name}" "${name}" >&2
    exit 1
  }
}

case "${ROLE}" in
  edge)
    [[ -x /usr/lib/frr/frrinit.sh ]] || { echo 'frrinit.sh is missing' >&2; exit 127; }
    /usr/lib/frr/frrinit.sh start
    [[ -x /usr/libexec/ipsec/charon-systemd ]] || { echo 'charon-systemd is missing' >&2; exit 127; }
    start_background_required charon-systemd /usr/libexec/ipsec/charon-systemd --nofork
    if [[ -f /etc/nftables.conf ]]; then nft -f /etc/nftables.conf; else nft list ruleset >/dev/null; fi
    ;;
  distribution)
    [[ -x /usr/lib/frr/frrinit.sh ]] || { echo 'frrinit.sh is missing' >&2; exit 127; }
    /usr/lib/frr/frrinit.sh start
    start_background_required keepalived keepalived --dont-fork --log-console
    if [[ -f /etc/nftables.conf ]]; then nft -f /etc/nftables.conf; else nft list ruleset >/dev/null; fi
    ;;
  access) : ;;
esac

printf 'role=%s ovs_datapath=%s (%s)\n' "${ROLE}" "${OVS_DATAPATH_MODE}" "${OVS_DATAPATH_TYPE}"
if [[ $# -gt 0 ]]; then
  exec "$@"
fi
exec tail -f /dev/null
