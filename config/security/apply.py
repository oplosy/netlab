#!/usr/bin/env python3
"""Render and apply the SEC-170 inventory-scoped nftables policy."""
from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "inventory" / "inventory.yaml"
PHYSICAL_KINDS = {"routed", "ebgp", "l2", "access"}
SIM_DNS = "203.0.113.10"
SIM_NTP = "203.0.113.11"
OOB_PREFIX = "172.31.255.0/24"


def load_inventory(path: Path = INVENTORY) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sites = {item["id"]: item for item in data["sites"]}
    if set(sites) != {"hq", "br1"}:
        raise ValueError("SEC-170 currently requires the Phase 1 hq and br1 sites")
    return data


def _interface_map(node: dict[str, Any]) -> dict[str, str]:
    return {
        item["name"]: f"eth{index}"
        for index, item in enumerate(
            (item for item in node.get("interfaces", []) if item.get("kind") in PHYSICAL_KINDS),
            start=1,
        )
    }


def _site_services(data: dict[str, Any], site_id: str) -> dict[str, str]:
    result = {}
    for service in ("dns", "ntp", "dhcp"):
        nodes = [item for item in data["nodes"] if item.get("site") == site_id and item.get("service") == service]
        if len(nodes) != 1:
            raise ValueError(f"{site_id} must have exactly one local {service} service")
        result[service] = str(ipaddress.ip_interface(nodes[0]["service_address"]).ip)
    return result


def _linked_peer(data: dict[str, Any], node_id: str, link_kind: str) -> list[tuple[str, str]]:
    nodes = {item["id"]: item for item in data["nodes"]}
    result = []
    for link in data["links"]:
        if link.get("kind") != link_kind:
            continue
        local = next((item for item in link["endpoints"] if item["node"] == node_id), None)
        if local is None:
            continue
        remote = next(item for item in link["endpoints"] if item["node"] != node_id)
        if not remote.get("address"):
            continue
        result.append((_interface_map(nodes[node_id])[local["interface"]], str(ipaddress.ip_interface(remote["address"]).ip)))
    return result


def _drop_chain(node_id: str, chain: str, rules: list[str], hook: str) -> list[str]:
    return [
        f"  chain {chain} {{",
        f"    type filter hook {hook} priority -10; policy drop;",
        "    ct state established,related counter accept",
        *rules,
        f'    limit rate 10/second burst 20 packets log prefix "SEC170|{node_id}|{chain}|deny " level warn',
        "    ct state invalid counter drop",
        "    counter drop",
        "  }",
    ]


