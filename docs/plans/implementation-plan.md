# Implementation Plan

This plan is execution-ready only after the repository has a real `main` base.
Task IDs, dependencies, path ownership, and acceptance criteria are also stored
in `plans/tasks.yaml` for machine consumption.

## Phase gates

| Phase | Outcome | Exit gate |
|---|---|---|
| 0 Foundation | Reproducible environment, images, inventory, topology skeleton | Empty deployment and teardown succeed |
| 1 HQ + Branch 1 | Complete switching, routing, VPN, services, security, telemetry | End-to-end evidence report passes |
| 2 Dual ISP | Redundant edge and provider paths | ISP failure converges within objective |
| 3 Branch 2 | Template-based site expansion | New site adds one summary and passes isolation tests |
| 4 SecureEdge | Explicit security service layer and IDS/IPS | Policy and detection tests pass |
| 5 Automation | NetBox projection, dynamic inventory, drift and CI | Clean rebuild and drift detection are automated |

## Phase 0: Foundation

### GOV-001 — Repository bootstrap and protection

- Owner: repository owner plus orchestrator
- Depends on: none
- Deliver: real `main`, branch protection, PR requirement, status checks, CODEOWNERS
- Accept: direct `main` push is rejected and a feature PR can be opened
- Serial: yes; all other implementation tasks depend on this

### ENV-010 — WSL and toolchain preflight

- Deliver: version policy, preflight script, environment runbook, lock file
- Lock: commit `pyproject.toml` and `uv.lock`; dependency installation remains an
  explicit user action and is never performed by preflight
- Deliver: root Makefile that includes task-owned `mk/*.mk` fragments
- Accept: script detects WSL2, Docker, Containerlab, required kernel features,
  CPU/RAM/disk minimums, and gives actionable failures
- Exclude: installing or removing host software without explicit approval

### IMG-020 — Reproducible node images

- Deliver role-specific network and client/service images with health checks
- Accept: pinned builds succeed twice, vulnerability inventory is recorded, and
  each component reports the expected version
- Important: use OVS userspace datapath only if the WSL kernel datapath is
  unavailable; record the selected mode in evidence rather than silently
  falling back

### IMG-025 — strongSwan ECP-384 crypto backend

- Depends on: IMG-020
- Deliver: install and verify the pinned `libstrongswan-standard-plugins` package that supplies the OpenSSL provider
- Accept: the network image loads ECP-384 and parses ECDSA P-384 certificates;
  the legacy starter daemon remains absent
- Required by: VPN-150; its accepted IKE proposal uses ECP-384

### DAT-030 — Authoritative inventory schema

- Deliver versioned site/node/link/VLAN/prefix/ASN/service data plus schema
- Accept: duplicate IPs, overlapping subnets, invalid peer references, and
  out-of-range allocations fail validation
- Parallel: may run with ENV-010 after GOV-001 and continue alongside IMG-020

### TOP-040 — Containerlab topology skeleton

- Depends on: ENV-010, IMG-020, DAT-030
- Deliver all Phase 1 nodes, deterministic interfaces, fixed OOB addresses, and
  lifecycle commands
- Accept: deploy, inspect, and destroy leave no orphaned project resources

## Phase 1: Headquarters and Branch 1

### L2-110 — VLAN, LACP, and RSTP switching

- Deliver VLAN trunks/access ports, two-member LACP bundles, RSTP root policy,
  and state verification
- Accept: VLAN isolation passes; removing one LACP member interrupts traffic for
  no more than 1 second; active RSTP-path failure recovers within 5 seconds
- Constraint: no bundle spans two distribution nodes

### L3-120 — Distribution gateways and VRRP

- Depends on: L2-110
- Deliver SVIs, VRRP virtual gateways, DHCP relay hooks, routed edge links, and
  gateway policy alignment
- Accept: clients use `.1`; master failure moves the virtual gateway; user,
  server, guest, and management routes remain isolated as designed

### WAN-140 — Simulated ISP and eBGP policy

- Depends on: TOP-040
- Deliver ISP routing, site eBGP sessions, simulated Internet host, exact prefix
  filters, and maximum-prefix protection
- Accept: sites learn only default; ISP never learns RFC1918 site prefixes;
  intentional leak tests are rejected
- Parallel: may run alongside L2-110 and L3-120 because paths do not overlap

### VPN-150 — IKEv2 XFRM overlay

- Depends on: L3-120, WAN-140, IMG-025
- Deliver lab PKI generation, site certificates, strongSwan configuration, XFRM
  links, MTU/MSS setting, and rekey/recovery tests
- Accept: XFRM peer addresses communicate only after IPsec is established;
  underlay capture shows ESP and no plaintext enterprise payload; rekey
  preserves the XFRM path within objective
