"""AUTO-510: project Git intent (inventory/inventory.yaml) into NetBox objects.

Pure functions only; ``sync.py`` talks to the NetBox API. Every object is keyed
by a natural key and carries the fields NetBox must hold for it (ADR 0019).
"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "inventory" / "inventory.yaml"
TAG = "netlab-intent"
SOURCE = "inventory/inventory.yaml"
SHARED_SITE = {"slug": "shared", "name": "Shared services and simulated Internet"}
MANUFACTURER = {"slug": "netlab", "name": "netlab"}
DEVICE_TYPE = {"slug": "linux-container", "model": "Linux container"}
ROLE_COLORS = {
    "edge": "2196f3", "dist": "4caf50", "firewall": "f44336", "access": "8bc34a",
    "isp": "9e9e9e", "service": "ff9800", "client": "607d8b", "server": "795548",
}
# NetBox interface type per inventory interface kind.
INTERFACE_TYPES = {"routed": "virtual", "l2": "virtual", "svi": "virtual", "xfrm": "virtual"}

# Model order for creation; deletion runs in reverse.
MODELS = ("sites", "roles", "devices", "interfaces", "ip_addresses", "vlans", "prefixes")


def load_inventory(path: Path = INVENTORY) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _description(kind: str, ident: str) -> str:
    return f"{TAG}: {kind} {ident} from {SOURCE}"


def desired(data: dict[str, Any]) -> dict[str, dict[Any, dict[str, Any]]]:
    """Desired NetBox state: model -> natural key -> fields."""
    sites = {
        site["id"]: {"name": site["name"], "description": _description("site", site["id"])}
        for site in data["sites"]
    }
    sites[SHARED_SITE["slug"]] = {
        "name": SHARED_SITE["name"],
        "description": _description("site", SHARED_SITE["slug"]),
    }
    roles = {
        role: {"name": role, "color": color}
        for role, color in ROLE_COLORS.items()
        if any(node.get("role") == role for node in data["nodes"])
    }
    devices: dict[Any, dict[str, Any]] = {}
    interfaces: dict[Any, dict[str, Any]] = {}
    addresses: dict[Any, dict[str, Any]] = {}
    for node in data["nodes"]:
        name = node["id"]
        devices[name] = {
            "role": node["role"],
            "site": node.get("site") or SHARED_SITE["slug"],
            "device_type": DEVICE_TYPE["slug"],
            "description": _description("node", name),
        }
        specs: list[tuple[str, str, list[str]]] = [
            (item["name"], INTERFACE_TYPES[item["kind"]], item.get("addresses", []))
            for item in node.get("interfaces", [])
        ]
        if node.get("oob"):
            specs.append(("mgmt0", "virtual", [node["oob"]]))
        if node.get("loopback"):
            specs.append(("lo", "virtual", [node["loopback"]]))
        if node.get("service_address"):
            specs.append(("service0", "virtual", [node["service_address"]]))
        for iface, iface_type, iface_addresses in specs:
            interfaces[(name, iface)] = {
                "type": iface_type,
                "description": _description("interface", f"{name}:{iface}"),
            }
            for address in iface_addresses:
                address = str(ipaddress.ip_interface(address))
                addresses[(name, iface, address)] = {
                    "description": _description("address", f"{name}:{iface}"),
                }
    vlans = {
        (vlan["site"], int(vlan["vlan_id"])): {
            "name": vlan["name"],
            "description": _description("vlan", vlan["id"]),
        }
        for vlan in data["vlans"]
    }
    prefixes = {
        str(ipaddress.ip_network(prefix["cidr"])): {
            "description": _description(f"prefix/{prefix['kind']}", prefix["id"]),
        }
        for prefix in data["prefixes"]
    }
    return {
        "sites": sites,
        "roles": roles,
        "devices": devices,
        "interfaces": interfaces,
        "ip_addresses": addresses,
        "vlans": vlans,
        "prefixes": prefixes,
    }


def diff(
    want: dict[str, dict[Any, dict[str, Any]]], have: dict[str, dict[Any, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Ordered changes that turn ``have`` into ``want``.

    Each change is ``{"model", "key", "action": create|update|delete, "fields"}``.
    ``have`` must be keyed and shaped like ``want`` (see sync.observed).
    """
    changes: list[dict[str, Any]] = []
    for model in MODELS:
        for key in sorted(want[model], key=str):
            fields = want[model][key]
            current = have.get(model, {}).get(key)
            if current is None:
                changes.append({"model": model, "key": key, "action": "create", "fields": fields})
            else:
                delta = {f: v for f, v in fields.items() if current.get(f) != v}
                if delta:
                    changes.append({"model": model, "key": key, "action": "update", "fields": delta})
    for model in reversed(MODELS):
        for key in sorted(have.get(model, {}), key=str):
            if key not in want[model]:
                changes.append({"model": model, "key": key, "action": "delete", "fields": {}})
    return changes
