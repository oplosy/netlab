# Phase 1 acceptance report

## Run identity

- Result: **PASS**
- Run ID: `phase-1-20260929T064923.869785Z`
- Runtime window: 2026-09-29 09:49:23–10:10:08 Europe/Istanbul
- Verified tree: `chore/phase-1-evidence-refresh` at `bf7a436`, which is `main`
  (`86b38e3`, after phases 2–5, VPN-150, and OBS-180) plus the runner's
  child-interpreter fix
- Runtime: existing Containerlab and Docker Engine inside the project WSL2 distribution
- Docker settings changed: **no**
- Stages: **25 passed, 0 failed, 0 warnings**
- Quantitative thresholds: **25 passed, 0 failed**

The raw timestamped bundle is produced under the ignored
`artifacts/runs/phase-1/<run-id>/` directory on the operator's checkout. This
tracked report summarizes the results needed for review. The
[Phase 1 runbook](../../docs/runbooks/phase-1.md) documents how to reproduce
the acceptance run.

## Verified behavior

### IPsec and OSPF

- The HQ–BR1 certificate-authenticated IKEv2/XFRM tunnel established; traffic
  reached the peer only while the security association was up. Every site
  overlay (all three XFRM links) was established before OSPF acceptance.
- The tunnel enforced a 1400-byte XFRM MTU and 1360-byte TCP MSS clamp.
- Rekey traffic's maximum observed gap was **0.100 s**, below the 3 s limit.
- The underlay capture contained **364 ESP packets**, **28 IKE packets**, and
  **0 cleartext enterprise packets**. The tracked capture is
  [`phase-1-ipsec-underlay.pcap`](../pcaps/phase-1-ipsec-underlay.pcap), 77,986
  bytes. The repository capture verifier independently reported the same
  packet counts after it was copied into the evidence directory.
- OSPF/BFD failover's measured packet interruption was **0.501 s**, below the
  3 s limit. The three-run OSPF-131 repeatability evidence also passed; prior
  run interruption measurements were 1.754 s, 1.253 s, and 1.754 s.
- Site summary routes converged at both distribution sites before the live
  failover measurement began.

### Switching and gateways

- The inventory-derived L2 plan passed as a required stage. The live LACP and
  RSTP tests passed at HQ and BR1; maximum reply gaps were 0.060 s and 0.061 s
  at HQ, and 0.060 s and 0.112 s at BR1. The limits were 1 s for LACP and 5 s
  for RSTP.
- Guest-to-user VLAN isolation passed at both sites.
- Routed /31 links, user gateway reachability, guest denies, and VRRP gateway
  failover passed at HQ and BR1.

### Regression stages added after phase 1

- `test-bgp` (phase 2) and `apply-secure-edge` (EDGE-410, which opens the
  firewall tier before transit suites) ran as required stages and passed.

### Services, security, and observability

- SVC-160 passed at both sites: DHCP lease options, local and simulated
  Internet DNS, NTP queries, relay ownership, guest deny rules, and AAA-backed
  SSH. OOB-only SSH listeners and the vtysh-only break-glass account during a
  RADIUS outage were also verified. See the [service evidence](../specs/services/latest.md).
- SEC-170 passed its allowed and denied flow checks, including guest
  segmentation, OOB isolation, and logged deny counters. See the
  [security evidence](../specs/security/latest.md).
- Observability acceptance passed: SNMPv3 authPriv worked for all 18
  inventory-derived targets (OBS-180) and SNMPv2c was denied; a known IPFIX
  flow had the expected endpoints and interfaces; syslog reached Loki; and
  link-failure metric/log correlation passed.

## Cleanup and limits

The runner's final teardown, `make verify-clean`, and its check for leftover
observability containers all passed. A separate post-run check reported no
Docker containers left running. The operator enabled
`net.netfilter.nf_log_all_netns=1` for SEC-170 before the run and restores it
afterwards, as the runbook describes.

This acceptance run used the already-built pinned images and verified their
lock and digests; it did not perform an image rebuild. The image build and
repeatability procedure is documented separately in the
[image runbook](../../docs/runbooks/images.md).
