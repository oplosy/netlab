#!/usr/bin/env python3
"""Render site-local Kea DHCPv4 configuration from the authoritative inventory."""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def render(data: dict[str, Any], site: str) -> dict[str, Any]:
    if site not in {item["id"] for item in data["sites"]}:
        raise ValueError(f"unknown site: {site}")
    services = {item["service"]: item for item in data["nodes"]
                if item.get("site") == site and item.get("role") == "service"}
    if not {"dhcp", "dns", "ntp"} <= services.keys():
        raise ValueError(f"{site} must have local DHCP, DNS and NTP services")
    dns_ip = str(ipaddress.ip_interface(services["dns"]["service_address"]).ip)
    ntp_ip = str(ipaddress.ip_interface(services["ntp"]["service_address"]).ip)
    subnets = []
    vlans = sorted((v for v in data["vlans"] if v["site"] == site), key=lambda v: v["vlan_id"])
    for index, vlan in enumerate(vlans, 1):
        network = ipaddress.ip_network(vlan["prefix"])
        subnets.append({
            "id": index,
            "subnet": str(network),
            "pools": [{"pool": f"{network.network_address + 100}-{network.network_address + 199}"}],
            "option-data": [
                {"name": "routers", "data": vlan["gateway"]},
                {"name": "domain-name-servers", "data": dns_ip},
                {"name": "ntp-servers", "data": ntp_ip},
                {"name": "domain-name", "data": f"{site}.netlab.test"},
            ],
        })
    return {"Dhcp4": {
        "interfaces-config": {"interfaces": ["eth1"]},
        "lease-database": {"type": "memfile", "persist": True, "name": "/var/lib/kea/kea-leases.csv"},
        "valid-lifetime": 3600, "renew-timer": 900, "rebind-timer": 1800,
        "subnet4": subnets,
        "loggers": [{"name": "kea-dhcp4", "output_options": [{"output": "stdout"}], "severity": "INFO"}],
    }}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=ROOT / "inventory" / "inventory.yaml")
    parser.add_argument("--site", required=True)
    args = parser.parse_args()
    print(json.dumps(render(json.loads(args.inventory.read_text(encoding="utf-8")), args.site), indent=2))


if __name__ == "__main__":
    main()
