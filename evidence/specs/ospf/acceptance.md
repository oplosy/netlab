# OSPF-130 acceptance procedure

OSPF configuration is generated from `inventory/inventory.yaml` by
`config/routing/ospf/render.py`. Do not hand-edit generated FRR configuration.
The apply script enables only FRR's `ospfd` and `bfdd` daemons on the six
Phase 1 edge/distribution routers, then applies the generated configuration.
It removes only injected `eth0` default routes from these data-plane routers,
preserving the directly connected OOB subnet so OOB cannot win over the valid
ISP/BGP or OSPF default.
It does not alter Docker, WSL, or host kernel settings.

## Apply and static checks

From the repository root in the lab's Ubuntu WSL distribution:

```sh
python3 config/routing/ospf/render.py --output-dir /tmp/netlab-ospf
bash -n automation/roles/ospf/apply.sh
bash automation/roles/ospf/apply.sh
bash automation/roles/ospf/apply.sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p no:cacheprovider tests/integration/ospf/test_plan.py
```

The two apply runs should each report `applied OSPF/BFD policy` for HQ/BR1
edges and all four distribution routers. The second run is idempotent. Static
tests cover area assignments, passive SVI behavior, BFD on routed adjacencies,
site `/16` aggregation, conditional default origination, the absence of OSPF
on WAN/OOB links, and unique router IDs.

## Live acceptance

Serialize access to the shared lab runtime, then run:

```sh
NETLAB_OSPF_LIVE=1 python3 tests/integration/ospf/measure.py --output evidence/specs/ospf/latest.json
```

The executable evidence routine waits for every expected OSPF adjacency to be
Full and every BFD peer to be Up. It checks the two remote site summaries in
Area 0 without VLAN-specific summary LSAs, verifies both dist routers receive
the remote `/16` plus a default route, and rejects neighbor formation on OOB,
WAN, or passive SVI interfaces. It then drops only BFD UDP control packets on
HQ edge `eth1` while keeping the link and OSPF Hellos up. BFD must remove the
HQ edge to HQ dist 1 OSPF adjacency within 3 seconds. The duplicated HQ VLAN
route must remain via HQ dist 2; this verifies that the partial routed-link
failure is covered by the second distribution path. The script removes its
temporary nftables table in a `finally` block and measures adjacency recovery.

The site default route-map requires both the exact default prefix and BGP as
the source protocol. This prevents an OOB kernel default from being mistaken
for valid Internet reachability. The live routine compares each edge's BGP
default with its self-originated OSPF external default LSA. It also verifies
that a site default is present when at least one edge has a valid BGP default.
This default-policy check is read-only.

## Runtime result

The final live run on 2026-09-24 completed the area, summary, passive-interface,
BGP-default-source, and BFD checks. It also exposed a gateway failover blocker;
`evidence/specs/ospf/latest.json` records the failed packet-continuity result.

- All expected OSPF neighbors and BFD peers were Up before fault injection.
  Area 0 carried the HQ and BR1 `/16` summaries, not VLAN `/24` LSAs. Remote
  `/16` routes, conditional OSPF defaults, and edge BGP defaults were present.
- Dropping BFD control packets on HQ edge `eth1` removed its HQ dist-1
  adjacency in `0.827s`. The HQ edge route to `10.10.10.0/24` moved to HQ
  dist-2 on `eth2`.
- During the fault, the HQ dist-2 SVI `10.10.10.3` answered `80/80` probes
  with a maximum reply gap of `0.100s`. The active gateway VIP `10.10.10.1`
  remained on HQ dist-1 before, during, and after the fault; its probe received
  only `36/80` replies with an `11.120s` maximum gap (`11.020s` estimated
  interruption). HQ dist-1's physical SVI `.2` had the same loss.
- The dist-1 and dist-2 return routes to the BR1 tunnel address were present
  before and after. Both return routes were confirmed restored `18.640s` after
  BFD unblocking. The result does not meet the three-second user-traffic
  interruption objective because the static VRRP master does not move the VIP
  when its OSPF uplink adjacency fails.
- The live Keepalived configuration has priorities `150` and `100` and no
  upstream `track_interface` or `track_script`. A physical interface tracker
  alone would not detect this BFD-only failure because `eth1` stays up.

OSPF summaries, passive-interface behavior, conditional default origination,
and BFD detection are verified. End-to-end gateway failover remains blocked on
upstream-reachability tracking for VRRP, which belongs to the gateway/L3 task
outside OSPF-130's allowed paths. No Docker, WSL, or host kernel settings were
changed, and the temporary nftables test table was removed in cleanup.

## Integration target

The task packet calls for `make test-ospf`, but the Makefile fragment is outside
OSPF-130's allowed paths and no such target exists at this task base. The
commands above are the direct task-scoped verification procedure; integration
must add the Make target separately.
