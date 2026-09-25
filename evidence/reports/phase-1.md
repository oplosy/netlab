# Phase 1 acceptance report

## Run identity

- Result: **PASS**
- Run ID: `phase-1-20260925T114508.400058Z`
- Runtime window: 2026-09-25 14:45:08–14:52:19 Europe/Istanbul
- Verified tree: `integration/phase-1` at `7fbd7af` (`merge(TEST-190): require L2 plan acceptance`)
- Runtime: existing Containerlab and Docker Engine inside the project WSL2 distribution
- Docker settings changed: **no**
- Stages: **23 passed, 0 failed, 0 warnings**
- Quantitative thresholds: **25 passed, 0 failed**

The raw timestamped bundle is produced under the ignored
`artifacts/runs/phase-1/<run-id>/` directory on the operator's checkout. This
tracked report summarizes the results needed for review. The
[Phase 1 runbook](../../docs/runbooks/phase-1.md) documents how to reproduce
the acceptance run.

## Verified behavior

### IPsec and OSPF

- The HQ–BR1 certificate-authenticated IKEv2/XFRM tunnel established; traffic
  reached the peer only while the security association was up.
- The tunnel enforced a 1400-byte XFRM MTU and 1360-byte TCP MSS clamp.
- Rekey traffic's maximum observed gap was **0.101 s**, below the 3 s limit.
- The underlay capture contained **364 ESP packets**, **8 IKE packets**, and
  **0 cleartext enterprise packets**. The tracked capture is
  [`phase-1-ipsec-underlay.pcap`](../pcaps/phase-1-ipsec-underlay.pcap), 77,438
  bytes. The repository capture verifier independently reported the same
  packet counts after it was copied into the evidence directory.
- OSPF/BFD failover's measured packet interruption was **1.252 s**, below the
  3 s limit. The three-run OSPF-131 repeatability evidence also passed; prior
  run interruption measurements were 1.754 s, 1.253 s, and 1.754 s.
- Site summary routes converged at both distribution sites before the live
  failover measurement began.

### Switching and gateways

- The inventory-derived L2 plan passed as a required stage. The live LACP and
  RSTP tests passed at HQ and BR1; maximum reply gaps were 0.060 s and 0.112 s
  at HQ, and 0.060 s and 0.168 s at BR1. The limits were 1 s for LACP and 5 s
  for RSTP.
- Guest-to-user VLAN isolation passed at both sites.
- Routed /31 links, user gateway reachability, guest denies, and VRRP gateway
  failover passed at HQ and BR1.

### Services, security, and observability

- SVC-160 passed at both sites: DHCP lease options, local and simulated
  Internet DNS, NTP queries, relay ownership, guest deny rules, and AAA-backed
  SSH. OOB-only SSH listeners and the vtysh-only break-glass account during a
  RADIUS outage were also verified. See the [service evidence](../specs/services/latest.md).
- SEC-170 passed its allowed and denied flow checks, including guest
  segmentation, OOB isolation, and logged deny counters. See the
  [security evidence](../specs/security/latest.md).
- Observability acceptance passed: SNMPv3 authPriv worked for 9 targets and
  SNMPv2c was denied; a known IPFIX flow had the expected endpoints and
  interfaces; syslog reached Loki; and link-failure metric/log correlation
  passed.

## Cleanup and limits

The runner's final teardown, `make verify-clean`, and its check for leftover
observability containers all passed. A separate post-run check confirmed
`net.netfilter.nf_log_all_netns=0` and reported no `netlab-phase-1` containers
or `netlab-mgmt` network.

This acceptance run used the already-built pinned images and verified their
lock and digests; it did not perform an image rebuild. The image build and
repeatability procedure is documented separately in the
[image runbook](../../docs/runbooks/images.md).
