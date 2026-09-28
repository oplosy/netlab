"""AUTO-530 offline checks for snapshot normalization and drift comparison."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "netlab_snapshot", ROOT / "automation" / "compliance" / "snapshot.py"
)
assert SPEC and SPEC.loader
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


def test_vrrp_virtual_addresses_come_from_intent() -> None:
    volatile = snapshot.vrrp_addresses(snapshot.load_inventory())
    assert {"10.10.10.1", "10.20.10.1", "10.30.99.1"} <= volatile
    assert "10.10.10.2" not in volatile


def test_address_normalization_drops_vips_and_loopback_and_sorts() -> None:
    raw = (
        "2: vlan10    inet 10.10.10.2/24 brd 10.10.10.255 scope global vlan10\n"
        "2: vlan10    inet 10.10.10.1/24 scope global secondary vlan10\n"
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "5: eth1@if9    inet 10.10.252.13/31 scope global eth1\n"
    )
    normalized = snapshot.normalize("ipv4-addresses", raw, {"10.10.10.1"})
    assert normalized.splitlines() == ["eth1 10.10.252.13/31", "vlan10 10.10.10.2/24"]
    # A VIP move (failover) does not change the normalized state.
    moved = raw.replace("2: vlan10    inet 10.10.10.1/24 scope global secondary vlan10\n", "")
    assert snapshot.normalize("ipv4-addresses", moved, {"10.10.10.1"}) == normalized


def test_frr_header_lines_and_ovs_row_order_are_not_state() -> None:
    config = "Building configuration...\n\nCurrent configuration:\n!\nfrr version 8.4.4\nrouter ospf\n"
    assert snapshot.normalize("frr-running-config", config, set()).splitlines()[-1] == "router ospf"
    first = 'name,tag\n"eth2",20\n"eth1",10'
    second = 'name,tag\n"eth1",10\n"eth2",20'
    assert snapshot.normalize("ovs-ports", first, set()) == snapshot.normalize("ovs-ports", second, set())


def test_compare_reports_changed_sections_only() -> None:
    base = {"nodes": {"hq-fw-1": {"nftables": "table a", "frr-running-config": "x"}}}
    same = {"nodes": {"hq-fw-1": {"nftables": "table a", "frr-running-config": "x"}}}
    drifted = {"nodes": {"hq-fw-1": {"nftables": "table a\ntable b", "frr-running-config": "x"}}}
    assert snapshot.compare(base, same) == []
    (difference,) = snapshot.compare(base, drifted)
    assert (difference["node"], difference["section"]) == ("hq-fw-1", "nftables")
    assert "+table b" in difference["diff"]


def test_every_network_role_has_sections() -> None:
    for role in snapshot.NETWORK_ROLES:
        groups = snapshot.ROLE_SECTIONS[role]
        assert "common" in groups and all(group in snapshot.SECTIONS for group in groups)
