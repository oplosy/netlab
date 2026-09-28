#!/usr/bin/env python3
"""Live AUTO-520 check: the Git-generated and NetBox dynamic inventories are equal.

Compares ``ansible-inventory --list`` for automation/inventory/hosts.yml and
automation/inventory/netbox_inventory.py after a NetBox sync.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ANSIBLE_INVENTORY = str(Path(sys.executable).with_name("ansible-inventory"))
SOURCES = {
    "git": ROOT / "automation" / "inventory" / "hosts.yml",
    "netbox": ROOT / "automation" / "inventory" / "netbox_inventory.py",
}


def listing(source: Path) -> dict[str, Any]:
    output = subprocess.run(
        [ANSIBLE_INVENTORY, "-i", str(source), "--list"],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=300,
    ).stdout
    data = json.loads(output)
    return {
        "hostvars": data["_meta"]["hostvars"],
        "groups": {k: sorted(v.get("hosts", [])) for k, v in data.items() if k not in ("_meta", "all")},
    }


def main() -> int:
    git, netbox = (listing(path) for path in SOURCES.values())
    result = {
        "hosts": {"git": len(git["hostvars"]), "netbox": len(netbox["hostvars"])},
        "groups": {"git": len(git["groups"]), "netbox": len(netbox["groups"])},
        "host_mismatches": sorted(
            h for h in set(git["hostvars"]) | set(netbox["hostvars"])
            if git["hostvars"].get(h) != netbox["hostvars"].get(h)
        ),
        "group_mismatches": sorted(
            g for g in set(git["groups"]) | set(netbox["groups"])
            if git["groups"].get(g) != netbox["groups"].get(g)
        ),
    }
    result["result"] = "PASS" if not result["host_mismatches"] and not result["group_mismatches"] else "FAIL"
    print(json.dumps(result, indent=2))
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