def _render_dist(data: dict[str, Any], node: dict[str, Any]) -> str:
    site_id = node["site"]
    site = next(item for item in data["sites"] if item["id"] == site_id)
    remote = next(item for item in data["sites"] if item["id"] != site_id)
    vlans = sorted((item for item in data["vlans"] if item["site"] == site_id), key=lambda item: int(item["vlan_id"]))
    services = _site_services(data, site_id)
    own_users = next(item["prefix"] for item in vlans if int(item["vlan_id"]) == 10)
    own_guests = next(item["prefix"] for item in vlans if int(item["vlan_id"]) == 30)
    remote_users = next(item["prefix"] for item in data["vlans"] if item["site"] == remote["id"] and int(item["vlan_id"]) == 10)
    input_rules = [
        f'iifname "eth0" ip saddr {OOB_PREFIX} tcp dport 22 counter accept',
        f'iifname "eth0" ip saddr {OOB_PREFIX} ip protocol icmp counter accept',
        f'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
    ]
    for vlan in vlans:
        iface = f"vlan{int(vlan['vlan_id'])}"
        vlan_id = int(vlan["vlan_id"])
        input_rules.append(f'iifname "{iface}" udp sport 68 udp dport 67 counter accept')
        input_rules.append(f'iifname "{iface}" ip protocol icmp icmp type echo-request counter accept')
        local = next(item for item in node["interfaces"] if item["name"] == iface)
        local_ip = str(ipaddress.ip_interface(local["addresses"][0]).ip)
        peer_ip = next(address for address in vlan["distribution_addresses"] if address != local_ip)
        input_rules.append(f'iifname "{iface}" ip saddr {peer_ip} ip protocol vrrp counter accept')
    dhcp = services["dhcp"]
    input_rules.append(f'iifname "vlan20" ip saddr {dhcp} udp sport 67 udp dport 67 counter accept')
    for iface, _peer in _linked_peer(data, node["id"], "routed"):
        input_rules.extend([
            f'iifname "{iface}" ip protocol ospf counter accept',
            f'iifname "{iface}" udp dport {{ 3784, 3785 }} counter accept',
        ])
    forward_rules = []
    for vlan in vlans:
        vlan_id = int(vlan["vlan_id"])
        iface = f"vlan{vlan_id}"
        if vlan_id == 10:
            forward_rules.extend([
                f'iifname "{iface}" ip daddr {services["dns"]} udp dport 53 counter accept',
                f'iifname "{iface}" ip daddr {services["dns"]} tcp dport 53 counter accept',
                f'iifname "{iface}" ip daddr {services["ntp"]} udp dport 123 counter accept',
                f'iifname "{iface}" ip saddr {vlan["prefix"]} ip daddr {remote["aggregate"]} ip protocol icmp counter accept',
            ])
        elif vlan_id == 20:
            forward_rules.extend([
                f'iifname "{iface}" ip saddr {services["dns"]} ip daddr {SIM_DNS} udp dport 53 counter accept',
                f'iifname "{iface}" ip saddr {services["dns"]} ip daddr {SIM_DNS} tcp dport 53 counter accept',
                f'iifname "{iface}" ip saddr {services["ntp"]} ip daddr {SIM_NTP} udp dport 123 counter accept',
            ])
        elif vlan_id == 30:
            forward_rules.extend([
                f'iifname "{iface}" ip daddr {services["dns"]} udp dport 53 counter accept',
                f'iifname "{iface}" ip daddr {services["dns"]} tcp dport 53 counter accept',
                f'iifname "{iface}" ip daddr {services["ntp"]} udp dport 123 counter accept',
                f'iifname "{iface}" ip daddr {SIM_DNS} udp dport 53 counter accept',
                f'iifname "{iface}" ip daddr {SIM_DNS} tcp dport 53 counter accept',
                f'iifname "{iface}" ip daddr {SIM_NTP} udp dport 123 counter accept',
            ])
    for iface, _peer in _linked_peer(data, node["id"], "routed"):
        forward_rules.append(
            f'iifname "{iface}" ip saddr {remote_users} ip daddr {own_users} ip protocol icmp counter accept'
        )
        forward_rules.extend([
            f'iifname "{iface}" ip saddr {SIM_DNS} ip daddr {services["dns"]} udp sport 53 counter accept',
            f'iifname "{iface}" ip saddr {SIM_DNS} ip daddr {services["dns"]} tcp sport 53 counter accept',
            f'iifname "{iface}" ip saddr {SIM_NTP} ip daddr {services["ntp"]} udp sport 123 counter accept',
            # Return traffic for SNATed guest queries can use either distribution
            # node on the way back. Conntrack state is local to each node, so
            # permit only the exact simulated DNS/NTP replies to the guest VLAN.
            f'iifname "{iface}" ip saddr {SIM_DNS} ip daddr {own_guests} udp sport 53 counter accept',
            f'iifname "{iface}" ip saddr {SIM_DNS} ip daddr {own_guests} tcp sport 53 counter accept',
            f'iifname "{iface}" ip saddr {SIM_NTP} ip daddr {own_guests} udp sport 123 counter accept',
        ])
    lines = [
        f"table inet netlab_sec170 {{",
        *_drop_chain(node["id"], "input", input_rules, "input"),
        *_drop_chain(node["id"], "forward", forward_rules, "forward"),
        "  chain output { type filter hook output priority -10; policy accept; }",
        "}",
        "",
    ]
    return "\n".join(lines)


