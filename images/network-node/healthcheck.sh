#!/usr/bin/env bash
# IMG-020 source: network-node health assertion.
set -Eeuo pipefail

ROLE=${NETLAB_NODE_ROLE:-edge}
OVS_BRIDGE=${OVS_BRIDGE:-netlab-br0}
test -s /run/netlab/ovs-datapath.json
ovs-vsctl --timeout=2 br-exists "${OVS_BRIDGE}"
test -s /usr/share/netlab/component-versions
pgrep -x ovs-vswitchd >/dev/null
if [[ -f /run/netlab/aaa-configured ]]; then pgrep -x sshd >/dev/null; fi
has_keepalived_config() {
  [[ -s /etc/keepalived/keepalived.conf ]] \
    && grep -Eq '^[[:space:]]*[^#[:space:]]' /etc/keepalived/keepalived.conf
}
case "${ROLE}" in
  access) : ;;
  edge)
    pgrep -x zebra >/dev/null
    (pgrep -x charon-systemd >/dev/null || pgrep -x charon >/dev/null)
    command -v swanctl >/dev/null
    nft list ruleset >/dev/null
    ;;
  distribution)
    pgrep -x zebra >/dev/null
    nft list ruleset >/dev/null
    if has_keepalived_config; then pgrep -x keepalived >/dev/null; fi
    ;;
  router)
    pgrep -x zebra >/dev/null
    nft list ruleset >/dev/null
    ;;
  *) exit 64 ;;
esac
printf 'healthy role=%s bridge=%s\n' "${ROLE}" "${OVS_BRIDGE}"
