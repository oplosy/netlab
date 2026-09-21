# Security and Observability Architecture

## Security zones

The forwarding policy uses five zones:

- `users`: may reach approved server services, shared services, other corporate
  sites, and simulated Internet services.
- `servers`: accepts only explicitly published service ports; unsolicited
  server-to-user initiation is denied.
- `guest`: may use DHCP, approved DNS and NTP, and NAT to the simulated
  Internet. All enterprise, in-band management, and OOB destinations are denied.
- `inband-mgmt`: reachable only from approved operations sources.
- `oob-mgmt`: physically separate; no data-plane route exists from any VLAN.

nftables implements stateful policy. Each chain has a default-deny terminal
rule and rate-limited logging for new denied flows. Security tests must assert
both permits and denies.

The distribution pair enforces inter-VLAN and management segmentation. The site
edge enforces outside policy, IKE/ESP admission, and guest source NAT. Policy is
not duplicated at arbitrary intermediate interfaces.

## Control-plane protection

- SSH, SNMPv3, and administrative HTTP endpoints listen on OOB addresses.
- Routing protocol traffic is accepted only on named peer interfaces.
- eBGP import accepts only the default route in Phase 1 and enforces a maximum
  prefix limit.
- eBGP export accepts only exact, documented lab endpoint prefixes.
- OSPF is passive by default and enabled only on explicit routed links or XFRM
  interfaces.
- A local break-glass account remains available when central AAA is down. Its
  secret is generated locally and excluded from Git.

## IPsec profile

- IKE version: IKEv2 only
- Configuration interface: `swanctl`/VICI; legacy `ipsec.conf` is not used
- Authentication: certificates from a generated lab CA
- Tunnel model: route-based Linux XFRM interface
- Encryption: AES-256-GCM
- Key agreement: ECDH P-384
- Rekeying: enabled and tested without adjacency loss beyond the convergence
  objective
- Routing: OSPF Area 0 over the XFRM interface

Private keys, CA state, and issued credentials are runtime artifacts. The
repository contains generation scripts and examples, never live credentials.

## Operations services

| Capability | Selected component | Transport/security |
|---|---|---|
| DHCP | Kea DHCPv4 | Relay from gateway pairs |
| DNS | BIND 9 | Internal zone plus recursive cache |
| Time | chrony | OOB-restricted clients |
| AAA | FreeRADIUS with PAM integration | OOB only; local break-glass fallback |
| Syslog | syslog-ng to files, Grafana Alloy to Loki | TLS where supported |
| Metrics | Prometheus and `snmp_exporter` | SNMPv3 `authPriv` only |
| Flow telemetry | Open vSwitch IPFIX to GoFlow2, then Alloy/Loki | IPFIX v10 on OOB |
| Dashboards | Grafana | OOB only |

No SNMPv2c community is permitted. Default credentials are prohibited even in
the lab.

## Evidence model

Every verification run receives a unique run ID. Raw output is written below
`artifacts/runs/<run-id>/`, which is ignored by Git. The run includes:

- resolved dependency and image versions
- topology inspection output
- relevant route, neighbor, VRRP, LACP, and RSTP state
- timestamped traffic-generator output
- failure injection timestamps and measured convergence
- filtered packet captures
- security permit/deny results
- links or exports for matching telemetry panels

A curated report is committed under `evidence/reports/`. Small, sanitized packet
captures may be committed under `evidence/pcaps/`; each must be below 5 MiB and
must be checked for credentials or sensitive payloads first.

## Quantitative acceptance objectives

These are upper bounds, not assumed results. The evidence report records the
observed value and test method.

| Event | Objective |
|---|---:|
| One LACP member fails | no more than 1 second traffic interruption |
| Active RSTP path fails | no more than 5 seconds traffic interruption |
| One routed site-core link fails | no more than 3 seconds with BFD |
| Phase 2 ISP/overlay path fails | no more than 10 seconds |

If the objective is missed, the test fails. The result must not be rewritten as
success merely because connectivity eventually returns.
