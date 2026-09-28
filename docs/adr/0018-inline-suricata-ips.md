# ADR 0018: Run Suricata Inline on the HQ Firewall and Fail Closed

- Status: Accepted
- Date: 2026-09-28
- Supersedes: None
- Related: ADR 0008, ADR 0017

## Context

Phase 4 adds IDS/IPS behavior to the SecureEdge tier. ADR 0017 already makes
`hq-fw-1` the only path between the HQ site and its edges, and its forward
policy drops by default. The IPS needs a documented failure behavior and a
curated, local rule set that the lab can test without Internet feeds.

## Decision

- **Placement.** Suricata 7 runs on `hq-fw-1` in NFQUEUE (IPS) mode. No node
  or link is added.
- **Inspection scope.** In the firewall's forward chain, every verdict that
  used to be `accept`, including established and related traffic, becomes
  `queue num 0`. Only traffic the firewall policy already permits is
  inspected; denied traffic is still dropped by the firewall first. Input
  and output (control plane) traffic is not queued.
- **Failure mode: fail closed.** The queue verdict has no `bypass` flag. When
  Suricata is not attached to queue 0, the kernel drops queued packets, so
  HQ north-south traffic stops instead of passing uninspected. The firewall
  policy and the queue rules are one nftables table, so no window exists in
  which the firewall forwards without the IPS.
- **Rules.** Only the local file `secure-edge/suricata/rules/netlab-local.rules`
  is loaded. It holds curated test signatures in the reserved SID range
  `9420000-9420999`: `alert` rules prove detection and `drop` rules prove
  prevention. No external feed is fetched at build or run time.
- **Evidence.** Suricata writes `eve.json` alerts on `hq-fw-1`. EDGE-430
  correlates them with firewall decisions and flow records.
- **Package.** The network-node image adds `suricata` from the locked Ubuntu
  snapshot, pinned like every other component, and its tag moves to `0.2.0`.

## Consequences

- A Suricata crash or stop isolates HQ from the WAN until it restarts. This
  is the accepted cost of fail closed; the acceptance test measures it.
- All HQ north-south traffic pays a user-space inspection cost. The lab's
  traffic volume is small, so throughput is not an acceptance criterion.
- Existing tests that count firewall `accept` rules now count `queue` rules.
