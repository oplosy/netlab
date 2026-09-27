"""WAN-220 policy and live dual-provider route selection checks."""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from config.routing.bgp.render import (  # noqa: E402
    PRIMARY_PROVIDER,
    PROVIDER_LOCAL_PREFERENCE,
    _interface_map,
    configs,
    load_inventory,
)

BACKUP_PROVIDER = "isp2-core-1"
PROBE_ADDRESS = "203.0.113.10"


def _link_sessions(data: dict) -> dict[str, dict[str, dict[str, str]]]:
    nodes = {node["id"]: node for node in data["nodes"]}
    sessions: dict[str, dict[str, dict[str, str]]] = {}
    for link in data["links"]:
        if link.get("kind") != "ebgp":
            continue
        edge = next((endpoint for endpoint in link["endpoints"] if "edge" in endpoint["node"]), None)
        if edge is None:
            continue
        provider = next(endpoint for endpoint in link["endpoints"] if endpoint is not edge)
        sessions.setdefault(edge["node"], {})[provider["node"]] = {
            "peer": str(ipaddress.ip_interface(provider["address"]).ip),
            "interface": _interface_map(nodes[edge["node"]])[edge["interface"]],
            "endpoint": str(ipaddress.ip_interface(edge["address"]).ip),
        }
    return sessions


def _run(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, capture_output=True, text=True)


def _vtysh(node_id: str, *commands: str) -> str:
    container = f"clab-netlab-phase-1-{node_id}"
    return _run(
        "docker",
        "exec",
        container,
        "vtysh",
        *[part for command in commands for part in ("-c", command)],
    ).stdout


def _route(node_id: str, target: str = PROBE_ADDRESS) -> str:
    container = f"clab-netlab-phase-1-{node_id}"
    result = _run("docker", "exec", container, "ip", "route", "get", target, check=False)
    return result.stdout.strip() or result.stderr.strip()


def _neighbor_routes(node_id: str, peer: str) -> set[str]:
    output = _vtysh(node_id, f"show bgp ipv4 unicast neighbors {peer} routes")
    return set(
        re.findall(
            r"^\s*[A-Za-z*>=]{1,3}\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\s",
            output,
            re.MULTILINE,
        )
    )


def _wait_route(node_id: str, peer: str, interface: str, timeout: float = 20) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = _route(node_id)
        if f"via {peer} dev {interface}" in last:
            return last
        time.sleep(0.25)
    raise AssertionError(f"{node_id} did not select {peer} on {interface}: {last}")


def test_rendered_policy_prefers_isp1_and_prepares_isp2_backup() -> None:
    data = load_inventory()
    rendered = configs(data)
    sites = {site["id"]: site for site in data["sites"]}
    sessions = _link_sessions(data)

    for edge_id, peers in sessions.items():
        edge = next(node for node in data["nodes"] if node["id"] == edge_id)
        site_id = edge["site"]
        endpoint = sites[site_id]["public_endpoint"]
        config = rendered[edge_id]
        for provider_id, peer in peers.items():
            provider_name = "ISP1" if provider_id == PRIMARY_PROVIDER else "ISP2"
            assert f"neighbor {peer['peer']} route-map {provider_name}-IN in" in config
            assert f"neighbor {peer['peer']} route-map {provider_name}-OUT out" in config
            assert (
                f"set local-preference {PROVIDER_LOCAL_PREFERENCE[provider_id]}" in config
            )
            assert f"neighbor {peer['peer']} maximum-prefix 1" in config
            if edge_id.endswith("edge-1"):
                assert f"neighbor {peer['peer']} prefix-list {site_id.upper()}-ENDPOINT out" in config
            else:
                assert f"neighbor {peer['peer']} prefix-list DENY-ALL out" in config

        assert "ip prefix-list ISP-DEFAULT seq 10 permit 0.0.0.0/0" in config
        assert "route-map ISP1-IN permit 10" in config
        assert "match ip address prefix-list ISP-DEFAULT" in config
        assert "set local-preference 200" in config
        assert "set local-preference 100" in config
        assert f"route-map ISP1-OUT permit 10" in config
        assert f"match ip address prefix-list {site_id.upper()}-ENDPOINT" in config
        assert f"route-map ISP2-OUT permit 10" in config
        if edge_id.endswith("edge-1"):
            assert f"set as-path prepend {edge['asn']} {edge['asn']}" in config
            assert f"ip address {endpoint}" in config
        else:
            assert f"ip address {endpoint}" not in config
            assert f"network {endpoint}" not in config


def test_both_isps_advertise_only_default_and_limit_neighbor_prefixes() -> None:
    data = load_inventory()
    rendered = configs(data)
    for provider_id in (PRIMARY_PROVIDER, BACKUP_PROVIDER):
        config = rendered[provider_id]
        assert "ip route 0.0.0.0/0 Null0" in config
        assert "ip prefix-list ISP-DEFAULT-ONLY seq 10 permit 0.0.0.0/0" in config
        assert "ip prefix-list ISP-DEFAULT-ONLY seq 100 deny 0.0.0.0/0 le 32" in config
        for session in _link_sessions(data):
            peer = next(
                endpoint
                for link in data["links"]
                if link.get("kind") == "ebgp"
                for endpoint in link["endpoints"]
                if endpoint["node"] == session
                and any(item["node"] == provider_id for item in link["endpoints"])
            )
            peer_ip = str(ipaddress.ip_interface(peer["address"]).ip)
            assert f"neighbor {peer_ip} maximum-prefix 1" in config
            assert f"neighbor {peer_ip} prefix-list ISP-DEFAULT-ONLY out" in config
            assert f"neighbor {peer_ip} default-originate" in config


