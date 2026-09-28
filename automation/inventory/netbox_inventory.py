#!/usr/bin/env python3
"""AUTO-520: Ansible dynamic inventory read from the NetBox projection.

Usage: ``ansible-inventory -i automation/inventory/netbox_inventory.py --list``.
Only devices tagged ``netlab-intent`` are listed. The OOB address comes from
the device's ``mgmt0`` interface. Shape matches render_inventory.build().
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "netbox"))
import render_inventory  # noqa: E402
import sync  # noqa: E402


def hosts_from_netbox(nb: sync.NetBox) -> list[dict[str, Any]]:
    oob: dict[str, str] = {}
    for address in nb.all("ip_addresses"):
        assigned = address.get("assigned_object") or {}
        if assigned.get("name") == "mgmt0":
            oob[(assigned.get("device") or {}).get("name")] = address["address"]
    hosts = []
    for device in nb.all("devices"):
        if not any(tag["slug"] == "netlab-intent" for tag in device.get("tags", [])):
            continue
        site = (device.get("site") or {}).get("slug")
        hosts.append(
            render_inventory.host(
                device["name"],
                (device.get("role") or {}).get("slug"),
                None if site == render_inventory.SHARED_SITE else site,
                oob.get(device["name"]),
            )
        )
    return hosts


def to_script_json(inventory: dict[str, Any]) -> dict[str, Any]:
    """Convert the YAML-shaped inventory into the dynamic-inventory JSON format."""
    root = inventory["all"]
    result: dict[str, Any] = {
        "_meta": {"hostvars": {name: {**root["vars"], **hv} for name, hv in root["hosts"].items()}},
        "all": {"children": sorted(root["children"]), "vars": root["vars"]},
    }
    for group, body in root["children"].items():
        result[group] = {"hosts": sorted(body["hosts"])}
    return result


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--host":
        print("{}")
        return 0
    nb = sync.NetBox(os.environ.get("NETBOX_URL", sync.DEFAULT_URL), sync._token())
    print(json.dumps(to_script_json(render_inventory.build(hosts_from_netbox(nb))), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
