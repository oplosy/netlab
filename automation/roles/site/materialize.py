#!/usr/bin/env python3
"""Materialize declarative site fragments into the runtime inventory."""

from __future__ import annotations
import argparse
import copy
import json
import ipaddress
from pathlib import Path
from typing import Any
from render import INVENTORY, TEMPLATE, read_yaml, render_site, instantiate_site

ROOT = Path(__file__).resolve().parents[3]
BR2_SOURCE = ROOT / "inventory" / "sites" / "br2" / "site.yaml"
SOURCE_KEY = "site_template_sources"
ADDRESS_MAP = {
    "192.0.2.2": "192.0.2.8",
    "192.0.2.3": "192.0.2.9",
    "192.0.2.6": "192.0.2.10",
    "192.0.2.7": "192.0.2.11",
    "198.51.100.4": "198.51.100.8",
    "198.51.100.5": "198.51.100.9",
    "198.51.100.6": "198.51.100.10",
    "198.51.100.7": "198.51.100.11",
    "10.255.0.1": "10.255.0.5",
    "10.255.0.3": "10.255.0.7",
    "203.0.113.130": "203.0.113.133",
    "203.0.113.132": "203.0.113.134",
    **{
        f"172.31.255.{n}": f"172.31.255.{n + 20}"
        for n in (*range(50, 55), *range(60, 63))
    },
}


def clean_site(data: dict[str, Any], site_id: str) -> dict[str, Any]:
    result = copy.deepcopy(data)
    result["sites"] = [x for x in result["sites"] if x["id"] != site_id]
    site_node_ids = {x["id"] for x in result["nodes"] if x.get("site") == site_id}
    remove_xfrm_interfaces = {
        (endpoint["node"], endpoint["interface"])
        for link in result["links"]
        if link.get("kind") == "xfrm"
        and any(item["node"] in site_node_ids for item in link["endpoints"])
        for endpoint in link["endpoints"]
        if endpoint["node"] not in site_node_ids
    }
    remove_xfrm_prefixes = {
        link["prefix"]
        for link in result["links"]
        if link.get("kind") == "xfrm"
        and any(item["node"] in site_node_ids for item in link["endpoints"])
    }
    result["nodes"] = [x for x in result["nodes"] if x["id"] not in site_node_ids]
    for node in result["nodes"]:
        if node["id"] in {"isp1-core-1", "isp2-core-1"}:
            node["interfaces"] = [
                x
                for x in node.get("interfaces", [])
                if not x["name"].startswith(f"to_{site_id}_")
            ]
        if node.get("role") == "edge":
            node["interfaces"] = [
                x
                for x in node.get("interfaces", [])
                if (node["id"], x["name"]) not in remove_xfrm_interfaces
            ]
    result["vlans"] = [x for x in result["vlans"] if x.get("site") != site_id]
    result["prefixes"] = [
        x
        for x in result["prefixes"]
        if x.get("owner") != site_id and x.get("id") not in remove_xfrm_prefixes
    ]
    result["bundles"] = [x for x in result["bundles"] if x.get("site") != site_id]
    result["links"] = [
        x
        for x in result["links"]
        if not any(e["node"] in site_node_ids for e in x.get("endpoints", []))
    ]
    result["service_intents"] = [
        x for x in result["service_intents"] if not x["id"].startswith(f"{site_id}-")
    ]
    for intent in result["service_intents"]:
        intent["source_sites"] = [
            x for x in intent.get("source_sites", []) if x != site_id
        ]
    if SOURCE_KEY in result.get("metadata", {}):
        result["metadata"].pop(SOURCE_KEY, None)
    for asn in result["asns"]:
        if asn["id"] == 65102:
            asn["reserved"] = True
    return result


def _translate_string(value: str) -> str:
    if "/" in value:
        address, suffix = value.split("/", 1)
        if address in ADDRESS_MAP:
            return f"{ADDRESS_MAP[address]}/{suffix}"
        try:
            parsed = ipaddress.ip_interface(value)
            if parsed.ip in ipaddress.ip_network("10.20.0.0/16"):
                return f"{parsed.ip.exploded.replace('10.20.', '10.30.')}/{parsed.network.prefixlen}"
        except ValueError:
            pass
    if value in ADDRESS_MAP:
        return ADDRESS_MAP[value]
    if value.startswith("10.20."):
        return value.replace("10.20.", "10.30.", 1)
    value = value.replace("Branch 1", "Branch 2").replace("br1", "br2")
    return value


