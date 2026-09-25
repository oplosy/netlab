"""Static and live acceptance checks for Phase 1 eBGP policy."""

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
from config.routing.bgp.render import configs, load_inventory  # noqa: E402


def _peer(data: dict, site_id: str) -> str:
    node_id = {"hq": "hq-edge-1", "br1": "br1-edge-1"}[site_id]
    link = next(link for link in data["links"] if link["kind"] == "ebgp" and any(
        endpoint["node"] == node_id for endpoint in link["endpoints"]
    ))
    remote = next(
        endpoint for endpoint in link["endpoints"] if endpoint["node"] != node_id
    )
    return str(ipaddress.ip_interface(remote["address"]).ip)


def test_rendered_policy_builds_full_mesh_and_keeps_endpoint_on_primary_edges() -> None:
    data = load_inventory()
    rendered = configs(data)
    sites = {site["id"]: site for site in data["sites"]}

    ebgp_links = [link for link in data["links"] if link["kind"] == "ebgp"]
    assert len(ebgp_links) == 8
    for site_id in ("hq", "br1"):
        endpoint = sites[site_id]["public_endpoint"]
        for edge_index in (1, 2):
            node_id = f"{site_id}-edge-{edge_index}"
            config = rendered[node_id]
            peers = [line for line in config.splitlines() if line.startswith(" neighbor ") and " remote-as " in line]
            assert len(peers) == 2
            assert "maximum-prefix 1" in config
            assert "ip prefix-list ISP-DEFAULT seq 10 permit 0.0.0.0/0" in config
            assert "route-map ISP-IN permit 10" in config
            if edge_index == 1:
                assert "interface lo" in config
                assert f"ip address {endpoint}" in config
                assert f"network {endpoint}" in config
            else:
                assert "interface lo" not in config
                assert f"network {endpoint}" not in config
                assert "prefix-list DENY-ALL out" in config


def test_isp_exports_only_declared_site_endpoints_and_imports_only_them() -> None:
    data = load_inventory()
    rendered = configs(data)
    sites = {site["id"]: site for site in data["sites"]}
    for isp_id in ("isp1-core-1", "isp2-core-1"):
        config = rendered[isp_id]
        for site_id in ("hq", "br1"):
            endpoint = sites[site_id]["public_endpoint"]
            assert f"ip prefix-list {site_id.upper()}-ENDPOINT seq 10 permit {endpoint}" in config
            for edge_index in (1, 2):
                edge_id = f"{site_id}-edge-{edge_index}"
                link = next(link for link in data["links"] if link["kind"] == "ebgp" and any(endpoint["node"] == edge_id for endpoint in link["endpoints"]) and any(endpoint["node"] == isp_id for endpoint in link["endpoints"]))
                peer = next(endpoint for endpoint in link["endpoints"] if endpoint["node"] == edge_id)
                peer_ip = str(ipaddress.ip_interface(peer["address"]).ip)
                expected_filter = f"{site_id.upper()}-ENDPOINT" if edge_index == 1 else "DENY-ALL"
                assert f"neighbor {peer_ip} prefix-list {expected_filter} in" in config
                assert f"neighbor {peer_ip} maximum-prefix 1" in config
        if isp_id == "isp1-core-1":
            assert "default-originate" in config
            assert "ip prefix-list ISP-DEFAULT-ONLY seq 10 permit 0.0.0.0/0" in config
            assert "203.0.113.0/25 Null0" in config
        else:
            assert "default-originate" not in config
            assert "neighbor 198.51.100.0 prefix-list DENY-ALL out" in config


def test_apply_enables_bgpd_and_reloads_only_when_needed() -> None:
    script = (ROOT / "automation" / "roles" / "bgp" / "apply.sh").read_text(
        encoding="utf-8"
    )
    assert 'grep -qx "bgpd=no" "$daemons"' in script
    assert 'sed -i "s/^bgpd=no$/bgpd=yes/" "$daemons"' in script
    assert "if ! pgrep -x bgpd" in script
    assert "/usr/lib/frr/frrinit.sh reload" in script
    assert "vtysh -f /tmp/netlab-bgp.conf" in script
    assert "ip route del default dev eth0" in script


def _vtysh(node_id: str, *commands: str, check: bool = True) -> str:
    container = f"clab-netlab-phase-1-{node_id}"
    result = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "vtysh",
            *[part for command in commands for part in ("-c", command)],
        ],
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout + result.stderr


def _prefixes(node_id: str, peer: str | None = None) -> set[str]:
    command = (
        "show bgp ipv4 unicast"
        if peer is None
        else f"show bgp ipv4 unicast neighbors {peer} routes"
    )
    output = _vtysh(node_id, command)
    prefix_pattern = r"^\s*[A-Za-z*>=]{1,3}\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\s"
    return set(re.findall(prefix_pattern, output, re.MULTILINE))


