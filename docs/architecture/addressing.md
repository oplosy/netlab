# Addressing and Naming Plan

## Naming

Node names use lowercase `<site>-<role>-<index>`:

- Sites: `hq`, `br1`, `br2`
- Roles: `edge`, `dist`, `access`, `client`, `server`
- Examples: `hq-edge-1`, `br1-dist-2`, `hq-client-guest-1`

Service nodes use `svc-<function>-<index>`, such as `svc-dns-1`. ISP nodes use
`isp<index>-<role>-<index>`, such as `isp1-core-1`.

Interface descriptions must identify the peer and peer interface:
`to_<peer>_<peer-interface>`.

## Autonomous systems

| Domain | ASN |
|---|---:|
| ISP 1 | 65000 |
| ISP 2 | 65001 |
| Headquarters | 65100 |
| Branch 1 | 65101 |
| Branch 2, reserved | 65102 |

All are private ASNs. The lab must not imply that they are public assignments.

## Site aggregates

| Site | Aggregate | OSPF area |
|---|---|---:|
| Headquarters | `10.10.0.0/16` | 10 |
| Branch 1 | `10.20.0.0/16` | 20 |
| Branch 2, reserved | `10.30.0.0/16` | 30 |

## Repeated VLAN layout

The same VLAN IDs and third octets are used at each site, but their Layer 2
domains are independent.

| VLAN | Name | HQ prefix | Branch 1 prefix | Purpose |
|---:|---|---|---|---|
| 10 | USERS | `10.10.10.0/24` | `10.20.10.0/24` | Corporate clients |
| 20 | SERVERS | `10.10.20.0/24` | `10.20.20.0/24` | Site servers |
| 30 | GUEST | `10.10.30.0/24` | `10.20.30.0/24` | Internet-only clients |
| 99 | INBAND-MGMT | `10.10.99.0/24` | `10.20.99.0/24` | Fallback management |

For every VLAN, `.1` is the VRRP virtual IP, `.2` is distribution 1, `.3` is
distribution 2, `.10-.49` is infrastructure or static service space, and
`.100-.199` is the default DHCP pool.

## Infrastructure ranges

| Purpose | Headquarters | Branch 1 |
|---|---|---|
| Routed point-to-point links | `10.10.252.0/24`, allocated as `/31` | `10.20.252.0/24`, allocated as `/31` |
| Loopbacks/router IDs | `10.10.255.0/24`, allocated as `/32` | `10.20.255.0/24`, allocated as `/32` |

Point-to-point links use RFC 3021 `/31` prefixes. Specific assignments belong
in the authoritative inventory and must not be duplicated manually in topology
files.

## Overlay and underlay

| Purpose | Prefix |
|---|---|
| IPsec XFRM point-to-point links | `10.255.0.0/24`, allocated as `/31` |
| ISP 1 point-to-point links | `192.0.2.0/24`, allocated as `/31` |
| ISP 2 point-to-point links | `198.51.100.0/24`, allocated as `/31` |
| Simulated Internet services | `203.0.113.0/25` |
| Enterprise public endpoint loopbacks | `203.0.113.128/25`, allocated as `/32` |

The three underlay blocks are documentation prefixes and are used only inside
the isolated lab.

WAN-210 activates ISP-2 and allocates HQ/BR1 edge-2. The inventory assigns
`10.10.252.4/31` and `10.10.252.6/31` to HQ edge-2 distribution links,
`10.20.252.4/31` and `10.20.252.6/31` to BR1 edge-2 distribution links,
`192.0.2.4/31` and `192.0.2.6/31` to edge-2 ISP-1 links, and
`198.51.100.0/31` through `198.51.100.6/31` to the eight ISP-2 endpoints.
OOB addresses are `172.31.255.21` (ISP-2), `172.31.255.34` (HQ edge-2), and
`172.31.255.54` (BR1 edge-2).

WAN-210 assigns the stable site endpoints `203.0.113.129/32` to HQ edge-1 and
`203.0.113.130/32` to Branch 1 edge-1. WAN-230 assigns separate endpoints
`203.0.113.131/32` to HQ edge-2 and `203.0.113.132/32` to Branch 1 edge-2.
Each edge advertises only its own `/32`; this keeps the IKE peer address
deterministic while both edge pairs maintain independent encrypted overlays.

The primary HQ/BR1 XFRM link uses `10.255.0.0/31` at OSPF cost 10. The
secondary edge-2 XFRM link uses `10.255.0.2/31` at OSPF cost 100, so OSPF uses
edge-1 while both paths are healthy and can move to edge-2 after primary-path
failure.

## Out-of-band management

The Containerlab management network is `172.31.255.0/24` with fixed addresses.
The initial reservation is:

| Range | Use |
|---|---|
| `172.31.255.1` | Management bridge gateway |
| `172.31.255.10-19` | Automation and operations services |
| `172.31.255.20-29` | ISP and Internet nodes |
| `172.31.255.30-49` | Headquarters infrastructure |
| `172.31.255.50-69` | Branch 1 infrastructure |
| `172.31.255.70-89` | Branch 2 infrastructure |

The management bridge must disable IP masquerading. OOB addresses are never
advertised by OSPF or BGP.
