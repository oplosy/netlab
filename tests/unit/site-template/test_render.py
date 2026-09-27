from __future__ import annotations
import sys
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from automation.roles.site.render import (
    TEMPLATE,
    INVENTORY,
    read_yaml,
    render_site,
    validate_template,
)


class BranchSiteTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = read_yaml(INVENTORY)
        cls.template = read_yaml(TEMPLATE)
        cls.rendered = render_site(cls.data, "br1", cls.template)

    def test_branch1_projection_matches_authoritative_inventory(self):
        data = self.data
        rendered = self.rendered
        ids = {node["id"] for node in rendered["nodes"]}
        self.assertEqual(
            rendered["site"],
            next(site for site in data["sites"] if site["id"] == "br1"),
        )
        self.assertEqual(
            rendered["nodes"],
            [node for node in data["nodes"] if node.get("site") == "br1"],
        )
        self.assertEqual(
            rendered["vlans"],
            [vlan for vlan in data["vlans"] if vlan.get("site") == "br1"],
        )
        self.assertEqual(
            rendered["prefixes"],
            [prefix for prefix in data["prefixes"] if prefix.get("owner") == "br1"],
        )
        self.assertEqual(
            rendered["bundles"],
            [bundle for bundle in data["bundles"] if bundle.get("site") == "br1"],
        )
        self.assertTrue(
            all(
                all(endpoint["node"] in ids for endpoint in link["endpoints"])
                or link["kind"] == "ebgp"
                for link in rendered["links"]
            )
        )
        self.assertEqual(
            [node["id"] for node in rendered["nodes"] if node["role"] == "edge"],
            ["br1-edge-1", "br1-edge-2"],
        )

    def test_template_has_no_runtime_state(self):
        self.assertEqual(self.template["runtime_fields"], [])
        validate_template(self.template)

    def test_template_rejects_generated_route_state(self):
        bad = dict(self.template)
        bad["routes"] = []
        with self.assertRaisesRegex(ValueError, "runtime-only"):
            validate_template(bad)

    def test_site_with_different_role_shape_is_rejected(self):
        data = dict(self.data)
        data["nodes"] = [
            node for node in self.data["nodes"] if node["id"] != "br1-edge-2"
        ]
        with self.assertRaisesRegex(ValueError, "role counts"):
            render_site(data, "br1", self.template)


if __name__ == "__main__":
    unittest.main()
