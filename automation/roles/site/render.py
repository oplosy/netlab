#!/usr/bin/env python3
"""Render a site-scoped inventory projection from the authoritative inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
INVENTORY = ROOT / "inventory" / "inventory.yaml"
TEMPLATE = ROOT / "inventory" / "templates" / "branch-site.yaml"
FORBIDDEN_RUNTIME_FIELDS = {
    "state",
    "observed_at",
    "routes",
    "sessions",
    "counters",
    "packet_capture",
    "generated_at",
}


def read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a YAML mapping")
    return value


def validate_template(template: dict[str, Any]) -> None:
    if template.get("version") != 1:
        raise ValueError("branch site template version must be 1")
    if template.get("runtime_fields") != []:
        raise ValueError("site templates must not contain generated runtime state")
    if set(template) & FORBIDDEN_RUNTIME_FIELDS:
        raise ValueError("template contains a runtime-only field")
    if template.get("role_counts") != {
        "edge": 2,
        "dist": 2,
        "access": 1,
        "client": 2,
        "server": 1,
        "service": 3,
    }:
        raise ValueError(
            "branch site template role_counts do not match the Phase 1 site shape"
        )
    if template.get("vlan_ids") != [10, 20, 30, 99]:
        raise ValueError("branch site template VLANs must be [10, 20, 30, 99]")
    if template.get("bundle_kinds") != {"access-uplink": 2, "distribution-peer": 1}:
        raise ValueError("branch site template bundle kinds are invalid")


def instantiate_site(
    definition: dict[str, Any], template: dict[str, Any]
) -> dict[str, Any]:
    """Expand a site metadata record with the reusable declarative site shape."""
    validate_template(template)
    required = {"id", "name", "aggregate", "ospf_area", "asn", "public_endpoint"}
    if set(definition) != required:
        raise ValueError(f"site definition fields must be {sorted(required)}")
    return {
        "site": dict(definition),
        "template_version": template["version"],
        "role_counts": dict(template["role_counts"]),
        "vlan_ids": list(template["vlan_ids"]),
        "bundle_kinds": dict(template["bundle_kinds"]),
    }


def render_site(
    data: dict[str, Any], site_id: str, template: dict[str, Any]
) -> dict[str, Any]:
    validate_template(template)
    sites = {item["id"]: item for item in data.get("sites", [])}
    if site_id not in sites:
        raise ValueError(f"unknown site {site_id!r}")
    nodes = [item for item in data.get("nodes", []) if item.get("site") == site_id]
    node_ids = {item["id"] for item in nodes}
    counts = {
        role: sum(item.get("role") == role for item in nodes)
        for role in template["role_counts"]
    }
    if counts != template["role_counts"]:
        raise ValueError(
            f"{site_id} node role counts do not match branch template: {counts}"
        )
    vlans = [item for item in data.get("vlans", []) if item.get("site") == site_id]
    if sorted(int(item["vlan_id"]) for item in vlans) != template["vlan_ids"]:
        raise ValueError(f"{site_id} VLANs do not match branch template")
    bundles = [item for item in data.get("bundles", []) if item.get("site") == site_id]
    bundle_counts = {
        kind: sum(item.get("kind") == kind for item in bundles)
        for kind in template["bundle_kinds"]
    }
    if bundle_counts != template["bundle_kinds"]:
        raise ValueError(
            f"{site_id} bundle kinds do not match branch template: {bundle_counts}"
        )
    links = []
    for link in data.get("links", []):
        local = [
            endpoint
            for endpoint in link.get("endpoints", [])
            if endpoint.get("node") in node_ids
        ]
        if local and (
            len(local) == len(link.get("endpoints", []))
            or (link.get("kind") == "ebgp" and len(local) == 1)
        ):
            links.append(link)
    return {
        "site": sites[site_id],
        "nodes": nodes,
        "vlans": vlans,
        "prefixes": [
            item for item in data.get("prefixes", []) if item.get("owner") == site_id
        ],
        "links": links,
        "bundles": bundles,
        "service_intents": [
            item
            for item in data.get("service_intents", [])
            if site_id in item.get("source_sites", [])
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site")
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        rendered = render_site(
            read_yaml(args.inventory), args.site, read_yaml(args.template)
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    output = json.dumps(rendered, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
