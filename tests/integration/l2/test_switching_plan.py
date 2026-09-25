"""Static evidence checks for the inventory-derived L2 switching plan."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from config.switching.apply import build_plan
from tests.integration.l2.measure import (
    FOLLOW_ON_TIMEOUT_SECONDS,
    PING_INTERVAL,
    POST_RECOVERY_HOLD_SECONDS,
)


def _inventory() -> dict:
    return json.loads((ROOT / "inventory" / "inventory.yaml").read_text(encoding="utf-8"))


def test_every_lacp_bundle_has_two_members_and_one_declared_peer_pair() -> None:
    data = _inventory()
    plan = build_plan(data)
    bundle_config = [item for item in plan if "bundle" in item]
    assert len(bundle_config) == 12
    for item in bundle_config:
        assert len(item["members"]) == 2
        assert item["argv"][0:3] == ["ovs-vsctl", "--may-exist", "add-port"]
        assert "lacp=active" not in item["argv"]
        assert "vlan_mode=trunk" in item["argv"]
        assert "trunks=10,20,30,99" in item["argv"]


def test_access_ports_are_single_vlan_and_guest_is_not_shared() -> None:
    data = _inventory()
    plan = build_plan(data)
    access = [item for item in plan if "access_vlan" in item]

    nodes = {node["id"]: node for node in data["nodes"]}
    switch_ids = {node_id for node_id, node in nodes.items() if node.get("role") in {"dist", "access"}}
    vlan_ids = {vlan["id"]: int(vlan["vlan_id"]) for vlan in data["vlans"]}
    physical_kinds = {"routed", "ebgp", "l2", "access"}
    ports: dict[tuple[str, str], str] = {}
    for node_id, node in nodes.items():
        physical = [interface for interface in node.get("interfaces", [])
                    if interface.get("kind") in physical_kinds]
        ports.update({(node_id, interface["name"]): f"eth{index}"
                      for index, interface in enumerate(physical, start=1)})

    expected = []
    for link in data["links"]:
        if link.get("kind") != "access":
            continue
        switch_end = next(endpoint for endpoint in link["endpoints"] if endpoint["node"] in switch_ids)
        expected.append((switch_end["node"], ports[(switch_end["node"], switch_end["interface"])], vlan_ids[link["vlan"]]))

    actual = [(item["node"], item["argv"][4], item["access_vlan"]) for item in access]
    assert sorted(actual) == sorted(expected)
    for item in access:
        assert "vlan_mode=access" in item["argv"]
        assert f"tag={item['access_vlan']}" in item["argv"]
        assert "other_config:rstp-port-admin-edge=true" in item["argv"]


def test_rstp_root_is_deterministic_and_site_local() -> None:
    data = _inventory()
    plan = build_plan(data)
    bridge = {item["node"]: item["argv"] for item in plan if item["argv"][1] == "set"}
    assert "other_config:rstp-priority=4096" in bridge["hq-dist-1"]
    assert "other_config:rstp-priority=8192" in bridge["hq-dist-2"]
    assert "other_config:rstp-priority=4096" in bridge["br1-dist-1"]
    assert "other_config:rstp-priority=8192" in bridge["br1-dist-2"]
    assert "other_config:rstp-priority=32768" in bridge["hq-access-1"]
    assert all("netlab-phase-1-" in item["container"] for item in plan)
    assert sum(bool(item.get("bridge_setup")) for item in plan) == 6


def test_distribution_peer_bundle_cannot_span_sites_or_access_nodes() -> None:
    data = _inventory()
    plan = build_plan(data)
    bundles = {bundle["id"]: bundle for bundle in data["bundles"]}
    configured_ids = {item["bundle"] for item in plan if "bundle" in item}
    assert configured_ids == set(bundles)
    for bundle in bundles.values():
        roles = {
            node["id"]: node["role"]
            for node in data["nodes"]
            if node["id"] in bundle["peer_nodes"]
        }
        peer_sites = {
            node["site"]
            for node in data["nodes"]
            if node["id"] in bundle["peer_nodes"]
        }
        assert len(roles) == 2
        assert len(peer_sites) == 1
        if bundle["kind"] == "distribution-peer":
            assert set(roles.values()) == {"dist"}
        else:
            assert sorted(roles.values()) == ["access", "dist"]


def test_plan_rejects_bundle_whose_declared_peer_is_on_another_site() -> None:
    data = deepcopy(_inventory())
    data["bundles"][0]["peer_nodes"] = ["hq-access-1", "br1-dist-1"]
    with pytest.raises(ValueError, match="does not match its two declared peers"):
        build_plan(data)


def test_plan_has_only_scoped_container_operations() -> None:
    data = _inventory()
    plan = build_plan(data)
    assert plan
    assert all(item["argv"][0] == "ovs-vsctl" for item in plan)
    assert {item["node"] for item in plan} == {
        "hq-dist-1", "hq-dist-2", "hq-access-1", "br1-dist-1", "br1-dist-2", "br1-access-1"
    }


def test_follow_on_ping_timeout_covers_the_post_recovery_hold() -> None:
    assert FOLLOW_ON_TIMEOUT_SECONDS >= POST_RECOVERY_HOLD_SECONDS + PING_INTERVAL
