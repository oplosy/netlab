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
PING_STREAM = ROOT / "tests" / "integration" / "ospf" / "ping_stream.py"
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


def _vlan10_addresses(node: str) -> str:
    container = f"clab-netlab-phase-1-{node}"
    return _exec(container, "ip", "-4", "addr", "show", "dev", "vlan10")


def _kernel_has_ospf_route(node: str, prefix: str) -> bool:
    container = f"clab-netlab-phase-1-{node}"
    output = _exec(container, "ip", "-4", "route", "show", "exact", prefix)
    return any(re.search(r"\bproto\s+ospf\b", line) for line in output.splitlines())


def _vip_owners(addresses: dict[str, str], vip: str) -> list[str]:
    pattern = re.compile(rf"\binet {re.escape(vip)}/\d+\b")
    return sorted(node for node, output in addresses.items() if pattern.search(output))


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


def _wait_optional(predicate, timeout: float, interval: float = 0.1) -> float | None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if predicate():
            return time.monotonic() - started
        time.sleep(interval)
    return None


def _icmp_probe(container: str, target: str, *, count: int) -> dict[str, object]:
    output = _exec(
        container,
        "python3", "-", target, "--count", str(count),
        input_text=PING_STREAM.read_text(encoding="utf-8"),
    )
    return json.loads(output)