def _render_edge(data: dict[str, Any], node: dict[str, Any]) -> tuple[str, str]:
    site_id = node["site"]
    site = next(item for item in data["sites"] if item["id"] == site_id)
    remote = next(item for item in data["sites"] if item["id"] != site_id)
    mapping = _interface_map(node)
    public_ip = str(ipaddress.ip_interface(site["public_endpoint"]).ip)
    own_users = next(item["prefix"] for item in data["vlans"] if item["site"] == site_id and int(item["vlan_id"]) == 10)
    remote_users = next(item["prefix"] for item in data["vlans"] if item["site"] == remote["id"] and int(item["vlan_id"]) == 10)
    own_guests = next(item["prefix"] for item in data["vlans"] if item["site"] == site_id and int(item["vlan_id"]) == 30)
    isp_link = next(link for link in data["links"] if link["kind"] == "ebgp" and any(item["node"] == node["id"] for item in link["endpoints"]))
    local = next(item for item in isp_link["endpoints"] if item["node"] == node["id"])
    peer = next(item for item in isp_link["endpoints"] if item["node"] != node["id"])
    wan_iface = mapping[local["interface"]]
    peer_ip = str(ipaddress.ip_interface(peer["address"]).ip)
    ospf_peers = _linked_peer(data, node["id"], "routed")
    xfrm = next(link for link in data["links"] if link.get("kind") == "xfrm" and any(item["node"] == node["id"] for item in link["endpoints"]))
    remote_xfrm = next(item for item in xfrm["endpoints"] if item["node"] != node["id"])
    remote_xfrm_ip = str(ipaddress.ip_interface(remote_xfrm["address"]).ip)
    input_rules = [
        f'iifname "eth0" ip saddr {OOB_PREFIX} tcp dport 22 counter accept',
        f'iifname "eth0" ip saddr {OOB_PREFIX} ip protocol icmp counter accept',
        f'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
        f'iifname "{wan_iface}" ip saddr {peer_ip} tcp dport 179 counter accept',
    ]
    for link in data["links"]:
        if link.get("kind") != "ebgp":
            continue
        local = next((endpoint for endpoint in link["endpoints"] if endpoint["node"] == node["id"]), None)
        if local is None or local["interface"] == next(item for item in isp_link["endpoints"] if item["node"] == node["id"])["interface"]:
            continue
        peer = next(endpoint for endpoint in link["endpoints"] if endpoint["node"] != node["id"])
        input_rules.append(
            f'iifname "{mapping[local["interface"]]}" ip saddr '
            f'{ipaddress.ip_interface(peer["address"]).ip} tcp dport 179 counter accept'
        )
    remote_public = str(ipaddress.ip_interface(remote["public_endpoint"]).ip)
    input_rules.extend([
        f'iifname "{wan_iface}" ip saddr {remote_public} udp dport {{ 500, 4500 }} counter accept',
        f'iifname "{wan_iface}" ip saddr {remote_public} ip protocol esp counter accept',
        f'iifname "xfrm0" ip saddr {remote_xfrm_ip} ip protocol ospf counter accept',
        f'iifname "xfrm0" ip saddr {remote_xfrm_ip} udp dport {{ 3784, 3785 }} counter accept',
        f'iifname "xfrm0" ip saddr {remote_xfrm_ip} ip protocol icmp icmp type echo-request counter accept',
    ])
    for iface, address in ospf_peers:
        input_rules.extend([
            f'iifname "{iface}" ip saddr {address} ip protocol ospf counter accept',
            f'iifname "{iface}" ip saddr {address} udp dport {{ 3784, 3785 }} counter accept',
            f'iifname "{iface}" ip saddr {address} ip protocol icmp icmp type echo-request counter accept',
        ])
    forward_rules = [
        f'iifname "xfrm0" oifname {{ {", ".join(sorted(set(mapping.values()) - {wan_iface}))} }} ip daddr {own_users} ip protocol icmp counter accept',
        f'oifname "xfrm0" ip saddr {own_users} ip daddr {remote_users} ip protocol icmp counter accept',
        f'ip saddr {own_guests} ip daddr {SIM_DNS} udp dport 53 counter accept',
        f'ip saddr {own_guests} ip daddr {SIM_DNS} tcp dport 53 counter accept',
        f'ip saddr {own_guests} ip daddr {SIM_NTP} udp dport 123 counter accept',
    ]
    services = _site_services(data, site_id)
    forward_rules.extend([
        f'ip saddr {services["dns"]} ip daddr {SIM_DNS} udp dport 53 counter accept',
        f'ip saddr {services["dns"]} ip daddr {SIM_DNS} tcp dport 53 counter accept',
        f'ip saddr {services["ntp"]} ip daddr {SIM_NTP} udp dport 123 counter accept',
    ])
    inet_table = [
        "table inet netlab_sec170 {",
        *_drop_chain(node["id"], "input", input_rules, "input"),
        *_drop_chain(node["id"], "forward", forward_rules, "forward"),
        "  chain output { type filter hook output priority -10; policy accept; }",
        "}",
        "",
    ]
    nat_table = [
        "table ip netlab_sec170_nat {",
        "  chain postrouting {",
        "    type nat hook postrouting priority srcnat; policy accept;",
        f'    oifname "{wan_iface}" ip saddr {own_guests} ip daddr {SIM_DNS} udp dport 53 counter snat to {public_ip}',
        f'    oifname "{wan_iface}" ip saddr {own_guests} ip daddr {SIM_DNS} tcp dport 53 counter snat to {public_ip}',
        f'    oifname "{wan_iface}" ip saddr {own_guests} ip daddr {SIM_NTP} udp dport 123 counter snat to {public_ip}',
        "  }",
        "}",
        "",
    ]
    return "\n".join(inet_table), "\n".join(nat_table)


