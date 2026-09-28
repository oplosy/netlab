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
    assert len(plan["nodes"]) == 6
    assert len(plan["routed_endpoints"]) == 24
    for node in plan["nodes"]:
        assert [gateway["vlan_id"] for gateway in node["gateways"]] == [10, 20, 30, 99]
        # HQ distribution reaches the edges only through hq-fw-1 (ADR 0017).
        expected_uplinks = 1 if node["node"].startswith("hq-") else 2
        assert len(node["routed_interfaces"]) == expected_uplinks
        assert all(
            item["interface"].startswith("eth") for item in node["routed_interfaces"]
        )
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
            assert (
                f"virtual_router_id {gateway['vlan_id']}" in node["keepalived_config"]
            )
            assert (
                f"unicast_src_ip {gateway['address'].split('/')[0]}"
                in node["keepalived_config"]
            )
            assert gateway["unicast_peer"] != gateway["unicast_src_ip"]
        assert "check_unicast_src" in node["keepalived_config"]
        assert "advert_int 1" in node["keepalived_config"]
        assert (
            'notify_master "/usr/local/sbin/netlab-dhcp-relay-reconcile"'
            in node["keepalived_config"]
        )
        assert (
            'notify_backup "/usr/local/sbin/netlab-dhcp-relay-reconcile"'
            in node["keepalived_config"]
        )
        assert (
            'notify_fault "/usr/local/sbin/netlab-dhcp-relay-reconcile"'
            in node["keepalived_config"]
        )


def test_vrrp_tracks_inventory_derived_remote_ospf_summary() -> None:
    plan = _plan()
    expected = {"hq": "10.20.0.0/16", "br1": "10.10.0.0/16", "br2": "10.10.0.0/16"}
    checker = (ROOT / "config" / "gateway" / "check_ospf_route.sh").read_text(
        encoding="utf-8"
    )
    assert 'ip -4 route show exact "$1"' in checker
    assert "proto ospf" in checker
    for node in plan["nodes"]:
        remote = expected[node["site"]]
        config = node["keepalived_config"]
        assert node["remote_aggregate"] == remote
        assert "enable_script_security" in config
        assert "script_user root" in config
        assert f'script "/usr/local/sbin/netlab-check-ospf-route {remote}"' in config
        assert "  interval 1\n  timeout 1\n  fall 1\n  rise 2\n  weight -60" in config
        assert config.count("    chk_remote_ospf_route") == 4
        assert config.count("  track_script {") == 4


def test_dhcp_relay_hook_exposes_vlan_and_inventory_service_target() -> None:
    plan = _plan()
    hooks = plan["dhcp_relay_hooks"]
    assert len(hooks) == 24
    assert all(hook["interface"].startswith("vlan") for hook in hooks)
    assert len({hook["node"] for hook in hooks}) == 6
    servers = {"hq": "10.10.20.10", "br1": "10.20.20.10", "br2": "10.30.20.10"}
    assert all(hook["server"] == servers[hook["site"]] for hook in hooks)
    assert all(hook["enabled"] is True for hook in hooks)
    assert all(hook["virtual_ip"].endswith(".1/24") for hook in hooks)


def test_guest_forwarding_blocks_enterprise_and_oob_destinations() -> None:
    plan = _plan()
    for node in plan["nodes"]:
        rules = node["nftables_config"]
        assert (
            'iifname "vlan30" ip daddr { 10.10.0.0/16, 10.20.0.0/16, 10.30.0.0/16, 172.31.255.0/24 } counter drop'
            in rules
        )
        assert "chain input" in rules
        site_prefix = {"hq": "10.10", "br1": "10.20", "br2": "10.30"}[node["site"]]
        assert (
            f'iifname "vlan30" ip saddr {{ {site_prefix}.30.2, {site_prefix}.30.3 }} ip protocol 112 accept'
            in rules
        )
        assert "chain forward" in rules


def test_changed_dhcp_service_address_is_used_without_hardcoding() -> None:
    data = load_inventory(ROOT / "inventory" / "inventory.yaml")
    baseline = build_plan(data)
    baseline_servers = {
        hook["site"]: hook["server"] for hook in baseline["dhcp_relay_hooks"]
    }
    dhcp = next(node for node in data["nodes"] if node.get("service") == "dhcp")
    dhcp["service_address"] = "203.0.113.12/32"
    plan = build_plan(data)
    target_site = dhcp["site"]
    assert {
        hook["server"]
        for hook in plan["dhcp_relay_hooks"]
        if hook["site"] == target_site
    } == {"203.0.113.12"}
    assert (
        len([hook for hook in plan["dhcp_relay_hooks"] if hook["site"] == target_site])
        == 8
    )
    assert all(
        hook["server"]
        == (
            "203.0.113.12"
            if hook["site"] == target_site
            else baseline_servers[hook["site"]]
        )
        for hook in plan["dhcp_relay_hooks"]
    )