def _start_icmp_probe(container: str, target: str, *, count: int) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        ["docker", "exec", "-i", container, "python3", "-", target, "--count", str(count)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(PING_STREAM.read_text(encoding="utf-8"))
    process.stdin.close()
    process.stdin = None
    return process


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
    # HQ distribution peers only with hq-fw-1 (ADR 0017): break BFD on the
    # firewall's link to hq-dist-1 and expect the hq-dist-2 link to carry on.
    test_edge = "hq-fw-1"
    test_peer = "10.10.252.13"
    test_interface = "eth3"
    probe_node = "br1-edge-1"
    probe_container = f"clab-netlab-phase-1-{probe_node}"
    probe_targets = {
        "hq-dist-1": "10.10.10.2",
        "hq-dist-2": "10.10.10.3",
        "hq-active-vip": "10.10.10.1",
    }
    probe_baselines = {
        node: _icmp_probe(probe_container, address, count=3)
        for node, address in probe_targets.items()
    }
    assert all(result["received"] == 3 for result in probe_baselines.values()), (
        f"continuity probe baseline failed: {probe_baselines}"
    )
    target_routers = ("hq-dist-1", "hq-dist-2")
    target_routes_before = {node: _route(node, "10.255.0.1") for node in target_routers}
    vip_owners_before = {node: _vlan10_addresses(node) for node in target_routers}
    gateway_route_before = _route(test_edge, "10.10.10.1")
    probe_processes = {
        node: _start_icmp_probe(probe_container, address, count=80)
        for node, address in probe_targets.items()
    }
    time.sleep(0.4)
    table = "ospf130_" + uuid.uuid4().hex[:8]
    container = f"clab-netlab-phase-1-{test_edge}"
    try:
        _exec(container, "nft", "add", "table", "inet", table)
        _exec(container, "nft", "add", "chain", "inet", table, "input", "{", "type", "filter", "hook", "input", "priority", "-5", ";", "policy", "accept", ";", "}")
        _exec(container, "nft", "add", "chain", "inet", table, "output", "{", "type", "filter", "hook", "output", "priority", "-5", ";", "policy", "accept", ";", "}")
        _exec(container, "nft", "add", "rule", "inet", table, "input", "iifname", test_interface, "udp", "dport", "3784", "drop")
        _exec(container, "nft", "add", "rule", "inet", table, "output", "oifname", test_interface, "udp", "sport", "3784", "drop")
        detection = _wait(lambda: "Full" not in _neighbors(test_edge).get(test_peer, ""), timeout=3.0)
        alternate = _route(test_edge, "10.10.10.0/24")
        assert "10.10.252.15, via eth4" in alternate, f"surviving VLAN route lacks alternate dist adjacency: {alternate}"
        vip_takeover = _wait_optional(
            lambda: _vip_owners(
                {node: _vlan10_addresses(node) for node in target_routers}, "10.10.10.1"
            ) == ["hq-dist-2"],
            timeout=5.0,
        )
        target_routes_during = {node: _route(node, "10.255.0.1") for node in target_routers}
        vip_owners_during = {node: _vlan10_addresses(node) for node in target_routers}
        gateway_route_during = _route(test_edge, "10.10.10.1")
    finally:
        _exec(container, "nft", "delete", "table", "inet", table, check=False)

    fault_cleared_at = time.monotonic()
    recovery = _wait(lambda: "Full" in _neighbors(test_edge).get(test_peer, ""), timeout=10.0)
    route_recovery_after_edge_full = _wait(
        lambda: all(
            "10.255.0.0/31" in _route(node, "10.255.0.1")
            for node in target_routers
        ),
        timeout=30.0,
    )
    target_route_recovery = recovery + route_recovery_after_edge_full
    target_routes_after = {node: _route(node, "10.255.0.1") for node in target_routers}
    vip_owners_after = {node: _vlan10_addresses(node) for node in target_routers}
    gateway_route_after = _route(test_edge, "10.10.10.1")
    probe_streams: dict[str, dict[str, object]] = {}
    try:
        for node, process in probe_processes.items():
            probe_stdout, probe_stderr = process.communicate(timeout=25)
            assert process.returncode == 0, f"{node} continuity probe failed: {probe_stderr.strip() or probe_stdout.strip()}"
            probe_streams[node] = json.loads(probe_stdout)
    except (AssertionError, subprocess.TimeoutExpired):
        for process in probe_processes.values():
            if process.poll() is None:
                process.kill()
                process.communicate()
        raise
    preferred_vip_return = _wait_optional(
        lambda: _kernel_has_ospf_route("hq-dist-1", "10.20.0.0/16")
        and _vip_owners(
            {node: _vlan10_addresses(node) for node in target_routers}, "10.10.10.1"
        ) == ["hq-dist-1"],
        timeout=20.0,
    )
    preferred_vip_returned_at = time.monotonic()
    vip_owners_settled = {node: _vlan10_addresses(node) for node in target_routers}
    for stream in probe_streams.values():
        stream.pop("reply_timestamps", None)
    surviving_path_streams = {
        name: probe_streams[name] for name in ("hq-dist-2", "hq-active-vip")
    }
    packet_interruptions = {
        name: max(0.0, stream["max_reply_gap_seconds"] - stream["interval_seconds"])
        for name, stream in surviving_path_streams.items()
    }
    packet_continuity_passed = all(value <= 3.0 for value in packet_interruptions.values())
    expected_vip_owner = "hq-dist-2"
    vip_owner_during = _vip_owners(vip_owners_during, "10.10.10.1")
    vip_failover_passed = vip_takeover is not None and vip_owner_during == [expected_vip_owner]
    packet_interruption = max(packet_interruptions.values())
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
            "continuity_probe_source": probe_node,
            "continuity_probe_targets": probe_targets,
            "continuity_probe_baselines": probe_baselines,
            "continuity_probe_streams": probe_streams,
            "surviving_path_packet_interruptions_seconds": packet_interruptions,
            "target_return_routes_before": target_routes_before,
            "target_return_routes_during": target_routes_during,
            "target_return_routes_after": target_routes_after,
            "hq_gateway_forward_routes_before": gateway_route_before,
            "hq_gateway_forward_routes_during": gateway_route_during,
            "hq_gateway_forward_routes_after": gateway_route_after,
            "hq_vlan10_addresses_before": vip_owners_before,
            "hq_vlan10_addresses_during": vip_owners_during,
            "hq_vlan10_addresses_after": vip_owners_after,
            "hq_vlan10_addresses_settled": vip_owners_settled,
            "hq_vlan10_vip_owners": {
                "before": _vip_owners(vip_owners_before, "10.10.10.1"),
                "during": vip_owner_during,
                "after": _vip_owners(vip_owners_after, "10.10.10.1"),
                "settled": _vip_owners(vip_owners_settled, "10.10.10.1"),
            },
            "preferred_vip_return_wait_after_probe_streams_seconds": (
                round(preferred_vip_return, 3)
                if preferred_vip_return is not None
                else None
            ),
            "preferred_vip_return_seconds_after_bfd_unblock": (
                round(preferred_vip_returned_at - fault_cleared_at, 3)
                if preferred_vip_return is not None
                else None
            ),
            "preferred_vip_returned_after_route_recovery": preferred_vip_return is not None,
            "vip_takeover_seconds_after_bfd_detection": (
                round(vip_takeover, 3) if vip_takeover is not None else None
            ),
            "target_route_recovery_seconds_after_bfd_unblock": round(target_route_recovery, 3),
            "estimated_packet_interruption_seconds": round(packet_interruption, 3),
            "packet_continuity_threshold_passed": packet_continuity_passed,
            "vrrp_failover_threshold_passed": vip_failover_passed,
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
    accepted = (
        result["bfd_test"]["packet_continuity_threshold_passed"]
        and result["bfd_test"]["vrrp_failover_threshold_passed"]
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