- Status: complete; live HQ–BR1 results are recorded in
  `evidence/specs/ipsec/acceptance.md`

### OSPF-130 — Multi-area routing and summaries

- Depends on: L3-120, VPN-150
- Deliver Areas 0/10/20, passive defaults, BFD, site summaries, and conditional
  default origination
- Accept: no unwanted adjacency exists; Area 0 sees only site summaries; routed
  link failure converges within 3 seconds; partial-summary black holes are tested

### SVC-160 — Core infrastructure services

- Architecture: accepted ADR-0014 gives DNS/DHCP/NTP site-local data-plane
  addresses in VLAN 20 while keeping OOB administration isolated.
- Depends on: L3-120, TOP-040
- Deliver Kea DHCP, BIND 9, chrony, FreeRADIUS, and local fallback procedures
- Accept: every client receives the correct site/VLAN lease, resolves internal
  and simulated Internet names, synchronizes time, and exercises central AAA
- Parallel: may run with WAN-140 before VPN-150

### SEC-170 — Segmentation, control plane, and NAT

- Depends on: OSPF-130, WAN-140, SVC-160
- Deliver nftables zone policy, guest NAT, routing-protocol interface filters,
  management ACLs, and negative test matrix
- Accept: guests reach allowed simulated Internet services but cannot reach any
  server, in-band management, corporate site, or OOB address; denied attempts
  produce rate-limited logs

### OBS-180 — Metrics, logs, and flow telemetry

- Depends on: TOP-040, SVC-160
- Deliver Prometheus, SNMP exporter, Grafana, syslog-ng, Alloy, Loki, GoFlow2,
  OVS IPFIX, dashboards, and timestamp labels
- Accept: SNMPv3 authPriv succeeds; v2c fails; a test flow appears with correct
  endpoints/interfaces; link failure produces matching metric and log events
- Parallel: may run with VPN-150 and SEC-170 until integration tests

### TEST-190 — Phase 1 evidence suite

- Depends on: OSPF-130, VPN-150, SEC-170, OBS-180
- Deliver repeatable traffic, fault, security, capture, and correlation tests
- Accept: every definition-of-done item in the architecture overview is produced
  by one run; quantitative thresholds are evaluated automatically
- Serial: owns the shared runtime while executing

### DOC-195 — Rebuild and operations runbooks

- Depends on: TEST-190
- Deliver clean setup, deploy, verify, troubleshoot, evidence, and teardown
  procedures plus the curated Phase 1 report
- Accept: a fresh environment can follow only committed instructions; commands
  and expected outputs match the tested implementation

## Phase 2: Second ISP and redundant edge

1. `WAN-210` adds ISP 2 and second enterprise edge nodes without changing site
   addressing.
2. `WAN-220` implements local preference, AS-path policy, exact export filters,
   and deterministic primary/backup selection.
3. `WAN-230` adds redundant XFRM tunnels whose routing cost follows healthy
   underlay paths.
4. `TEST-240` proves provider, edge, and tunnel failure recovery within 10
   seconds and proves return-path symmetry where policy requires it.

## Phase 3: Branch 2

1. `SITE-310` extracts a validated site template from Branch 1 without copying
   generated state.
2. `SITE-320` instantiates Branch 2 from data using `10.30.0.0/16`, Area 30, and
   AS 65102.
3. `SITE-330` verifies that Area 0 gains one site summary, security policy remains
   isolated, and Branch 1 behavior does not regress.

## Phase 4: SecureEdge

SecureEdge is a routed security service tier, not a renamed edge router.

1. `EDGE-410` introduces explicit inside, outside, guest, management, and service
   zones with fail-closed policy.
2. `EDGE-420` adds Suricata IDS/IPS using a curated local rule set and a bypass
   plan for failure testing.
3. `EDGE-430` correlates firewall decisions and IDS alerts with the existing run
   ID, packets, and flow records.

Remote-access VPN, TLS interception, and production threat feeds are outside the
baseline SecureEdge scope unless a later ADR adds them.

## Phase 5: Network automation

1. `AUTO-510` builds and seeds NetBox as a projection of Git intent.
2. `AUTO-520` generates Ansible dynamic inventory and rendered configuration
   from validated data.
3. `AUTO-530` adds prechecks, backups, diffs, postchecks, idempotence, and drift
   detection. An unreviewed NetBox-only change must be reported as drift.
4. `AUTO-540` adds CI for schemas, templates, configuration lint, unit tests, and
   a resource-bounded topology smoke test.

## Explicit non-goals for the first delivery

- IPv6 routing
- EVPN/VXLAN or stretched Layer 2
- Kubernetes
- SD-WAN controllers
- real Internet dependency
- production PKI or secret-management integration
- vendor-specific certification claims
- wireless controller behavior