def _translate(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _translate(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_translate(item, key) for item in value]
    if isinstance(value, str):
        return _translate_string(value)
    if isinstance(value, int) and not isinstance(value, bool):
        if key == "asn" and value == 65101:
            return 65102
        if key == "ospf_area" and value == 20:
            return 30
    return value


def materialize_site(
    data: dict[str, Any], definition: dict[str, Any], template: dict[str, Any]
) -> dict[str, Any]:
    site_id = definition["site"]["id"]
    base = clean_site(data, site_id)
    if site_id != "br2":
        raise ValueError(
            "this materializer currently supports the reserved Branch 2 site"
        )
    if "br1" not in {x["id"] for x in base["sites"]}:
        raise ValueError("Branch 1 must exist as the source site")
    shape = render_site(base, "br1", template)
    instance = instantiate_site(definition["site"], template)
    result = copy.deepcopy(base)
    result["sites"].append(instance["site"])
    result["asns"] = [
        {**x, "reserved": False} if x["id"] == instance["site"]["asn"] else x
        for x in result["asns"]
    ]
    result["nodes"].extend(_translate(shape["nodes"]))
    result["vlans"].extend(_translate(shape["vlans"]))
    result["prefixes"].extend(_translate(shape["prefixes"]))
    result["bundles"].extend(_translate(shape["bundles"]))
    site_nodes = {x["id"] for x in result["nodes"] if x.get("site") == site_id}
    for link in _translate(shape["links"]):
        result["links"].append(link)
        for endpoint in link["endpoints"]:
            if endpoint["node"] not in site_nodes:
                external = next(
                    node for node in result["nodes"] if node["id"] == endpoint["node"]
                )
                if not any(
                    item["name"] == endpoint["interface"]
                    for item in external["interfaces"]
                ):
                    external["interfaces"].append(
                        {
                            "name": endpoint["interface"],
                            "kind": "routed",
                            "addresses": [endpoint["address"]],
                        }
                    )
    result["service_intents"].extend(
        _translate([x for x in shape["service_intents"] if x["id"].startswith("br1-")])
    )
    for intent in result["service_intents"]:
        if (
            intent["id"] in {"isp-dns", "isp-ntp"}
            and site_id not in intent["source_sites"]
        ):
            intent["source_sites"].append(site_id)
    # One certificate-authenticated Branch 2 overlay terminates on the primary HQ edge.
    hq_edge = next(x for x in result["nodes"] if x["id"] == "hq-edge-1")
    hq_edge["interfaces"].append(
        {"name": "xfrm_to_br2_edge_1", "kind": "xfrm", "addresses": ["10.255.0.4/31"]}
    )
    br_edges = [
        x for x in result["nodes"] if x.get("site") == "br2" and x.get("role") == "edge"
    ]
    for edge in br_edges:
        edge["interfaces"] = [x for x in edge["interfaces"] if x["kind"] != "xfrm"]
    br_edge = next(x for x in br_edges if x["id"] == "br2-edge-1")
    br_edge["interfaces"].append(
        {"name": "xfrm_to_hq_edge_1", "kind": "xfrm", "addresses": ["10.255.0.5/31"]}
    )
    result["links"].append(
        {
            "id": "hq-br2-xfrm",
            "kind": "xfrm",
            "prefix": "hq-br2-xfrm",
            "ospf_cost": 10,
            "endpoints": [
                {
                    "node": "hq-edge-1",
                    "interface": "xfrm_to_br2_edge_1",
                    "address": "10.255.0.4/31",
                },
                {
                    "node": "br2-edge-1",
                    "interface": "xfrm_to_hq_edge_1",
                    "address": "10.255.0.5/31",
                },
            ],
        }
    )
    result["prefixes"].append(
        {
            "id": "hq-br2-xfrm",
            "cidr": "10.255.0.4/31",
            "kind": "xfrm",
            "owner": "overlay",
            "parent": "xfrm-pool",
        }
    )
    # XFRM ifnames are Linux interface names (maximum 15 characters).
    nodes_by_id = {node["id"]: node for node in result["nodes"]}
    for node in result["nodes"]:
        node["interfaces"] = [
            item for item in node["interfaces"] if item.get("kind") != "xfrm"
        ]
    interface_index: dict[str, int] = {}
    for link in result["links"]:
        if link.get("kind") != "xfrm":
            continue
        for endpoint in link["endpoints"]:
            index = interface_index.get(endpoint["node"], 0)
            name = f"xfrm{index}"
            interface_index[endpoint["node"]] = index + 1
            endpoint["interface"] = name
            node = nodes_by_id[endpoint["node"]]
            node["interfaces"].append(
                {"name": name, "kind": "xfrm", "addresses": [endpoint["address"]]}
            )
    prefix_ids = {item["id"] for item in result["prefixes"]}
    for link in result["links"]:
        if link.get("kind") != "ebgp" or link.get("prefix") in prefix_ids:
            continue
        provider = next(
            endpoint["node"]
            for endpoint in link["endpoints"]
            if endpoint["node"].startswith("isp")
        )
        provider_endpoint = next(
            endpoint for endpoint in link["endpoints"] if endpoint["node"] == provider
        )
        network = str(ipaddress.ip_interface(provider_endpoint["address"]).network)
        isp = "isp2" if provider.startswith("isp2") else "isp1"
        result["prefixes"].append(
            {
                "id": link["prefix"],
                "cidr": network,
                "kind": "p2p",
                "owner": isp,
                "parent": f"{isp}-underlay-pool",
            }
        )
        prefix_ids.add(link["prefix"])
    result["metadata"][SOURCE_KEY] = {
        "br2": {
            "site": "inventory/sites/br2/site.yaml",
            "template": "inventory/templates/branch-site.yaml",
        }
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--site-definition", type=Path, default=BR2_SOURCE)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--output", type=Path, default=INVENTORY)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        data = json.loads(args.inventory.read_text(encoding="utf-8"))
        definition = read_yaml(args.site_definition)
        if definition.get("template") != "branch-site":
            raise ValueError("site fragment must select branch-site template")
        rendered = materialize_site(data, definition, read_yaml(args.template))
        output = json.dumps(rendered, indent=2) + "\n"
        if args.check:
            current = args.output.read_text(encoding="utf-8")
            if current != output:
                print(
                    f"site inventory is stale: run {Path(__file__).name}",
                    file=__import__("sys").stderr,
                )
                return 1
            print(f"site inventory current: {args.output}")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
            print(
                f"materialized {definition['site']['id']} from {args.site_definition} and {args.template} into {args.output}"
            )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
