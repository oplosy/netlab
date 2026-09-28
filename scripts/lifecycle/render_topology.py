#!/usr/bin/env python3
"""Render the Containerlab topology from the authoritative inventory.

The inventory is the only source for node names, addresses, interfaces, and
links.  This renderer deliberately omits the XFRM link from Containerlab's
physical veth graph; VPN-150 creates that virtual interface over the underlay.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "inventory" / "inventory.yaml"
SCHEMA = ROOT / "schemas" / "inventory.schema.json"
VERSIONS = ROOT / "versions.env"
OUTPUT = ROOT / "lab" / "phase-1.clab.yml"
PHYSICAL_LINK_KINDS = {"routed", "ebgp", "l2", "access"}


def _load_versions(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"')
        values[key] = value
    return values


def _load_inventory(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"inventory load failed: {exc}") from exc

    # Reuse DAT-030's validator so a topology can never be rendered from an
    # inventory with an unknown peer, duplicate address, or invalid bundle.
    sys.path.insert(0, str(ROOT))
    from scripts.validate.validate_inventory import validate_inventory

    errors = validate_inventory(data, SCHEMA)
    if errors:
        raise SystemExit("inventory validation failed:\n" + "\n".join(f"- {item}" for item in errors))
    return data


def _interface_maps(data: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Map inventory interface names to stable Linux endpoint names."""
    result: dict[str, dict[str, str]] = {}
    for node in data["nodes"]:
        mapping: dict[str, str] = {}
        index = 1  # eth0 is reserved for Containerlab management.
        for interface in node.get("interfaces", []):
            if interface.get("kind") in PHYSICAL_LINK_KINDS:
                mapping[interface["name"]] = f"eth{index}"
                index += 1
        result[node["id"]] = mapping
    return result


def _image_for(node: dict[str, Any], versions: dict[str, str]) -> tuple[str, str]:
    role = node.get("role")
    if role in {"edge", "dist", "firewall", "access", "isp"}:
        return versions["NETLAB_NETWORK_IMAGE"], "network-node"
    if role in {"client", "server"}:
        return versions["NETLAB_CLIENT_IMAGE"], "client"
    if role == "service":
        return versions["NETLAB_SERVICE_IMAGE"], "service"
    raise SystemExit(f"unsupported inventory node role: {role!r}")


def _node_entry(node: dict[str, Any], versions: dict[str, str], mappings: dict[str, dict[str, str]]) -> dict[str, Any]:
    image, image_role = _image_for(node, versions)
    endpoint = node["role"] in {"client", "server"}
    labels = {
        "netlab.inventory-node": node["id"],
        "netlab.inventory-role": node["role"],
        "netlab.interface-map": ",".join(
            f"{endpoint}={intent}" for intent, endpoint in mappings[node["id"]].items()
        ),
    }
    entry: dict[str, Any] = {
        "kind": "linux",
        "image": image,
        "network-mode" if endpoint else "mgmt-ipv4": (
            "none" if endpoint else str(ipaddress.ip_interface(node["oob"]).ip)
        ),
        "labels": labels,
    }
    if image_role == "network-node":
        # The image has a deliberately small role vocabulary. ISP-1 is still a
        # network node in the skeleton; WAN-140 adds its FRR policy later.
        entry["env"] = {
            "NETLAB_NODE_ROLE": (
                "router" if node["role"] in {"isp", "firewall"} else {"dist": "distribution"}.get(node["role"], node["role"])
            ),
            "OVS_DATAPATH_MODE": "kernel",
            "NETLAB_INVENTORY_ROLE": node["role"],
        }
        if node["role"] == "firewall":
            # Fail closed (ADR 0017): a new netns inherits the host's forwarding
            # setting, so the firewall boots with forwarding off. The security
            # apply enables it only after the drop-by-default policy is loaded.
            entry["sysctls"] = {"net.ipv4.ip_forward": 0}
    elif image_role == "service":
        entry["env"] = {"NETLAB_SERVICE": str(node.get("service", ""))}
    return entry


