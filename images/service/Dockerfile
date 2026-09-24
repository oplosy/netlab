# IMG-020 source: service role image definition.
ARG UBUNTU_BASE_IMAGE=ubuntu:24.04@sha256:a61567bd31828687156d735ea8eb01ba4e37636e225dd6a48ba94136a70d9d61
FROM ${UBUNTU_BASE_IMAGE}
ARG UBUNTU_SNAPSHOT=20260905T000000Z
ARG KEA_VERSION=2.4.1-3ubuntu0.2
ARG BIND9_VERSION=1:9.18.39-0ubuntu0.24.04.7
ARG BIND9_DNSUTILS_VERSION=1:9.18.39-0ubuntu0.24.04.7
ARG BIND9_UTILS_VERSION=1:9.18.39-0ubuntu0.24.04.7
ARG CHRONY_VERSION=4.5-1ubuntu4.2
ARG FREERADIUS_VERSION=3.2.5+dfsg-3~ubuntu24.04.3
ARG DHCP_CLIENT_VERSION=4.4.3-P1-4ubuntu2
ARG OPENSSH_CLIENT_VERSION=1:9.6p1-3ubuntu13.19
ARG SSHPASS_VERSION=1.09-1
LABEL org.opencontainers.image.title="netlab service" \
      org.opencontainers.image.version="0.1.0" \
      netlab.component.kea="${KEA_VERSION}" \
      netlab.component.bind9="${BIND9_VERSION}" \
      netlab.component.chrony="${CHRONY_VERSION}" \
      netlab.component.freeradius="${FREERADIUS_VERSION}" \
      netlab.component.sshpass="${SSHPASS_VERSION}" \
      netlab.ubuntu.snapshot="${UBUNTU_SNAPSHOT}"
ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
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
       bash ca-certificates curl iproute2 python3 \
       kea-dhcp4-server=${KEA_VERSION} \
       bind9=${BIND9_VERSION} bind9-dnsutils=${BIND9_DNSUTILS_VERSION} bind9-utils=${BIND9_UTILS_VERSION} \
       chrony=${CHRONY_VERSION} freeradius=${FREERADIUS_VERSION} freeradius-utils=${FREERADIUS_VERSION} \
       isc-dhcp-client=${DHCP_CLIENT_VERSION} openssh-client=${OPENSSH_CLIENT_VERSION} sshpass=${SSHPASS_VERSION} \
    && test "$(dpkg-query -W -f='${Version}' kea-dhcp4-server)" = "${KEA_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' bind9)" = "${BIND9_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' bind9-dnsutils)" = "${BIND9_DNSUTILS_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' bind9-utils)" = "${BIND9_UTILS_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' chrony)" = "${CHRONY_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' freeradius)" = "${FREERADIUS_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' isc-dhcp-client)" = "${DHCP_CLIENT_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' openssh-client)" = "${OPENSSH_CLIENT_VERSION}" \
    && test "$(dpkg-query -W -f='${Version}' sshpass)" = "${SSHPASS_VERSION}" \
    && mkdir -p /run/netlab /var/log/netlab /usr/share/netlab \
    && { \
         printf 'kea=%s\n' "$(dpkg-query -W -f='${Version}' kea-dhcp4-server)"; \
         printf 'bind9=%s\n' "$(dpkg-query -W -f='${Version}' bind9)"; \
         printf 'bind9-dnsutils=%s\n' "$(dpkg-query -W -f='${Version}' bind9-dnsutils)"; \
         printf 'bind9-utils=%s\n' "$(dpkg-query -W -f='${Version}' bind9-utils)"; \
         printf 'chrony=%s\n' "$(dpkg-query -W -f='${Version}' chrony)"; \
         printf 'freeradius=%s\n' "$(dpkg-query -W -f='${Version}' freeradius)"; \
         printf 'isc-dhcp-client=%s\n' "$(dpkg-query -W -f='${Version}' isc-dhcp-client)"; \
         printf 'openssh-client=%s\n' "$(dpkg-query -W -f='${Version}' openssh-client)"; \
         printf 'sshpass=%s\n' "$(dpkg-query -W -f='${Version}' sshpass)"; \
       } > /usr/share/netlab/component-versions \
    && rm -rf /var/lib/apt/lists/*
COPY images/service/entrypoint.sh /usr/local/bin/netlab-service-entrypoint
COPY images/service/healthcheck.sh /usr/local/bin/netlab-service-healthcheck
RUN chmod 0755 /usr/local/bin/netlab-service-entrypoint /usr/local/bin/netlab-service-healthcheck
HEALTHCHECK --interval=10s --timeout=5s --start-period=2m --retries=3 \
  CMD ["/usr/local/bin/netlab-service-healthcheck"]
ENTRYPOINT ["/usr/local/bin/netlab-service-entrypoint"]
