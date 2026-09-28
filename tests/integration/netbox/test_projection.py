"""AUTO-510 offline checks for the Git intent -> NetBox projection."""

from __future__ import annotations

import copy
import importlib.util
import ipaddress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "netbox_projection", ROOT / "automation" / "netbox" / "projection.py"
)
assert SPEC and SPEC.loader
projection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(projection)


def _want() -> dict:
    return projection.desired(projection.load_inventory())


def test_every_inventory_node_vlan_and_prefix_is_projected() -> None:
    data = projection.load_inventory()
    want = _want()
    assert set(want["devices"]) == {node["id"] for node in data["nodes"]}
    assert len(want["vlans"]) == len(data["vlans"])
    assert set(want["prefixes"]) == {
        str(ipaddress.ip_network(p["cidr"])) for p in data["prefixes"]
    }
    assert set(want["sites"]) == {s["id"] for s in data["sites"]} | {"shared"}


def test_nodes_without_a_site_land_in_the_shared_site() -> None:
    data = projection.load_inventory()
    want = _want()
    for node in data["nodes"]:
        expected = node.get("site") or "shared"
        assert want["devices"][node["id"]]["site"] == expected


def test_addresses_are_attached_to_their_interfaces_including_oob_and_loopback() -> None:
    want = _want()
    firewall = {key for key in want["ip_addresses"] if key[0] == "hq-fw-1"}
    assert ("hq-fw-1", "mgmt0", "172.31.255.35/24") in firewall
    assert ("hq-fw-1", "lo", "10.10.255.6/32") in firewall
    assert ("hq-fw-1", "to_hq_dist_1", "10.10.252.12/31") in firewall
    for device, iface, _ in want["ip_addresses"]:
        assert (device, iface) in want["interfaces"]


def test_every_projected_object_identifies_its_source() -> None:
    for model, objects in _want().items():
        for fields in objects.values():
            if "description" in fields:
                assert fields["description"].startswith(f"{projection.TAG}: "), model
                assert projection.SOURCE in fields["description"]


def test_diff_is_empty_when_netbox_matches_and_seeds_an_empty_netbox() -> None:
    want = _want()
    assert projection.diff(want, copy.deepcopy(want)) == []
    seed = projection.diff(want, {})
    assert seed and {change["action"] for change in seed} == {"create"}
    # Parents are created before children.
    order = [change["model"] for change in seed]
    assert order.index("sites") < order.index("devices") < order.index("interfaces")
    assert order.index("interfaces") < order.index("ip_addresses")


def test_netbox_only_edits_and_extra_objects_are_reported() -> None:
    want = _want()
    have = copy.deepcopy(want)
    have["devices"]["hq-fw-1"]["description"] = "edited in NetBox"
    have["vlans"][("hq", 777)] = {"name": "ROGUE", "description": ""}
    changes = projection.diff(want, have)
    assert {"model": "devices", "key": "hq-fw-1", "action": "update",
            "fields": {"description": want["devices"]["hq-fw-1"]["description"]}} in changes
    assert {"model": "vlans", "key": ("hq", 777), "action": "delete", "fields": {}} in changes
    assert len(changes) == 2
