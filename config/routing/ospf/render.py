#!/usr/bin/env python3
"""Render inventory-derived Phase 1 multi-area OSPF configuration."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
INVENTORY = ROOT / "inventory" / "inventory.yaml"
PHYSICAL_LINK_KINDS = {"routed", "ebgp", "l2", "access"}
AREA_BY_SITE = {"hq": 10, "br1": 20}


def load_inventory(path: Path = INVENTORY) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sites = {site["id"]: site for site in data["sites"]}
    if set(sites) != set(AREA_BY_SITE):
        raise ValueError("OSPF-130 requires exactly the Phase 1 sites hq and br1")
    return data


def _interface_map(node: dict[str, Any]) -> dict[str, str]:
    return {
        interface["name"]: f"eth{index}"
        for index, interface in enumerate(
            (item for item in node.get("interfaces", []) if item.get("kind") in PHYSICAL_LINK_KINDS),
            start=1,
        )
    }


def build_plan(data: dict[str, Any], lab_name: str = "netlab-phase-1") -> dict[str, Any]:
    sites = {site["id"]: site for site in data["sites"]}
    if set(sites) != set(AREA_BY_SITE):
        raise ValueError("OSPF-130 requires exactly the Phase 1 sites hq and br1")
    nodes = {node["id"]: node for node in data["nodes"]}
    planned: dict[str, dict[str, Any]] = {}

    for node in data["nodes"]:
        role = node.get("role")
        if role not in {"edge", "dist"}:
            continue
        site_id = node.get("site")
        if site_id not in AREA_BY_SITE:
            raise ValueError(f"{node['id']} has unsupported OSPF site {site_id!r}")
        area = AREA_BY_SITE[site_id]
        interfaces: list[dict[str, Any]] = []
        if role == "edge":
            internal_links = {
                endpoint["interface"]
                for link in data["links"]
                if link.get("kind") == "routed"
                and any(peer["node"] == node["id"] for peer in link["endpoints"])
                and any(nodes[peer["node"]].get("role") == "dist" for peer in link["endpoints"] if peer["node"] != node["id"])
                for endpoint in link["endpoints"]
                if endpoint["node"] == node["id"]
            }
            for intf in node.get("interfaces", []):
                if intf.get("kind") != "routed" or intf["name"] not in internal_links:
                    continue
                name = _interface_map(node).get(intf["name"])
                addresses = intf.get("addresses", [])
                if name is None or len(addresses) != 1:
                    raise ValueError(f"{node['id']} routed OSPF interface {intf['name']} is incomplete")
                network = ipaddress.ip_interface(addresses[0]).network
                interfaces.append({"name": name, "area": area, "bfd": True, "network": str(network)})
            interfaces.append({"name": "xfrm0", "area": 0, "bfd": True, "network": None})
        else:
            routed = [item for item in node.get("interfaces", []) if item.get("kind") == "routed"]
            if len(routed) != 1:
                raise ValueError(f"{node['id']} must have exactly one routed edge adjacency")
            routed_item = routed[0]
            name = _interface_map(node).get(routed_item["name"])
            addresses = routed_item.get("addresses", [])
            if name is None or len(addresses) != 1:
                raise ValueError(f"{node['id']} routed OSPF interface {routed_item['name']} is incomplete")
            network = ipaddress.ip_interface(addresses[0]).network
            interfaces.append({"name": name, "area": area, "bfd": True, "network": str(network)})
            for intf in node.get("interfaces", []):
                if intf.get("kind") != "svi":
                    continue
                if len(intf.get("addresses", [])) != 1:
                    raise ValueError(f"{node['id']} {intf['name']} needs exactly one SVI address")
                prefix = ipaddress.ip_interface(intf["addresses"][0]).network
                interfaces.append({"name": intf["name"], "area": area, "bfd": False, "network": str(prefix), "passive": True})

        router_id = ipaddress.ip_interface(node["loopback"]).ip
        planned[node["id"]] = {
            "node": node["id"],
            "site": site_id,
            "role": role,
            "container": f"clab-{lab_name}-{node['id']}",
            "router_id": str(router_id),
            "area": area,
            "aggregate": str(ipaddress.ip_network(sites[site_id]["aggregate"])),
            "interfaces": interfaces,
        }

    expected = {"hq-edge-1", "br1-edge-1", "hq-dist-1", "hq-dist-2", "br1-dist-1", "br1-dist-2"}
    if set(planned) != expected:
        raise ValueError(f"OSPF-130 expects the six Phase 1 edge/distribution nodes; got {sorted(planned)}")
    if len({item["router_id"] for item in planned.values()}) != len(planned):
        raise ValueError("OSPF router IDs must be unique")
    return {"nodes": planned}


def render_node(item: dict[str, Any]) -> str:
    lines = [
        "! Generated from inventory; do not edit.",
        "ip prefix-list OSPF-VALID-INTERNET-DEFAULT seq 10 permit 0.0.0.0/0",
        "route-map OSPF-VALID-INTERNET-DEFAULT permit 10",
        " match ip address prefix-list OSPF-VALID-INTERNET-DEFAULT",
        " match source-protocol bgp",
        "!",
    ]
    for interface in item["interfaces"]:
        name = interface["name"]
        lines.extend([f"interface {name}", f" ip ospf area {interface['area']}"])
        if interface.get("passive"):
            lines.append(" ip ospf passive")
        else:
            lines.append(" ip ospf network point-to-point")
            lines.append(" ip ospf bfd")
            lines.append(" no ip ospf passive")
        lines.append("!")

    lines.extend([
        "router ospf",
        f" ospf router-id {item['router_id']}",
        " passive-interface default",
    ])
    if item["role"] == "edge":
        lines.extend([
            f" area {item['area']} range {item['aggregate']}",
            # Also require a BGP-originated default; an OOB kernel default is not Internet reachability.
            " default-information originate route-map OSPF-VALID-INTERNET-DEFAULT",
        ])
    lines.append("!")
    return "\n".join(lines) + "\n"


def configs(data: dict[str, Any]) -> dict[str, str]:
    return {node_id: render_node(item) for node_id, item in build_plan(data)["nodes"].items()}


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
