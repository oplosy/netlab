# ADR 0009: Correlate Metrics, Logs, Flows, and Packets

- Status: Accepted
- Date: 2026-09-21

## Decision

Use:

- Prometheus plus `snmp_exporter` for SNMPv3 metrics
- Grafana for dashboards
- syslog-ng, Grafana Alloy, and Loki for logs
- Open vSwitch IPFIX, GoFlow2, Alloy, and Loki for flow records
- tcpdump and tshark for deterministic packet evidence

All verification artifacts share a run ID and synchronized timestamps. A result
is accepted only when active test output, route/protocol state, packet evidence,
and telemetry agree.

## Consequences

- No single dashboard is treated as authoritative proof.
- The stack is larger than a metrics-only design but covers all requested
  observability modes.
- Time synchronization and consistent labels are mandatory dependencies.
