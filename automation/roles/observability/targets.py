"""SNMP-monitored nodes, derived from inventory OOB addresses.

Single source for the SNMP agent sidecars (prepare.py), the Prometheus `snmp`
job targets (checked by tests/integration/observability/test_plan.py), and the
live target-count check (tests/integration/observability/acceptance.py).
Standard library only: the live acceptance runs under the lab's system Python.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
INVENTORY = ROOT / "inventory" / "inventory.yaml"
SNMP_ROLES = frozenset({"edge", "dist", "access", "isp", "firewall"})


def snmp_nodes(inventory: dict[str, Any] | None = None) -> dict[str, str]:
    """Return {node id: OOB address} for every SNMP-monitored node."""
    if inventory is None:
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    return {
        node["id"]: node["oob"].split("/")[0]
        for node in inventory["nodes"]
        if node.get("role") in SNMP_ROLES and node.get("oob")
    }
