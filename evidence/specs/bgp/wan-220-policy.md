# WAN-220 dual-provider BGP policy

ISP-1 remains the preferred provider to preserve the Phase 1 forwarding path.
Every edge accepts a default route from both providers and applies these values
on inbound routes:

| Provider | Local preference | Outbound endpoint AS path |
|---|---:|---|
| ISP-1 (AS 65000) | 200 | Site ASN once |
| ISP-2 (AS 65001) | 100 | Site ASN three times total |

Each provider advertises only `0.0.0.0/0` to each edge and accepts at most one
prefix per eBGP neighbor. Edge-1 advertises its site's stable `/32` endpoint to
both providers. Edge-2 advertises no endpoint until WAN-230 installs its
secondary encrypted overlay.

Run `make test-bgp-multihoming` from Ubuntu WSL with the Phase 1 Containerlab
topology running. The test checks all eight sessions, provider preference,
ISP-2 backup after disabling each ISP-1 edge link, recovery, endpoint AS paths,
and rejected route leaks.