def _render_secondary_edge(data: dict[str, Any], node: dict[str, Any]) -> str:
    """Keep a WAN-210 secondary edge control-plane-only until WAN-230 adds XFRM."""
    mapping = _interface_map(node)
    input_rules = [
        f'iifname "eth0" ip saddr {OOB_PREFIX} tcp dport 22 counter accept',
        f'iifname "eth0" ip saddr {OOB_PREFIX} ip protocol icmp counter accept',
        f'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
    ]
    for link in data["links"]:
        if link.get("kind") != "ebgp":
            continue
        local = next((endpoint for endpoint in link["endpoints"] if endpoint["node"] == node["id"]), None)
        if local is None:
            continue
        peer = next(endpoint for endpoint in link["endpoints"] if endpoint["node"] != node["id"])
        input_rules.append(
            f'iifname "{mapping[local["interface"]]}" ip saddr '
            f'{ipaddress.ip_interface(peer["address"]).ip} tcp dport 179 counter accept'
        )
    for iface, peer_ip in _linked_peer(data, node["id"], "routed"):
        input_rules.extend([
            f'iifname "{iface}" ip saddr {peer_ip} ip protocol ospf counter accept',
            f'iifname "{iface}" ip saddr {peer_ip} udp dport {{ 3784, 3785 }} counter accept',
        ])
    return "\n".join([
        "table inet netlab_sec170 {",
        *_drop_chain(node["id"], "input", input_rules, "input"),
        "  chain forward { type filter hook forward priority -10; policy drop; }",
        "  chain output { type filter hook output priority -10; policy accept; }",
        "}",
        "",
    ])


