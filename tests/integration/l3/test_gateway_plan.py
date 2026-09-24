from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from config.gateway.apply import build_plan, load_inventory


def _plan() -> dict:
    return build_plan(load_inventory(ROOT / "inventory" / "inventory.yaml"))


def test_every_distribution_gets_all_vips_and_inventory_addresses() -> None:
    plan = _plan()
    assert len(plan["nodes"]) == 4
    assert len(plan["routed_endpoints"]) == 8
    for node in plan["nodes"]:
        assert [gateway["vlan_id"] for gateway in node["gateways"]] == [10, 20, 30, 99]
        assert len(node["routed_interfaces"]) == 1
        assert node["routed_interfaces"][0]["interface"] == "eth1"
        for gateway in node["gateways"]:
            assert gateway["ovs_port"] == f"svi{gateway['vlan_id']}"
            if node["node"].endswith("dist-1"):
                assert gateway["address"].endswith(".2/24")
            else:
                assert gateway["address"].endswith(".3/24")
            assert gateway["virtual_ip"].endswith(".1/24")


def test_vrrp_master_preference_matches_rstp_distribution_root() -> None:
    plan = _plan()
    for node in plan["nodes"]:
        expected_priority, expected_state = (
            (150, "MASTER") if node["node"].endswith("dist-1") else (100, "BACKUP")
        )
        for gateway in node["gateways"]:
            assert gateway["priority"] == expected_priority
            assert gateway["state"] == expected_state
            assert f"virtual_router_id {gateway['vlan_id']}" in node["keepalived_config"]
            assert f"unicast_src_ip {gateway['address'].split('/')[0]}" in node["keepalived_config"]
            assert gateway["unicast_peer"] != gateway["unicast_src_ip"]
        assert "check_unicast_src" in node["keepalived_config"]
        assert "advert_int 1" in node["keepalived_config"]


def test_dhcp_relay_hook_exposes_vlan_and_inventory_service_target() -> None:
    plan = _plan()
    hooks = plan["dhcp_relay_hooks"]
    assert len(hooks) == 16
    assert all(hook["interface"].startswith("vlan") for hook in hooks)
    assert len({hook["node"] for hook in hooks}) == 4
    assert all(hook["server"] is None and hook["enabled"] is False for hook in hooks)


def test_guest_forwarding_blocks_enterprise_and_oob_destinations() -> None:
    plan = _plan()
    for node in plan["nodes"]:
        rules = node["nftables_config"]
        assert 'iifname "vlan30" ip daddr { 10.10.0.0/16, 10.20.0.0/16, 172.31.255.0/24 } counter drop' in rules
        assert "chain input" in rules
        site_prefix = "10.10" if node["site"] == "hq" else "10.20"
        assert f'iifname "vlan30" ip saddr {{ {site_prefix}.30.2, {site_prefix}.30.3 }} ip protocol 112 accept' in rules
        assert "chain forward" in rules

def test_changed_dhcp_service_address_is_used_without_hardcoding() -> None:
    data = load_inventory(ROOT / "inventory" / "inventory.yaml")
    dhcp = next(node for node in data["nodes"] if node.get("service") == "dhcp")
    dhcp["service_address"] = "203.0.113.12/32"
    plan = build_plan(data)
    assert {hook["server"] for hook in plan["dhcp_relay_hooks"]} == {"203.0.113.12"}
