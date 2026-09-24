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


def test_rendered_policy_has_exact_endpoints_and_default_only_imports() -> None:
    data = load_inventory()
    rendered = configs(data)
    sites = {site["id"]: site for site in data["sites"]}

    for site_id, node_id in (("hq", "hq-edge-1"), ("br1", "br1-edge-1")):
        config = rendered[node_id]
        endpoint = sites[site_id]["public_endpoint"]
        underlay = "192.0.2.0/31" if site_id == "hq" else "192.0.2.2/31"
        assert "interface eth3" in config
        assert f"ip address {underlay}" in config
        assert "interface lo" in config
        assert f"ip address {endpoint}" in config
        assert f"ip route {endpoint} Null0" not in config
        assert "ip prefix-list ISP-DEFAULT seq 10 permit 0.0.0.0/0" in config
        assert (
            f"ip prefix-list {site_id.upper()}-ENDPOINT seq 10 permit {endpoint}"
            in config
        )
        assert "maximum-prefix 1" in config
        assert f"neighbor {_peer(data, site_id)} prefix-list ISP-DEFAULT in" in config
        assert f"network {endpoint}" in config
        assert "route-map ISP-IN permit 10" in config


def test_isp_exports_only_declared_site_endpoints_and_imports_only_them() -> None:
    data = load_inventory()
    config = configs(data)["isp1-core-1"]
    sites = {site["id"]: site for site in data["sites"]}
    for site_id, edge_id in (("hq", "hq-edge-1"), ("br1", "br1-edge-1")):
        link = next(link for link in data["links"] if link["kind"] == "ebgp" and any(
            endpoint["node"] == edge_id for endpoint in link["endpoints"]
        ))
        site_endpoint = next(
            endpoint for endpoint in link["endpoints"] if endpoint["node"] == edge_id
        )
        site_ip = str(ipaddress.ip_interface(site_endpoint["address"]).ip)
        endpoint = sites[site_id]["public_endpoint"]
        expected_filter = (
            f"ip prefix-list {site_id.upper()}-ENDPOINT seq 10 permit {endpoint}"
        )
        permit_lines = [
            line
            for line in config.splitlines()
            if line.startswith(f"ip prefix-list {site_id.upper()}-ENDPOINT")
            and " permit " in line
        ]
        assert permit_lines == [expected_filter]
        assert f"neighbor {site_ip} prefix-list {site_id.upper()}-ENDPOINT in" in config
        assert f"neighbor {site_ip} prefix-list ISP-DEFAULT-ONLY out" in config
    assert "maximum-prefix 1" in config
    assert "default-originate" in config
    assert "ip prefix-list ISP-DEFAULT-ONLY seq 10 permit 0.0.0.0/0" in config
    assert "interface eth1" in config and "ip address 192.0.2.1/31" in config
    assert "interface eth2" in config and "ip address 192.0.2.3/31" in config
    assert "203.0.113.0/25 Null0" in config


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
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and _prefixes("isp1-core-1") != expected_isp_prefixes:
            time.sleep(0.5)
        assert _prefixes("isp1-core-1") == expected_isp_prefixes, (
            "ISP learned a non-endpoint route"
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
