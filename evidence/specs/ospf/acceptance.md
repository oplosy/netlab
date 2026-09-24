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

Final live acceptance on 2026-09-24 verified the OSPF areas, summaries, passive
interfaces, conditional BGP default origination, routed failover, and VRRP
reaction to loss of the remote OSPF summary. `evidence/specs/ospf/latest.json`
contains the full output, including route and VIP owner snapshots.

- All expected OSPF neighbors and BFD peers were Up before fault injection.
  Area 0 carried the HQ and BR1 `/16` summaries, not VLAN `/24` LSAs. Remote
  `/16` routes, conditional OSPF defaults, and edge BGP defaults were present.
- Dropping BFD control packets on HQ edge `eth1` removed its HQ dist-1
  adjacency in `0.841s`; the edge route to `10.10.10.0/24` moved to HQ dist-2
  on `eth2`.
- The exact OSPF route tracker lowered dist-1's effective VRRP priority from
  `150` to `90`, below dist-2's `100`. The active VIP `10.10.10.1` moved from
  dist-1 to dist-2 during the failure. Dist-2 SVI `10.10.10.3` replied to
  `80/80` probes; the VIP replied to `71/80`. Its maximum reply gap was
  `2.355s`, or `2.255s` estimated interruption, within the three-second goal.
  The measured VIP takeover was `4.145s` after BFD detected the failure; this
  is reported separately from the packet interruption, which is the acceptance
  threshold.
- The inactive dist-1 SVI `.2` lost replies while its remote path was
  withdrawn. Dist-2 remained reachable and owned the gateway VIP during the
  transition. HQ edge `eth1` stayed up throughout; the route tracker tests the
  exact remote `/16` with OSPF protocol, so the OOB or site default cannot mask
  loss of the summary.
- The edge OSPF adjacency returned in `0.534s` after BFD unblocking. Both
  distribution return routes were confirmed restored within `10.809s`. After
  the packet streams completed, the collector waited for dist-1's exact remote
  `10.20.0.0/16 proto ospf` route and preferred VIP ownership. The VIP returned
  to dist-1 `16.392s` after BFD unblocking (`4.066s` after stream collection);
  the final settled owner snapshot confirms dist-1. The wait is bounded at
  `20s` and the return time is independent of the takeover and packet-gap
  measurements.

Keepalived's trusted checker is installed under `/usr/local/sbin` and tests
`ip -4 route show exact <remote-site-/16>` for `proto ospf`. The test does not
change Docker, WSL, or host kernel settings; its temporary nftables table is
removed in a `finally` block.

## Integration target

The task packet calls for `make test-ospf`, but the Makefile fragment is outside
OSPF-130's allowed paths and no such target exists at this task base. The
commands above are the direct task-scoped verification procedure; integration
must add the Make target separately.
