#!/usr/bin/env python3
"""Render chrony configs restricted to inventory data-plane prefixes."""
from __future__ import annotations
import argparse
import ipaddress
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def render_site(data: dict[str, Any], site: str) -> str:
    aggregate = ipaddress.ip_network(next(item["aggregate"] for item in data["sites"] if item["id"] == site))
    node = next((item for item in data["nodes"] if item.get("id") == f"svc-{site}-ntp-1"), None)
    if node is None:
        raise ValueError(f"{site} has no local NTP service")
    bind_address = ipaddress.ip_interface(node["service_address"]).ip
    return "\n".join([
        "driftfile /var/lib/chrony/chrony.drift", "makestep 1.0 3", "rtcsync",
        "leapsectz right/UTC", f"bindaddress {bind_address}", "port 123",
        "server 203.0.113.11 iburst prefer", f"allow {aggregate}",
        "cmdport 0", "",
    ])


def render_internet(data: dict[str, Any]) -> str:
    site_sources = [
        str(ipaddress.ip_interface(site["public_endpoint"]).ip) + "/32"
        for site in data["sites"] if site["id"] in {"hq", "br1"}
    ]
    return "\n".join([
        "driftfile /var/lib/chrony/chrony.drift", "makestep 1.0 3", "rtcsync",
        "leapsectz right/UTC", "bindaddress 203.0.113.11", "port 123",
        "local stratum 8", *(f"allow {source}" for source in site_sources), "cmdport 0", "",
    ])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=ROOT / "inventory" / "inventory.yaml")
    parser.add_argument("--site", required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    print(render_site(inventory, args.site), end="")
