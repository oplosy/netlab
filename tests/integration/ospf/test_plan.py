from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RENDER_PATH = ROOT / "config" / "routing" / "ospf" / "render.py"
SPEC = importlib.util.spec_from_file_location("ospf_render", RENDER_PATH)
assert SPEC and SPEC.loader
ospf_render = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ospf_render
SPEC.loader.exec_module(ospf_render)


def test_plan_has_areas_passive_vlans_and_only_routed_bfd_adjacencies() -> None:
    data = ospf_render.load_inventory()
    plan = ospf_render.build_plan(data)["nodes"]

    assert set(plan) == {"hq-edge-1", "br1-edge-1", "hq-dist-1", "hq-dist-2", "br1-dist-1", "br1-dist-2"}
    assert plan["hq-edge-1"]["area"] == 10
    assert plan["br1-edge-1"]["area"] == 20
    assert plan["hq-edge-1"]["aggregate"] == "10.10.0.0/16"
    assert plan["br1-edge-1"]["aggregate"] == "10.20.0.0/16"

    for node_id, item in plan.items():
        config = ospf_render.render_node(item)
        assert "passive-interface default" in config
        assert "redistribute bgp" not in config
        for interface in item["interfaces"]:
            if interface.get("passive"):
                assert f"interface {interface['name']}\n ip ospf area {item['area']}" in config
                section = config.split(f"interface {interface['name']}\n", 1)[1].split("\n!", 1)[0]
                assert "ip ospf passive" in section
                assert "ip ospf bfd" not in section
            else:
                assert f"interface {interface['name']}\n ip ospf area {interface['area']}" in config
                section = config.split(f"interface {interface['name']}\n", 1)[1].split("\n!", 1)[0]
                assert "no ip ospf passive" in section
                assert "ip ospf bfd" in section


def test_edge_default_is_conditional_and_site_summary_is_only_aggregate() -> None:
    configs = ospf_render.configs(ospf_render.load_inventory())
    for node_id, aggregate in (("hq-edge-1", "10.10.0.0/16"), ("br1-edge-1", "10.20.0.0/16")):
        config = configs[node_id]
        assert f"area {10 if node_id.startswith('hq') else 20} range {aggregate}" in config
        assert "default-information originate route-map OSPF-VALID-INTERNET-DEFAULT" in config
        assert "default-information originate always" not in config
        assert "match source-protocol bgp" in config
        assert "10.10.10.0/24" not in config
        assert "10.20.10.0/24" not in config


def test_config_does_not_run_ospf_on_oob_or_isp_interfaces() -> None:
    configs = ospf_render.configs(ospf_render.load_inventory())
    for config in configs.values():
        assert "interface eth0" not in config
        assert "interface eth3" not in config


def test_router_ids_must_be_unique() -> None:
    data = ospf_render.load_inventory()
    dist2 = next(node for node in data["nodes"] if node["id"] == "hq-dist-2")
    dist1 = next(node for node in data["nodes"] if node["id"] == "hq-dist-1")
    dist2["loopback"] = dist1["loopback"]
    with pytest.raises(ValueError, match="router IDs must be unique"):
        ospf_render.build_plan(data)


def test_apply_enables_only_frr_ospf_and_bfd_daemons_idempotently() -> None:
    script = (ROOT / "automation" / "roles" / "ospf" / "apply.sh").read_text(encoding="utf-8")
    for daemon in ("ospfd", "bfdd"):
        assert f'grep -qx "${{daemon}}=no" "$daemons"' in script
        assert f'grep -qx "${{daemon}}=yes" "$daemons"' in script
        assert f'pgrep -x {daemon}' in script
    assert "vtysh -f /tmp/netlab-ospf.conf" in script
    assert "ip route del default dev eth0" in script
