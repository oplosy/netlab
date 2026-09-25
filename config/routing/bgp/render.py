#!/usr/bin/env python3
"""Render filtered WAN-210 eBGP sessions from authoritative inventory."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
INVENTORY = ROOT / "inventory" / "inventory.yaml"
PHYSICAL_LINK_KINDS = {"routed", "ebgp", "l2", "access"}


def load_inventory(path: Path = INVENTORY) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sites = {site["id"]: site for site in data["sites"]}
    if set(sites) != {"hq", "br1"}:
        raise ValueError("WAN-210 requires exactly the active sites hq and br1")
    isps = {node["id"]: node for node in data["nodes"] if node.get("role") == "isp"}
    if set(isps) != {"isp1-core-1", "isp2-core-1"}:
        raise ValueError("WAN-210 requires ISP-1 and ISP-2")
    return data


def _ip(address: str) -> str:
    return str(ipaddress.ip_interface(address).ip)


def _interface_map(node: dict[str, Any]) -> dict[str, str]:
    return {
        interface["name"]: f"eth{index}"
        for index, interface in enumerate(
            (item for item in node.get("interfaces", []) if item.get("kind") in PHYSICAL_LINK_KINDS),
            start=1,
        )
    }


def _sessions(data: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in data["nodes"]}
    result = []
    for link in data["links"]:
        if link.get("kind") != "ebgp":
            continue
        local = next((endpoint for endpoint in link["endpoints"] if endpoint["node"] == node_id), None)
        if local is None:
            continue
        remote = next(endpoint for endpoint in link["endpoints"] if endpoint["node"] != node_id)
        result.append({"local": local, "remote": remote, "peer": nodes[remote["node"]], "link": link})
    return result


def _underlay_interfaces(data: dict[str, Any], node_id: str) -> list[str]:
    node = next(node for node in data["nodes"] if node["id"] == node_id)
    mapping = _interface_map(node)
    lines: list[str] = []
    for session in _sessions(data, node_id):
        local, remote = session["local"], session["remote"]
        interface = mapping[local["interface"]]
        peer_name = remote["node"].replace("-", "_")
        description = f"to_{peer_name}_{remote['interface']}"
        lines.extend([f"interface {interface}", f" description {description}", f" ip address {local['address']}", "!"])
    return lines


def _endpoint(data: dict[str, Any], node_id: str) -> str:
    node = next(item for item in data["nodes"] if item["id"] == node_id)
    site = next(item for item in data["sites"] if item["id"] == node["site"])
    return site["public_endpoint"]


def _edge_index(node_id: str) -> int:
    return int(node_id.rsplit("-", 1)[-1])


def _router_id(node: dict[str, Any]) -> str:
    return _ip(node["loopback"]) if node.get("loopback") else _ip(node["interfaces"][0]["addresses"][0])


def _prefix_filters(site_id: str, endpoint: str) -> list[str]:
    return [
        f"ip prefix-list {site_id.upper()}-ENDPOINT seq 10 permit {endpoint}",
        f"ip prefix-list {site_id.upper()}-ENDPOINT seq 100 deny 0.0.0.0/0 le 32",
        "ip prefix-list DENY-ALL seq 10 deny 0.0.0.0/0 le 32",
    ]


def configs(data: dict[str, Any]) -> dict[str, str]:
    sites = {site["id"]: site for site in data["sites"]}
    routers = [node for node in data["nodes"] if node.get("role") in {"isp", "edge"}]
    result: dict[str, str] = {}
    for router in routers:
        node_id = router["id"]
        sessions = _sessions(data, node_id)
        lines = ["! Generated from inventory; do not edit.", *_underlay_interfaces(data, node_id)]
        if router["role"] == "isp":
            isp_asn = router["asn"]
            isp1 = isp_asn == 65000
            if isp1:
                lines.extend(["ip route 0.0.0.0/0 Null0", "ip route 203.0.113.0/25 Null0"])
            for site_id, site in sites.items():
                lines.extend(_prefix_filters(site_id, site["public_endpoint"]))
            if isp1:
                lines.extend([
                    "ip prefix-list ISP-DEFAULT-ONLY seq 10 permit 0.0.0.0/0",
                    "ip prefix-list ISP-DEFAULT-ONLY seq 100 deny 0.0.0.0/0 le 32",
                ])
            lines.extend([f"router bgp {isp_asn}", f" bgp router-id {_router_id(router)}", " no bgp ebgp-requires-policy"])
            for session in sessions:
                site_id = session["peer"].get("site")
                edge_id = session["peer"]["id"]
                peer_ip = _ip(session["remote"]["address"])
                edge1 = _edge_index(edge_id) == 1
                lines.extend([
                    f" neighbor {peer_ip} remote-as {session['peer']['asn']}",
                    f" neighbor {peer_ip} description {edge_id.upper()}",
                    f" neighbor {peer_ip} maximum-prefix 1",
                    f" neighbor {peer_ip} prefix-list {'%s-ENDPOINT' % site_id.upper() if edge1 else 'DENY-ALL'} in",
                    f" neighbor {peer_ip} prefix-list {'ISP-DEFAULT-ONLY' if isp1 else 'DENY-ALL'} out",
                ])
                if isp1:
                    lines.append(f" neighbor {peer_ip} default-originate")
            lines.append(" address-family ipv4 unicast")
            lines.extend(f"  neighbor {_ip(item['remote']['address'])} activate" for item in sessions)
            lines.extend([" exit-address-family", "!"])
        else:
            site_id = router["site"]
            edge1 = _edge_index(node_id) == 1
            endpoint = sites[site_id]["public_endpoint"]
            if edge1:
                lines.extend(["interface lo", " description stable_site_public_endpoint", f" ip address {endpoint}", "!"])
            lines.extend(_prefix_filters(site_id, endpoint))
            lines.extend([
                "ip prefix-list ISP-DEFAULT seq 10 permit 0.0.0.0/0",
                "ip prefix-list ISP-DEFAULT seq 100 deny 0.0.0.0/0 le 32",
                "route-map ISP-IN permit 10",
                " match ip address prefix-list ISP-DEFAULT",
                f"route-map {site_id.upper()}-OUT permit 10",
                f" match ip address prefix-list {site_id.upper()}-ENDPOINT",
                f"route-map {site_id.upper()}-OUT deny 100",
                f"router bgp {router['asn']}",
                f" bgp router-id {_router_id(router)}",
                " no bgp ebgp-requires-policy",
            ])
            for session in sessions:
                peer_ip = _ip(session["remote"]["address"])
                provider = session["peer"]
                lines.extend([
                    f" neighbor {peer_ip} remote-as {provider['asn']}",
                    f" neighbor {peer_ip} description {provider['id'].upper()}",
                    f" neighbor {peer_ip} maximum-prefix 1",
                    f" neighbor {peer_ip} prefix-list ISP-DEFAULT in",
                    f" neighbor {peer_ip} prefix-list {'%s-ENDPOINT' % site_id.upper() if edge1 else 'DENY-ALL'} out",
                ])
            lines.append(" address-family ipv4 unicast")
            for session in sessions:
                peer_ip = _ip(session["remote"]["address"])
                lines.extend([
                    f"  neighbor {peer_ip} activate",
                    f"  neighbor {peer_ip} route-map ISP-IN in",
                    f"  neighbor {peer_ip} route-map {site_id.upper()}-OUT out",
                ])
            if edge1:
                lines.append(f"  network {endpoint}")
            lines.extend([" exit-address-family", "!"])
        result[node_id] = "\n".join(lines) + "\n"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    for node_id, content in configs(load_inventory(args.inventory)).items():
        path = args.output_dir / f"{node_id}.conf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"rendered {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