@pytest.mark.skipif(
    os.environ.get("NETLAB_BGP_MULTIHOMING_LIVE") != "1",
    reason="requires the serialized live lab",
)
def test_live_provider_preference_failover_and_route_leak_rejection() -> None:
    data = load_inventory()
    nodes = {node["id"]: node for node in data["nodes"]}
    sessions = _link_sessions(data)
    site_endpoints = {site["public_endpoint"] for site in data["sites"]}
    down_interfaces: list[tuple[str, str]] = []
    injected_routes: list[tuple[str, int, str, list[str]]] = []

    try:
        # All four edges receive both defaults, but select ISP-1 by local preference.
        for edge_id, peers in sessions.items():
            for provider_id, peer in peers.items():
                state = _vtysh(edge_id, f"show bgp neighbors {peer['peer']}")
                assert "BGP state = Established" in state, f"{edge_id} to {provider_id} is down"
                assert _neighbor_routes(edge_id, peer["peer"]) == {"0.0.0.0/0"}, (
                    f"{edge_id} accepted an unexpected route from {provider_id}"
                )
            primary = peers[PRIMARY_PROVIDER]
            route = _wait_route(edge_id, primary["peer"], primary["interface"])
            assert f"via {primary['peer']} dev {primary['interface']}" in route
            path_detail = _vtysh(edge_id, "show bgp ipv4 unicast 0.0.0.0/0")
            assert "localpref 200" in path_detail
            assert "localpref 100" in path_detail

        # Each primary ISP-1 link can fail independently and recover to ISP-2.
        for edge_id, peers in sessions.items():
            primary = peers[PRIMARY_PROVIDER]
            backup = peers[BACKUP_PROVIDER]
            container = f"clab-netlab-phase-1-{edge_id}"
            _run("docker", "exec", container, "ip", "link", "set", "dev", primary["interface"], "down")
            down_interfaces.append((container, primary["interface"]))
            backup_route = _wait_route(edge_id, backup["peer"], backup["interface"])
            assert f"via {backup['peer']} dev {backup['interface']}" in backup_route
            _run("docker", "exec", container, "ip", "link", "set", "dev", primary["interface"], "up")
            down_interfaces.remove((container, primary["interface"]))
            _wait_route(edge_id, primary["peer"], primary["interface"], timeout=30)

        # Site routers cannot leak any extra prefix to either provider.
        for edge_id, peers in sessions.items():
            edge = nodes[edge_id]
            site_id = edge["site"]
            suffix = "1" if edge_id.endswith("edge-1") else "2"
            injected = f"10.255.220.{(10 if site_id == 'hq' else 20) + int(suffix)}/32"
            peer_ips = [peer["peer"] for peer in peers.values()]
            container = f"clab-netlab-phase-1-{edge_id}"
            commands = [
                "configure terminal",
                f"ip route {injected} Null0",
                f"router bgp {edge['asn']}",
                "address-family ipv4 unicast",
                f"network {injected}",
                "end",
                *[f"clear bgp {peer} soft out" for peer in peer_ips],
            ]
            _vtysh(edge_id, *commands)
            injected_routes.append((edge_id, edge["asn"], injected, peer_ips))
            for provider_id, peer in peers.items():
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    routes = _vtysh(provider_id, f"show bgp ipv4 unicast neighbors {peer['endpoint']} routes")
                    if injected not in routes:
                        break
                    time.sleep(0.25)
                assert injected not in routes, f"{provider_id} accepted leaked prefix {injected} from {edge_id}"

        # Providers advertise only one default; site endpoints are the only routes they accept.
        for provider_id in (PRIMARY_PROVIDER, BACKUP_PROVIDER):
            provider = next(node for node in data["nodes"] if node["id"] == provider_id)
            for edge_id, peers in sessions.items():
                expected = site_endpoints.intersection(
                    {next(site["public_endpoint"] for site in data["sites"] if site["id"] == nodes[edge_id]["site"])}
                ) if edge_id.endswith("edge-1") else set()
                output = _vtysh(provider_id, f"show bgp ipv4 unicast neighbors {peers[provider_id]['endpoint']} routes")
                advertised = {
                    f"{match.group(1)}/32"
                    for match in re.finditer(r"^\s*[A-Za-z*>=]{1,3}\s+(\d{1,3}(?:\.\d{1,3}){3})/32\s", output, re.MULTILINE)
                }
                assert advertised == expected, f"{provider_id} received {advertised} from {edge_id}"
                if edge_id.endswith("edge-1"):
                    endpoint = next(iter(expected))
                    route_line = next((line for line in output.splitlines() if endpoint in line), "")
                    expected_as_count = 1 if provider_id == PRIMARY_PROVIDER else 3
                    assert route_line.count(str(nodes[edge_id]["asn"])) == expected_as_count, (
                        f"unexpected AS path at {provider_id} for {edge_id}: {route_line}"
                    )
    finally:
        for container, interface in reversed(down_interfaces):
            _run("docker", "exec", container, "ip", "link", "set", "dev", interface, "up", check=False)
        for edge_id, asn, injected, peer_ips in reversed(injected_routes):
            commands = [
                "configure terminal",
                f"router bgp {asn}",
                "address-family ipv4 unicast",
                f"no network {injected}",
                "exit-address-family",
                "exit",
                f"no ip route {injected} Null0",
                "end",
                *[f"clear bgp {peer} soft out" for peer in peer_ips],
            ]
            _vtysh(edge_id, *commands)
