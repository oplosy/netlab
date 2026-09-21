from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.validate.validate_inventory import validate_inventory  # noqa: E402


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = json.loads((ROOT / "inventory" / "inventory.yaml").read_text(encoding="utf-8"))

    def test_authoritative_inventory_is_valid(self) -> None:
        self.assertEqual(validate_inventory(self.inventory), [])

    def test_duplicate_ip_is_rejected(self) -> None:
        changed = copy.deepcopy(self.inventory)
        changed["nodes"][1]["oob"] = changed["nodes"][0]["oob"]
        errors = validate_inventory(changed)
        self.assertTrue(any("duplicate IP address 172.31.255.30" in error for error in errors))

    def test_duplicate_asn_is_rejected(self) -> None:
        changed = copy.deepcopy(self.inventory)
        changed["asns"].append({"id": 65100, "name": "duplicate", "domain": "enterprise", "reserved": False})
        errors = validate_inventory(changed)
        self.assertTrue(any("duplicate ASN 65100" in error for error in errors))

    def test_unapproved_prefix_overlap_is_rejected(self) -> None:
        changed = copy.deepcopy(self.inventory)
        next(prefix for prefix in changed["prefixes"] if prefix["id"] == "br1-guest")["cidr"] = "10.10.30.128/25"
        errors = validate_inventory(changed)
        self.assertTrue(any("overlapping prefixes" in error for error in errors))

    def test_invalid_peer_reference_is_rejected(self) -> None:
        changed = copy.deepcopy(self.inventory)
        changed["links"][0]["endpoints"][1]["node"] = "hq-dist-99"
        errors = validate_inventory(changed)
        self.assertTrue(any("unknown peer node 'hq-dist-99'" in error for error in errors))

    def test_addressing_plan_values_are_enforced(self) -> None:
        changed = copy.deepcopy(self.inventory)
        next(site for site in changed["sites"] if site["id"] == "br1")["aggregate"] = "10.30.0.0/16"
        errors = validate_inventory(changed)
        self.assertTrue(any("site br1 aggregate must be '10.20.0.0/16'" in error for error in errors))

    def test_schema_is_machine_readable(self) -> None:
        schema = json.loads((ROOT / "schemas" / "inventory.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertTrue(set(schema["required"]) >= {"sites", "nodes", "links", "prefixes"})
