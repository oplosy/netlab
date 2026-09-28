from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from automation.roles.site.render import TEMPLATE, instantiate_site, read_yaml


class Branch2TemplateTests(unittest.TestCase):
    def test_branch2_metadata_is_instantiated_from_shared_template(self):
        definition = read_yaml(ROOT / "inventory/sites/br2/site.yaml")
        template = read_yaml(TEMPLATE)
        self.assertEqual(definition["template"], "branch-site")
        rendered = instantiate_site(definition["site"], template)
        self.assertEqual(rendered["site"]["aggregate"], "10.30.0.0/16")
        self.assertEqual(rendered["site"]["ospf_area"], 30)
        self.assertEqual(rendered["site"]["asn"], 65102)
        self.assertEqual(rendered["template_version"], template["version"])
        self.assertEqual(rendered["role_counts"], template["role_counts"])
        self.assertEqual(rendered["vlan_ids"], template["vlan_ids"])
        self.assertEqual(rendered["bundle_kinds"], template["bundle_kinds"])
        self.assertFalse(
            {"routes", "sessions", "counters", "observed_at"} & rendered.keys()
        )


if __name__ == "__main__":
    unittest.main()
