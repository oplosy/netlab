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

Live acceptance completed on 2026-09-24 after applying OSPF/BFD and the
integrated WAN-140 policy:

- HQ and BR1 edge each formed two routed site adjacencies plus the XFRM
  adjacency; all expected BFD sessions were Up.
- Area 0 contained only `10.10.0.0/16` and `10.20.0.0/16` site summaries; no
  VLAN-specific `/24` summary LSA was present.
- Each site's distribution router installed the remote `/16` and learned the
  default through OSPF. Edge defaults were learned from ISP-1 over `eth3`.
- With BFD packets dropped on HQ edge `eth1`, its HQ dist 1 adjacency was
  removed in `0.860s`. `10.10.10.0/24` remained routed through HQ dist 2 on
  `eth2`; recovery took `2.926s`.
- The OOB management default was absent from the routing nodes; direct OOB
  subnets remained connected. No Docker, WSL, or host kernel settings changed.

These values came from a successful run of the live measurement procedure
above. A later optional runtime-default-withdraw experiment was abandoned after
FRR failed to restore both ISP defaults on soft refresh; the temporary prefix
list entries were removed and the BGP policy was confirmed back at its original
two entries. The final live measurement runner does not perform that route
mutation. The conditional route-map and BFD/summary tests passed before that
experiment. The final source additionally checks self-originated default LSAs;
that added read-only assertion was statically checked but not rerun after the
runtime was released.

## Integration target

The task packet calls for `make test-ospf`, but the Makefile fragment is outside
OSPF-130's allowed paths and no such target exists at this task base. The
commands above are the direct task-scoped verification procedure; integration
must add the Make target separately.
