# IMG-020 source: client role image definition.
ARG UBUNTU_BASE_IMAGE=ubuntu:24.04@sha256:a61567bd31828687156d735ea8eb01ba4e37636e225dd6a48ba94136a70d9d61
FROM ${UBUNTU_BASE_IMAGE}
ARG UBUNTU_SNAPSHOT=20260905T000000Z
LABEL org.opencontainers.image.title="netlab client" \
      org.opencontainers.image.version="0.1.0" \
      netlab.ubuntu.snapshot="${UBUNTU_SNAPSHOT}"
ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN rm -f /etc/apt/sources.list.d/ubuntu.sources \
    && printf '%s\n' \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble main universe" \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble-updates main universe" \
       > /etc/apt/sources.list.d/netlab-snapshot.list \
    && apt-get update \
    && apt-get install --no-install-recommends --yes bash ca-certificates iproute2 iputils-ping curl dnsutils iperf3 procps \
    && rm -rf /var/lib/apt/lists/*
COPY images/client/entrypoint.sh /usr/local/bin/netlab-client-entrypoint
COPY images/client/healthcheck.sh /usr/local/bin/netlab-client-healthcheck
RUN chmod 0755 /usr/local/bin/netlab-client-entrypoint /usr/local/bin/netlab-client-healthcheck
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD ["/usr/local/bin/netlab-client-healthcheck"]
ENTRYPOINT ["/usr/local/bin/netlab-client-entrypoint"]