def render(data: dict[str, Any], versions: dict[str, str]) -> dict[str, Any]:
    mappings = _interface_maps(data)
    nodes = {node["id"]: _node_entry(node, versions, mappings) for node in data["nodes"]}
    links: list[dict[str, Any]] = []
    for link in data["links"]:
        kind = link.get("kind")
        if kind == "xfrm":
            continue
        if kind not in PHYSICAL_LINK_KINDS:
            raise SystemExit(f"unsupported physical link kind {kind!r} in {link.get('id')}")
        endpoints: list[dict[str, Any]] = []
        for endpoint in link["endpoints"]:
            node_id = endpoint["node"]
            intent_interface = endpoint["interface"]
            try:
                interface = mappings[node_id][intent_interface]
            except KeyError as exc:
                raise SystemExit(
                    f"link {link['id']} references non-physical interface {node_id}:{intent_interface}"
                ) from exc
            rendered: dict[str, Any] = {"node": node_id, "interface": interface}
            endpoints.append(rendered)
        address_intent = ";".join(
            f"{endpoint['node']}:{endpoint['interface']}={endpoint['address']}"
            for endpoint in link["endpoints"]
            if endpoint.get("address")
        )
        rendered_link: dict[str, Any] = {
            "type": "veth",
            "endpoints": endpoints,
            "labels": {
                "netlab.inventory-link": link["id"],
                "netlab.inventory-kind": kind,
                "netlab.inventory-bundle": link.get("bundle", ""),
                "netlab.inventory-addresses": address_intent,
            },
        }
        links.append(rendered_link)

    return {
        "name": "netlab-phase-1",
        "mgmt": {
            "network": "netlab-mgmt",
            "ipv4-subnet": "172.31.255.0/24",
            "ipv4-range": "172.31.255.128/25",
            "ipv4-gw": "172.31.255.1",
            "external-access": False,
            "driver-opts": {"com.docker.network.bridge.enable_ip_masquerade": "false"},
        },
        "topology": {"nodes": nodes, "links": links},
    }


def _serialized(topology: dict[str, Any]) -> str:
    header = (
        "# Generated by scripts/lifecycle/render_topology.py from "
        "inventory/inventory.yaml; do not edit.\n"
        "# XFRM intent links are virtual and are intentionally created by VPN-150, not as veth links.\n"
        "# Containerlab 0.77 has no privileged toggle; linux-kind defaults are retained by the pinned runtime.\n"
    )
    return header + yaml.safe_dump(topology, sort_keys=False, default_flow_style=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--versions", type=Path, default=VERSIONS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if output is not current")
    args = parser.parse_args()
    rendered = _serialized(_render_data(args.inventory, args.versions))
    if args.check:
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"topology is not rendered: {exc}", file=sys.stderr)
            return 1
        if current != rendered:
            print(f"topology is stale: regenerate {args.output}", file=sys.stderr)
            return 1
        print(f"topology current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"rendered topology: {args.output}")
    return 0


def _render_data(inventory: Path, versions: Path) -> dict[str, Any]:
    # Keep paths injectable for static checks while retaining repository-root
    # defaults for normal Make usage.
    data = _load_inventory(inventory)
    values = _load_versions(versions)
    required = {"NETLAB_NETWORK_IMAGE", "NETLAB_CLIENT_IMAGE", "NETLAB_SERVICE_IMAGE"}
    missing = sorted(required - values.keys())
    if missing:
        raise SystemExit(f"versions.env missing image locks: {', '.join(missing)}")
    if any(re.search(r":(?:latest|stable|edge)$", values[name]) for name in required):
        raise SystemExit("floating image tag in versions.env")
    return render(data, values)


if __name__ == "__main__":
    raise SystemExit(main())
