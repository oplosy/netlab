#!/bin/sh
set -eu
node="$1"
address="$2"
test -r "/run/netlab-snmp/${node}.conf"
test -r "/run/netlab-snmp/${node}.users.conf"
if ! grep -q '^usmUser .* netlab ' /var/lib/snmp/snmpd.conf 2>/dev/null; then
  install -m 0600 "/run/netlab-snmp/${node}.users.conf" /var/lib/snmp/snmpd.conf
fi
exec snmpd -f -Lo -C -c "/var/lib/snmp/snmpd.conf,/run/netlab-snmp/${node}.conf" -p /run/snmpd.pid "udp:${address}:161"
