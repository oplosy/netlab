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

    def test_distribution_peer_bundle_requires_two_l2_members(self) -> None:
        changed = copy.deepcopy(self.inventory)
        changed["links"] = [link for link in changed["links"] if link["id"] != "hq-dist-peer-2"]
        errors = validate_inventory(changed)
        self.assertTrue(any("bundle hq-dist-peer must have exactly two physical L2 member links" in error for error in errors))

        changed = copy.deepcopy(self.inventory)
        next(link for link in changed["links"] if link["id"] == "br1-dist-peer-1")["kind"] = "routed"
        errors = validate_inventory(changed)
        self.assertTrue(any("distribution peers for br1 must not use routed links" in error for error in errors))

    def test_radius_transport_is_udp(self) -> None:
        changed = copy.deepcopy(self.inventory)
        aaa = next(intent for intent in changed["service_intents"] if intent["id"] == "aaa-oob")
        next(transport for transport in aaa["transports"] if transport["port"] == 1812)["protocol"] = "tcp"
        errors = validate_inventory(changed)
        self.assertTrue(any("RADIUS port 1812 must use UDP" in error for error in errors))

    def test_access_link_requires_same_site_vlan(self) -> None:
        changed = copy.deepcopy(self.inventory)
        next(link for link in changed["links"] if link["id"] == "hq-client-users-1-access")["vlan"] = "br1-users"
        errors = validate_inventory(changed)
        self.assertTrue(any("access link hq-client-users-1-access endpoints must belong to VLAN site 'br1'" in error for error in errors))

    def test_endpoint_must_have_one_access_link(self) -> None:
        changed = copy.deepcopy(self.inventory)
        changed["links"] = [link for link in changed["links"] if link["id"] != "br1-client-guest-1-access"]
        errors = validate_inventory(changed)
        self.assertTrue(any("endpoint node br1-client-guest-1 must have exactly one access link, got 0" in error for error in errors))

    def test_site_services_use_servers_vlan_access_ports(self) -> None:
        self.assertEqual(validate_inventory(self.inventory), [])
        changed = copy.deepcopy(self.inventory)
        next(link for link in changed["links"] if link["id"] == "svc-hq-dns-1-access")["vlan"] = "hq-users"
        errors = validate_inventory(changed)
        self.assertTrue(any("site service svc-hq-dns-1 must attach to the SERVERS VLAN" in error for error in errors))

    def test_addressing_plan_values_are_enforced(self) -> None:
        changed = copy.deepcopy(self.inventory)
        next(site for site in changed["sites"] if site["id"] == "br1")["aggregate"] = "10.30.0.0/16"
        errors = validate_inventory(changed)
        self.assertTrue(any("site br1 aggregate must be '10.20.0.0/16'" in error for error in errors))

    def test_schema_is_machine_readable(self) -> None:
        schema = json.loads((ROOT / "schemas" / "inventory.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertTrue(set(schema["required"]) >= {"sites", "nodes", "links", "prefixes", "bundles"})
