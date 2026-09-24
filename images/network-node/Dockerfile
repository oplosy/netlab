# IMG-020 source: role image definition. Build with scripts/images/build.sh.
ARG UBUNTU_BASE_IMAGE=ubuntu:24.04@sha256:a61567bd31828687156d735ea8eb01ba4e37636e225dd6a48ba94136a70d9d61
FROM ${UBUNTU_BASE_IMAGE}

ARG UBUNTU_SNAPSHOT=20260905T000000Z
ARG FRR_VERSION=8.4.4-1.1ubuntu6.7
ARG OVS_VERSION=3.3.9-0ubuntu0.24.04.1
ARG KEEPALIVED_VERSION=1:2.2.8-1build2
ARG STRONGSWAN_VERSION=5.9.13-2ubuntu4.24.04.4
ARG NFTABLES_VERSION=1.0.9-1ubuntu0.1

LABEL org.opencontainers.image.title="netlab network node" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.description="FRR, Open vSwitch, Keepalived, strongSwan and nftables" \
      netlab.component.frr="${FRR_VERSION}" \
      netlab.component.openvswitch="${OVS_VERSION}" \
      netlab.component.keepalived="${KEEPALIVED_VERSION}" \
      netlab.component.strongswan="${STRONGSWAN_VERSION}" \
      netlab.component.nftables="${NFTABLES_VERSION}" \
      netlab.ubuntu.snapshot="${UBUNTU_SNAPSHOT}"

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The snapshot timestamp is part of the lock. No moving archive or package
# channel is used. The package versions are recorded at build time.
RUN rm -f /etc/apt/sources.list.d/ubuntu.sources \
    && printf '%s\n' \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble main universe" \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble-updates main universe" \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble-security main universe" \
       > /etc/apt/sources.list.d/netlab-snapshot.list \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install --no-install-recommends --yes ca-certificates \
    && rm -f /etc/apt/sources.list.d/netlab-snapshot.list \
    && printf '%s\n' \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble main universe" \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble-updates main universe" \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble-security main universe" \
       > /etc/apt/sources.list.d/netlab-snapshot.list \
    && apt-get update \
    && apt-get install --no-install-recommends --yes \
       bash ca-certificates iproute2 iproute2-doc kmod procps psmisc tini \
       frr=${FRR_VERSION} frr-pythontools=${FRR_VERSION} \
       openvswitch-switch=${OVS_VERSION} openvswitch-common=${OVS_VERSION} \
       keepalived=${KEEPALIVED_VERSION} \
       charon-systemd=${STRONGSWAN_VERSION} strongswan-swanctl=${STRONGSWAN_VERSION} \
       libstrongswan-standard-plugins=${STRONGSWAN_VERSION} \
       nftables=${NFTABLES_VERSION} \
    && test "$(dpkg-query -W -f='${Version}' frr)" = "${FRR_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' openvswitch-switch)" = "${OVS_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' keepalived)" = "${KEEPALIVED_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' charon-systemd)" = "${STRONGSWAN_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' libstrongswan-standard-plugins)" = "${STRONGSWAN_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' nftables)" = "${NFTABLES_VERSION}" \
    && mkdir -p /run/netlab /var/log/netlab /usr/share/netlab \
    && { \
         printf 'frr=%s\n' "$(dpkg-query -W -f='${Version}' frr)"; \
         printf 'openvswitch=%s\n' "$(dpkg-query -W -f='${Version}' openvswitch-switch)"; \
         printf 'keepalived=%s\n' "$(dpkg-query -W -f='${Version}' keepalived)"; \
         printf 'strongswan=%s\n' "$(dpkg-query -W -f='${Version}' charon-systemd)"; \
         printf 'strongswan-standard-plugins=%s\n' "$(dpkg-query -W -f='${Version}' libstrongswan-standard-plugins)"; \
         printf 'nftables=%s\n' "$(dpkg-query -W -f='${Version}' nftables)"; \
       } > /usr/share/netlab/component-versions \
    && rm -rf /var/lib/apt/lists/*

COPY images/network-node/entrypoint.sh /usr/local/bin/netlab-network-entrypoint
COPY images/network-node/healthcheck.sh /usr/local/bin/netlab-network-healthcheck
RUN chmod 0755 /usr/local/bin/netlab-network-entrypoint /usr/local/bin/netlab-network-healthcheck

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD ["/usr/local/bin/netlab-network-healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/netlab-network-entrypoint"]
