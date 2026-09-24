FROM ubuntu:24.04@sha256:a61567bd31828687156d735ea8eb01ba4e37636e225dd6a48ba94136a70d9d61
ARG DEBIAN_FRONTEND=noninteractive
ARG UBUNTU_SNAPSHOT=20260905T000000Z
ARG SNMPD_VERSION=5.9.4+dfsg-1.1ubuntu3.2
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources \
 && printf 'deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/%s noble main universe\ndeb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/%s noble-updates main universe\ndeb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/%s noble-security main universe\n' "$UBUNTU_SNAPSHOT" "$UBUNTU_SNAPSHOT" "$UBUNTU_SNAPSHOT" >/etc/apt/sources.list \
 && apt-get -o Acquire::https::Verify-Peer=false update \
 && apt-get -o Acquire::https::Verify-Peer=false install -y --no-install-recommends snmpd="$SNMPD_VERSION" snmp="$SNMPD_VERSION" \
 && rm -rf /var/lib/apt/lists/*
COPY agent-entrypoint.sh /usr/local/sbin/netlab-snmp-agent
RUN chmod 0755 /usr/local/sbin/netlab-snmp-agent && mkdir -p /var/lib/snmp
ENTRYPOINT ["/usr/local/sbin/netlab-snmp-agent"]
