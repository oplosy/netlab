#!/usr/bin/env python3
"""Measure OSPF summaries, passive interfaces, BFD failure detection, and recovery."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "config" / "routing" / "ospf"))
from render import build_plan, load_inventory  # noqa: E402


def _exec(container: str, *argv: str, input_text: str | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", "exec", *( ["-i"] if input_text is not None else []), container, *argv],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(f"{container} {' '.join(argv)}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout + result.stderr


def _vtysh(node: str, *commands: str) -> str:
    container = f"clab-netlab-phase-1-{node}"
    args = ["vtysh"]
    for command in commands:
        args.extend(["-c", command])
    return _exec(container, *args)


def _neighbors(node: str) -> dict[str, str]:
    output = _vtysh(node, "show ip ospf neighbor")
    result: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 7 and re.fullmatch(r"\d+(?:\.\d+){3}", fields[5]):
            # FRR columns: Neighbor ID, Pri, State, Up Time, Dead Time, Address, Interface.
            result[fields[5]] = fields[2]
    return result


def _route(node: str, prefix: str) -> str:
    return _vtysh(node, f"show ip route {prefix}")


def _bfd_up(node: str, expected: set[str]) -> bool:
    output = _vtysh(node, "show bfd peers")
    blocks = re.split(r"\n\s*peer ", output)
    statuses: dict[str, str] = {}
    for block in blocks[1:]:
        address = re.match(r"(\d+(?:\.\d+){3})\b", block)
        status = re.search(r"Status:\s*(\S+)", block, re.IGNORECASE)
        if address and status:
            statuses[address.group(1)] = status.group(1).lower()
    return all(statuses.get(peer) == "up" for peer in expected)


def _bgp_default(node: str) -> bool:
    return 'Known via "bgp"' in _route(node, "0.0.0.0/0")


def _own_default_lsa(node: str, router_id: str) -> bool:
    output = _vtysh(node, "show ip ospf database external")
    for block in re.split(r"(?=LS age:)", output):
        if "Link State ID: 0.0.0.0" in block and f"Advertising Router: {router_id}" in block:
            return True
    return False


def _wait(predicate, timeout: float, interval: float = 0.1) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if predicate():
            return time.monotonic() - started
        time.sleep(interval)
    raise TimeoutError(f"condition did not converge within {timeout:.1f} seconds")


def _assert_summary(node: str, area0_site: str) -> str:
    database = _vtysh(node, "show ip ospf database summary")
    aggregate = "10.10.0.0" if area0_site == "hq" else "10.20.0.0"
    specific = "10.10.10.0" if area0_site == "hq" else "10.20.10.0"
    assert aggregate in database, f"Area 0 database on {node} lacks site summary {aggregate}/16"
    assert specific not in database, f"Area 0 database on {node} contains unsummarized VLAN prefix {specific}/24"
    return database


def run() -> dict[str, object]:
    if os.environ.get("NETLAB_OSPF_LIVE") != "1":
        raise SystemExit("set NETLAB_OSPF_LIVE=1 to confirm exclusive access to the running lab")

    data = load_inventory()
    plan = build_plan(data)["nodes"]
    expected_neighbors: dict[str, set[str]] = {}
    expected_bfd: dict[str, set[str]] = {}
    for node_id in plan:
        expected_neighbors[node_id] = set()
        expected_bfd[node_id] = set()
    for link in data["links"]:
        if link.get("kind") not in {"routed", "xfrm"}:
            continue
        for local in link["endpoints"]:
            if local["node"] not in plan:
                continue
            remote = next(endpoint for endpoint in link["endpoints"] if endpoint["node"] != local["node"])
            peer = str(ipaddress.ip_interface(remote["address"]).ip)
            expected_neighbors[local["node"]].add(peer)
            expected_bfd[local["node"]].add(peer)

    deadlines = time.monotonic() + 45
    while time.monotonic() < deadlines:
        try:
            full = all(all("Full" in _neighbors(node).get(peer, "") for peer in peers)
                       for node, peers in expected_neighbors.items())
            passive_clean = all(
                not re.search(r"\b(?:eth0|eth3|vlan\d+)\b", _vtysh(node, "show ip ospf neighbor"))
                for node in expected_neighbors
            )
            bfd_up = all(_bfd_up(node, peers) for node, peers in expected_bfd.items())
            if full and passive_clean and bfd_up:
                break
        except (RuntimeError, AssertionError):
            pass
        time.sleep(0.5)
    else:
        raise TimeoutError("OSPF adjacencies/BFD did not become Full/Up within 45 seconds")

    summaries = {
        "hq-edge-1": _assert_summary("hq-edge-1", "br1"),
        "br1-edge-1": _assert_summary("br1-edge-1", "hq"),
    }
    remote_routes = {
        "hq": _route("hq-dist-1", "10.20.0.0/16"),
        "br1": _route("br1-dist-1", "10.10.0.0/16"),
    }
    assert "10.20.0.0/16" in remote_routes["hq"] and "10.10.0.0/16" in remote_routes["br1"], remote_routes
    edge_defaults = {
        "hq": _route("hq-edge-1", "0.0.0.0/0"),
        "br1": _route("br1-edge-1", "0.0.0.0/0"),
    }
    site_defaults = {
        "hq": _route("hq-dist-1", "0.0.0.0/0"),
        "br1": _route("br1-dist-1", "0.0.0.0/0"),
    }
    site_bgp_default = any(_bgp_default(node) for node in ("hq-edge-1", "br1-edge-1"))
    own_default_lsa = {
        node_id: _own_default_lsa(node_id, item["router_id"])
        for node_id, item in plan.items()
        if item["role"] == "edge"
    }
    for node_id, item in plan.items():
        if item["role"] == "edge":
            assert own_default_lsa[node_id] == _bgp_default(node_id), (
                f"{node_id} default LSA origination does not track a BGP default"
            )
    if site_bgp_default:
        assert all('Known via "ospf"' in site_defaults[site_id] for site_id in ("hq", "br1")), site_defaults
    else:
        assert all('Known via "ospf"' not in site_defaults[site_id] for site_id in ("hq", "br1")), site_defaults

    # Drop only BFD control packets on one routed adjacency while leaving the
    # interface and OSPF hellos up. This exercises BFD-triggered OSPF failover.
    test_edge = "hq-edge-1"
    test_peer = "10.10.252.1"
    test_interface = "eth1"
    table = "ospf130_" + uuid.uuid4().hex[:8]
    container = f"clab-netlab-phase-1-{test_edge}"
    _exec(container, "nft", "add", "table", "inet", table)
    try:
        _exec(container, "nft", "add", "chain", "inet", table, "input", "{", "type", "filter", "hook", "input", "priority", "-5", ";", "policy", "accept", ";", "}")
        _exec(container, "nft", "add", "chain", "inet", table, "output", "{", "type", "filter", "hook", "output", "priority", "-5", ";", "policy", "accept", ";", "}")
        _exec(container, "nft", "add", "rule", "inet", table, "input", "iifname", test_interface, "udp", "dport", "3784", "drop")
        _exec(container, "nft", "add", "rule", "inet", table, "output", "oifname", test_interface, "udp", "sport", "3784", "drop")
        detection = _wait(lambda: "Full" not in _neighbors(test_edge).get(test_peer, ""), timeout=3.0)
        alternate = _route(test_edge, "10.10.10.0/24")
        assert "10.10.252.3, via eth2" in alternate, f"surviving VLAN route lacks alternate dist adjacency: {alternate}"
    finally:
        _exec(container, "nft", "delete", "table", "inet", table, check=False)

    recovery = _wait(lambda: "Full" in _neighbors(test_edge).get(test_peer, ""), timeout=10.0)
    _assert_summary("hq-edge-1", "br1")
    _assert_summary("br1-edge-1", "hq")
    return {
        "source": "tests/integration/ospf/measure.py",
        "summary_lsa_views": {node: value for node, value in summaries.items()},
        "remote_site_summary_routes": remote_routes,
        "conditional_site_defaults": site_defaults,
        "edge_default_routes": edge_defaults,
        "edge_self_originated_default_lsa": own_default_lsa,
        "default_origin_tracks_bgp_route_source": True,
        "passive_interface_adjacency_check": "no adjacency on eth0/eth3 or VLAN SVIs",
        "bfd_test": {
            "node": test_edge,
            "peer": test_peer,
            "interface": test_interface,
            "failure_detection_seconds": round(detection, 3),
            "alternate_site_vlan_route": alternate.strip(),
            "recovery_seconds": round(recovery, 3),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    serialized = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