def _render_isp(data: dict[str, Any], node: dict[str, Any]) -> str:
    input_rules = [
        f'iifname "eth0" ip saddr {OOB_PREFIX} tcp dport 22 counter accept',
        f'iifname "eth0" ip saddr {OOB_PREFIX} ip protocol icmp counter accept',
        f'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
    ]
    for link in data["links"]:
        if link.get("kind") != "ebgp":
            continue
        local = next((endpoint for endpoint in link["endpoints"] if endpoint["node"] == node["id"]), None)
        if local is None:
            continue
        peer = next(endpoint for endpoint in link["endpoints"] if endpoint["node"] != node["id"])
        iface = _interface_map(node)[local["interface"]]
        peer_ip = str(ipaddress.ip_interface(peer["address"]).ip)
        input_rules.append(f'iifname "{iface}" ip saddr {peer_ip} tcp dport 179 counter accept')
    forward_rules: list[str] = []
    for source_site in data["sites"]:
        destination_site = next(site for site in data["sites"] if site["id"] != source_site["id"])
        source_public = str(ipaddress.ip_interface(source_site["public_endpoint"]).ip)
        destination_public = str(ipaddress.ip_interface(destination_site["public_endpoint"]).ip)
        source_edge_id = "hq-edge-1" if source_site["id"] == "hq" else "br1-edge-1"
        underlay = next(
            link for link in data["links"]
            if link["kind"] == "ebgp"
            and {item["node"] for item in link["endpoints"]} == {source_edge_id, node["id"]}
        )
        source_endpoint = next(item for item in underlay["endpoints"] if item["node"] == node["id"])
        source_iface = _interface_map(node)[source_endpoint["interface"]]
        forward_rules.extend([
            f'iifname "{source_iface}" ip saddr {source_public} ip daddr {destination_public} udp dport {{ 500, 4500 }} counter accept',
            f'iifname "{source_iface}" ip saddr {source_public} ip daddr {destination_public} ip protocol esp counter accept',
        ])
    for site in data["sites"]:
        public_ip = str(ipaddress.ip_interface(site["public_endpoint"]).ip)
        edge_id = "hq-edge-1" if site["id"] == "hq" else "br1-edge-1"
        link = next(
            link for link in data["links"]
            if link["kind"] == "ebgp"
            and {item["node"] for item in link["endpoints"]} == {edge_id, node["id"]}
        )
        edge_ep = next(item for item in link["endpoints"] if item["node"] == edge_id)
        local = next(item for item in link["endpoints"] if item["node"] == node["id"])
        iface = _interface_map(node)[local["interface"]]
        # Only advertised site endpoints may reach the simulator DNS and NTP hosts.
        forward_rules.extend([
            f'iifname "{iface}" ip saddr {public_ip} ip daddr {SIM_DNS} udp dport 53 counter accept',
            f'iifname "{iface}" ip saddr {public_ip} ip daddr {SIM_DNS} tcp dport 53 counter accept',
            f'iifname "{iface}" ip saddr {public_ip} ip daddr {SIM_NTP} udp dport 123 counter accept',
        ])
    lines = [
        "table inet netlab_sec170 {",
        *_drop_chain(node["id"], "input", input_rules, "input"),
        *_drop_chain(node["id"], "forward", forward_rules, "forward"),
        "  chain output { type filter hook output priority -10; policy accept; }",
        "}",
        "",
    ]
    return "\n".join(lines)


