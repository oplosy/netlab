# IMG-020 source: service role image definition.
ARG UBUNTU_BASE_IMAGE=ubuntu:24.04@sha256:a61567bd31828687156d735ea8eb01ba4e37636e225dd6a48ba94136a70d9d61
FROM ${UBUNTU_BASE_IMAGE}
ARG UBUNTU_SNAPSHOT=20260905T000000Z
LABEL org.opencontainers.image.title="netlab service" \
      org.opencontainers.image.version="0.1.0" \
      netlab.ubuntu.snapshot="${UBUNTU_SNAPSHOT}"
ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN rm -f /etc/apt/sources.list.d/ubuntu.sources \
    && printf '%s\n' \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble main universe" \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble-updates main universe" \
       > /etc/apt/sources.list.d/netlab-snapshot.list \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install --no-install-recommends --yes ca-certificates \
    && rm -f /etc/apt/sources.list.d/netlab-snapshot.list \
    && printf '%s\n' \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble main universe" \
       "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${UBUNTU_SNAPSHOT} noble-updates main universe" \
       > /etc/apt/sources.list.d/netlab-snapshot.list \
    && apt-get update \
    && apt-get install --no-install-recommends --yes bash ca-certificates curl iproute2 python3 \
    && rm -rf /var/lib/apt/lists/*
COPY images/service/entrypoint.sh /usr/local/bin/netlab-service-entrypoint
COPY images/service/healthcheck.sh /usr/local/bin/netlab-service-healthcheck
RUN chmod 0755 /usr/local/bin/netlab-service-entrypoint /usr/local/bin/netlab-service-healthcheck
EXPOSE 8080/tcp
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD ["/usr/local/bin/netlab-service-healthcheck"]
ENTRYPOINT ["/usr/local/bin/netlab-service-entrypoint"]
