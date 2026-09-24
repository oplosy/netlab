# WAN-140 BGP acceptance procedure

Policy is rendered from `inventory/inventory.yaml`; do not edit rendered FRR
configuration by hand. The apply script enables FRR's `bgpd` daemon in
`/etc/frr/daemons` and reloads FRR only when BGP is not already running. It
configures the inventory-derived ISP `/31` addresses and descriptions on the
WAN-facing interfaces, assigns each site's public endpoint `/32` to `lo`, then
applies the BGP policy. It is safe to repeat and requires the three Phase 1
router containers to be running.

## Apply

From the repository root inside the lab WSL distribution:

```sh
bash automation/roles/bgp/apply.sh
bash automation/roles/bgp/apply.sh
```

Both runs must complete with `applied BGP policy` for `hq-edge-1`,
`br1-edge-1`, and `isp1-core-1`. The second run must leave the same effective
FRR configuration. To check the rendered policy without a live lab:

```sh
uv run --locked python config/routing/bgp/render.py --output-dir /tmp/netlab-bgp
uv run --locked pytest -q -p no:cacheprovider tests/integration/bgp/test_policy.py
```

## Live acceptance

After deploying the topology and applying BGP policy, run this with the
exclusive runtime lock:

```sh
NETLAB_BGP_LIVE=1 uv run --locked pytest -q -p no:cacheprovider tests/integration/bgp/test_policy.py
```

The test requires both site eBGP sessions to be Established; each site's
routes learned from ISP-1 must contain only `0.0.0.0/0`. ISP-1's BGP table must
contain only the two declared site endpoint `/32`s. It temporarily originates
`10.255.254.254/32` from a site and adds `203.0.113.200/32` as an ISP static
route. The ISP must not learn the site prefix and neither site may learn the
unauthorized ISP prefix. Temporary routes are removed in a `finally` block.

The ISP has a per-peer inbound allowlist for only that site's public endpoint
`/32`; it originates a default to each site, limits each session to one
received prefix, and sends only the default back to the sites. Site edges
accept only the exact default and advertise only their own loopback endpoint
`/32`. The connected loopback route is the BGP network's source route; no
discard route is used for an endpoint that must terminate IPsec.

### OOB default route regression

Containerlab installs a management default through `eth0`. The BGP apply step
removes only default routes on `eth0` before applying FRR policy, preserving
the connected OOB management subnet. This allows the eBGP-learned default over
`eth3` to become the selected route for remote public endpoints.

Live verification on 2026-09-24, after applying the change to the running
Phase 1 lab:

```text
HQ: 203.0.113.130 via 192.0.2.1 dev eth3 src 192.0.2.0
BR1: 203.0.113.129 via 192.0.2.3 dev eth3 src 192.0.2.2
```

Both site edges selected their ISP peer over the data-plane WAN interface.
The live BGP acceptance function passed, including rejection and cleanup of
unauthorized route injections. Static pytest result: 3 passed, 1 skipped;
`bash -n automation/roles/bgp/apply.sh` passed.

## Integration boundary

The current inventory gives `svc-dns-1` and `svc-ntp-1` service addresses
`203.0.113.10/25` and `203.0.113.11/25`, but both have empty data-interface
lists and no data-plane links. `isp1-core-1` has only `to_hq_edge_1` and
`to_br1_edge_1`; its `203.0.113.0/25` route is a discard route. These service
addresses therefore are not reachable through the data plane. Phase
integration must add a service-network attachment and service data interfaces
before claiming client-to-service Internet traffic acceptance.

The BGP acceptance above remains measurable without service containers: the
three routing nodes alone provide session state and the relevant BGP tables,
and the live test injects/removes temporary routes on those nodes. A test IP
on the ISP could separately show a site edge reaching the ISP's local stack,
but it would not prove reachability to either service node. Also,
`lab-up` does not invoke this apply script; phase integration must wire
`bash automation/roles/bgp/apply.sh` after topology deployment. This task does
not change lifecycle or topology paths because they are outside WAN-140's
allowed paths.
