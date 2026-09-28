"""AUTO-520 offline checks: deterministic render and generated Ansible inventory."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _module(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str((ROOT / relative).parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


render_all = _module("automation/render/render_all.py", "render_all")
render_inventory = _module("automation/inventory/render_inventory.py", "render_inventory")
DATA = json.loads((ROOT / "inventory" / "inventory.yaml").read_text(encoding="utf-8"))


def test_render_is_deterministic_identifies_generators_and_hosts_yml_is_current() -> None:
    assert render_all.check(DATA) == []


def test_render_covers_every_engine_output() -> None:
    files = render_all.render(DATA)
    prefixes = {path.split("/", 1)[0] for path in files}
    assert prefixes == {"inventory", "frr", "nftables", "swanctl", "keepalived", "ovs"}
    assert "nftables/hq-fw-1-inet.nft" in files
    assert "queue num 0" in files["nftables/hq-fw-1-inet.nft"]


def test_inventory_groups_every_node_once_by_role_and_site() -> None:
    inventory = yaml.safe_load((ROOT / "automation" / "inventory" / "hosts.yml").read_text())
    root = inventory["all"]
    assert set(root["hosts"]) == {node["id"] for node in DATA["nodes"]}
    for node in DATA["nodes"]:
        memberships = [g for g, body in root["children"].items() if node["id"] in body["hosts"]]
        role_groups = [g for g in memberships if g.startswith("role_")]
        site_groups = [g for g in memberships if g.startswith("site_")]
        assert role_groups == [f"role_{node['role']}"], node["id"]
        assert site_groups == [f"site_{node.get('site') or 'shared'}"], node["id"]
    fw = root["hosts"]["hq-fw-1"]
    assert fw["netlab_container"] == "clab-netlab-phase-1-hq-fw-1"
    assert fw["netlab_oob"] == "172.31.255.35"


def test_dynamic_inventory_format_preserves_hosts_groups_and_vars() -> None:
    netbox_inventory = _module("automation/inventory/netbox_inventory.py", "netbox_inventory")
    structure = render_inventory.build(render_inventory.hosts_from_intent(DATA))
    script = netbox_inventory.to_script_json(structure)
    assert set(script["_meta"]["hostvars"]) == {node["id"] for node in DATA["nodes"]}
    assert script["role_firewall"]["hosts"] == ["hq-fw-1"]
    assert script["_meta"]["hostvars"]["hq-fw-1"]["netlab_role"] == "firewall"
    assert script["_meta"]["hostvars"]["hq-fw-1"]["ansible_connection"] == "local"