def render_plan(data: dict[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for node in data["nodes"]:
        role = node.get("role")
        if role == "dist":
            result[node["id"]] = {"inet": _render_dist(data, node)}
        elif role == "edge":
            has_xfrm = any(
                link.get("kind") == "xfrm"
                and any(endpoint["node"] == node["id"] for endpoint in link["endpoints"])
                for link in data["links"]
            )
            if has_xfrm:
                inet, nat = _render_edge(data, node)
                result[node["id"]] = {"inet": inet, "nat": nat}
            else:
                result[node["id"]] = {"inet": _render_secondary_edge(data, node)}
        elif role in {"access", "isp"}:
            # Access nodes have no routed data interfaces; they still expose management only on OOB.
            result[node["id"]] = {"inet": _render_isp(data, node) if role == "isp" else _render_access(node)}
    return result


def _render_access(node: dict[str, Any]) -> str:
    rules = [
        f'iifname "eth0" ip saddr {OOB_PREFIX} tcp dport 22 counter accept',
        f'iifname "eth0" ip saddr {OOB_PREFIX} ip protocol icmp counter accept',
        f'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
    ]
    return "\n".join([
        "table inet netlab_sec170 {",
        *_drop_chain(node["id"], "input", rules, "input"),
        "  chain forward { type filter hook forward priority -10; policy drop; }",
        "  chain output { type filter hook output priority -10; policy accept; }",
        "}",
        "",
    ])


def _run(docker: str, container: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([docker, "exec", container, *args], text=True, capture_output=True, check=False)
    if check and result.returncode:
        raise RuntimeError(f"{container}: {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
    return result


def check_policy(data: dict[str, Any], docker: str = "docker", lab_name: str = "netlab-phase-1") -> None:
    """Validate every generated nftables transaction against the live kernels without applying it."""
    for node_id, tables in render_plan(data).items():
        container = f"clab-{lab_name}-{node_id}"
        for family, content in tables.items():
            table_name = "netlab_sec170_nat" if family == "nat" else "netlab_sec170"
            table_family = "ip" if family == "nat" else "inet"
            exists = _run(docker, container, "nft", "list", "table", table_family, table_name, check=False).returncode == 0
            transaction = f"delete table {table_family} {table_name}\n{content}" if exists else content
            _write_in_container(docker, container, f"/tmp/{table_name}.nft", transaction)
            _run(docker, container, "nft", "-c", "-f", f"/tmp/{table_name}.nft")


def _write_in_container(docker: str, container: str, destination: str, content: str, source: Path | None = None) -> None:
    if source is None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False) as stream:
            stream.write(content)
            source = Path(stream.name)
        cleanup = True
    else:
        cleanup = False
    try:
        subprocess.run([docker, "cp", str(source), f"{container}:{destination}"], check=True)
    finally:
        if cleanup:
            source.unlink(missing_ok=True)


def apply(data: dict[str, Any], docker: str = "docker", lab_name: str = "netlab-phase-1") -> None:
    nodes = {item["id"]: item for item in data["nodes"]}
    for node_id, tables in render_plan(data).items():
        container = f"clab-{lab_name}-{node_id}"
        if nodes[node_id].get("role") in {"dist", "edge", "isp"}:
            forwarding = _run(docker, container, "cat", "/proc/sys/net/ipv4/ip_forward").stdout.strip()
            if forwarding != "1":
                _run(docker, container, "sysctl", "-w", "net.ipv4.ip_forward=1")
        _run(docker, container, "mkdir", "-p", "/run/netlab")
        for family, content in tables.items():
            table_name = "netlab_sec170_nat" if family == "nat" else "netlab_sec170"
            table_family = "ip" if family == "nat" else "inet"
            destination = f"/tmp/{table_name}.nft"
            marker = f"/run/netlab/{table_name}.nft"
            _write_in_container(docker, container, destination, content)
            table_exists = _run(docker, container, "nft", "list", "table", table_family, table_name, check=False).returncode == 0
            same_source = _run(docker, container, "cmp", "-s", destination, marker, check=False).returncode == 0
            if table_exists and same_source:
                continue
            transaction = f"delete table {table_family} {table_name}\n{content}" if table_exists else content
            _write_in_container(docker, container, destination, transaction)
            _run(docker, container, "nft", "-c", "-f", destination)
            _run(docker, container, "nft", "-f", destination)
            _write_in_container(docker, container, marker, content)
        _run(docker, container, "nft", "list", "table", "inet", "netlab_sec170")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--lab-name", default="netlab-phase-1")
    parser.add_argument("--render-dir", type=Path)
    parser.add_argument("--check", action="store_true", help="validate against live kernels without changing policy")
    args = parser.parse_args()
    data = load_inventory(args.inventory)
    plan = render_plan(data)
    if args.check:
        check_policy(data, args.docker, args.lab_name)
    elif args.render_dir:
        args.render_dir.mkdir(parents=True, exist_ok=True)
        for node_id, tables in plan.items():
            for family, content in tables.items():
                (args.render_dir / f"{node_id}-{family}.nft").write_text(content, encoding="utf-8")
    else:
        apply(data, args.docker, args.lab_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
