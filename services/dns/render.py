#!/usr/bin/env python3
"""Render deterministic site-local BIND zones from inventory addresses."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _zone_records(data: dict[str, Any], site: str) -> list[tuple[str, str]]:
    records: dict[str, str] = {}
    aggregate = ipaddress.ip_network(next(item["aggregate"] for item in data["sites"] if item["id"] == site))
    for node in data["nodes"]:
        if node.get("site") != site:
            continue
        addresses = [node["loopback"]] if node.get("loopback") else []
        addresses.extend(address for interface in node.get("interfaces", []) for address in interface.get("addresses", []))
        if node.get("service_address"):
            addresses.append(node["service_address"])
        for value in addresses:
            address = ipaddress.ip_interface(value).ip
            if address in aggregate:
                records[node["id"]] = str(address)
                break
    for vlan in (item for item in data["vlans"] if item["site"] == site):
        records[f"gateway-vlan{vlan['vlan_id']}"] = vlan["gateway"]
        for number, address in enumerate(vlan["distribution_addresses"], 1):
            records[f"{site}-dist-{number}-vlan{vlan['vlan_id']}"] = address
    return sorted(records.items())


def render_site(data: dict[str, Any], site: str) -> dict[str, str]:
    nodes = {node["id"]: node for node in data["nodes"]}
    service_node = nodes.get(f"svc-{site}-dns-1")
    if service_node is None or not service_node.get("service_address"):
        raise ValueError(f"{site} has no local DNS service address")
    dns_ip = str(ipaddress.ip_interface(service_node["service_address"]).ip)
    aggregate = ipaddress.ip_network(next(item["aggregate"] for item in data["sites"] if item["id"] == site))
    zone = f"{site}.netlab.test"
    records = _zone_records(data, site)
    seed = "\n".join(f"{name} {address}" for name, address in records).encode()
    serial = int(hashlib.sha256(seed).hexdigest()[:8], 16) or 1
    lines = ["$TTL 300", f"@ IN SOA ns1.{zone}. hostmaster.{zone}. (",
             f"  {serial} 300 120 604800 60 )", f"@ IN NS ns1.{zone}.", f"ns1 IN A {dns_ip}"]
    lines.extend(f"{name} IN A {address}" for name, address in records)
    options = "\n".join([
        "options {", '  directory "/var/cache/bind";',
        f"  listen-on {{ 127.0.0.1; {dns_ip}; }};", "  listen-on-v6 { none; };",
        "  recursion yes;", f"  allow-query {{ localhost; {aggregate}; }};",
        f"  allow-recursion {{ localhost; {aggregate}; }};", "  allow-transfer { none; };",
        "  dnssec-validation no;", "  forwarders { 203.0.113.10; };", "  forward only;", "};", "",
    ])
    local = f'zone "{zone}" {{ type master; file "/etc/bind/netlab-{site}.zone"; allow-transfer {{ none; }}; }};\n'
    return {"options": options, "local": local, "zone": "\n".join(lines) + "\n", "address": dns_ip}


def render_internet_zone() -> str:
    return "\n".join(["$TTL 300",
        "@ IN SOA ns1.internet.test. hostmaster.internet.test. ( 1 300 120 604800 60 )",
        "@ IN NS ns1.internet.test.", "ns1 IN A 203.0.113.10",
        "www IN A 203.0.113.20", "ntp IN A 203.0.113.11", ""])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=ROOT / "inventory" / "inventory.yaml")
    parser.add_argument("--site", required=True)
    args = parser.parse_args()
    print(json.dumps(render_site(json.loads(args.inventory.read_text(encoding="utf-8")), args.site), indent=2))