def _route_get(node_id: str, target: str) -> str:
    container = f"clab-netlab-phase-1-{node_id}"
    result = subprocess.run(
        ["docker", "exec", container, "ip", "route", "get", target],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.mark.skipif(
    os.environ.get("NETLAB_BGP_LIVE") != "1",
    reason="requires the serialized live lab",
)
def test_live_sessions_policy_and_rejection_of_injected_routes() -> None:
    data = load_inventory()
    sites = {site["id"]: site for site in data["sites"]}
    injected_site_prefix = "10.255.254.254/32"
    injected_isp_prefix = "203.0.113.200/32"
    injected_sites: list[tuple[str, str, str]] = []
    isp_injected = False
    try:
        expected_isp_prefixes = {site["public_endpoint"] for site in data["sites"]}
        deadline = time.monotonic() + 45
        for link in (item for item in data["links"] if item["kind"] == "ebgp"):
            edge = next(endpoint for endpoint in link["endpoints"] if "edge" in endpoint["node"])
            isp = next(endpoint for endpoint in link["endpoints"] if endpoint["node"].startswith("isp"))
            edge_peer = str(ipaddress.ip_interface(isp["address"]).ip)
            isp_peer = str(ipaddress.ip_interface(edge["address"]).ip)
            while time.monotonic() < deadline:
                edge_state = _vtysh(edge["node"], f"show bgp neighbors {edge_peer}")
                isp_state = _vtysh(isp["node"], f"show bgp neighbors {isp_peer}")
                if "BGP state = Established" in edge_state and "BGP state = Established" in isp_state:
                    break
                time.sleep(0.5)
            assert "BGP state = Established" in edge_state, f"{edge['node']} to {isp['node']} is not Established"
            assert "BGP state = Established" in isp_state, f"{isp['node']} to {edge['node']} is not Established"
        for isp_id in ("isp1-core-1", "isp2-core-1"):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and _prefixes(isp_id) != expected_isp_prefixes:
                time.sleep(0.5)
            assert _prefixes(isp_id) == expected_isp_prefixes, f"{isp_id} learned a non-endpoint route"
        assert _prefixes("hq-edge-2") == set() and _prefixes("br1-edge-2") == set(), (
            "WAN-210 secondary edges must not originate the stable endpoint or accept ISP defaults"
        )
        for site_id, node_id in (("hq", "hq-edge-1"), ("br1", "br1-edge-1")):
            peer = _peer(data, site_id)
            neighbor = _vtysh(node_id, f"show bgp neighbors {peer}")
            assert "BGP state = Established" in neighbor, (
                f"{node_id} eBGP session is not established"
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and _prefixes(node_id, peer) != {"0.0.0.0/0"}:
                time.sleep(0.5)
            assert _prefixes(node_id, peer) == {"0.0.0.0/0"}, (
                f"{node_id} imported a non-default route"
            )
            remote_site_id = "br1" if site_id == "hq" else "hq"
            remote_endpoint = sites[remote_site_id]["public_endpoint"].split("/")[0]
            route = _route_get(node_id, remote_endpoint)
            assert f"via {peer} dev eth3" in route, (
                f"{node_id} routes the IKE peer over OOB instead of ISP eth3: {route}"
            )
            injected_sites.append((site_id, node_id, peer))
            _vtysh(
                node_id,
                "configure terminal",
                f"ip route {injected_site_prefix} Null0",
                f"router bgp {sites[site_id]['asn']}",
                "address-family ipv4 unicast",
                f"network {injected_site_prefix}",
                "end",
                f"clear bgp {peer} soft out",
            )
            time.sleep(2)
            assert injected_site_prefix not in _prefixes("isp1-core-1"), (
                "ISP learned an unauthorized site route"
            )

            isp_injected = True
            _vtysh(
                "isp1-core-1",
                "configure terminal",
                f"ip route {injected_isp_prefix} Null0",
                "router bgp 65000",
                "address-family ipv4 unicast",
                f"network {injected_isp_prefix}",
                "end",
            )
            time.sleep(2)
            assert injected_isp_prefix not in _prefixes(node_id, peer), (
                f"{node_id} accepted an unauthorized ISP route"
            )
    finally:
        for site_id, node_id, peer in injected_sites:
            _vtysh(
                node_id,
                "configure terminal",
                f"router bgp {sites[site_id]['asn']}",
                "address-family ipv4 unicast",
                f"no network {injected_site_prefix}",
                "exit-address-family",
                "exit",
                f"no ip route {injected_site_prefix} Null0",
                "end",
                f"clear bgp {peer} soft out",
                check=False,
            )
        if isp_injected:
            _vtysh(
                "isp1-core-1",
                "configure terminal",
                "router bgp 65000",
                "address-family ipv4 unicast",
                f"no network {injected_isp_prefix}",
                "exit-address-family",
                "exit",
                f"no ip route {injected_isp_prefix} Null0",
                "end",
                check=False,
            )
