#!/usr/bin/env python3
"""Render and apply the SEC-170 inventory-scoped nftables policy."""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "inventory" / "inventory.yaml"
PHYSICAL_KINDS = {"routed", "ebgp", "l2", "access"}
SIM_DNS = "203.0.113.10"
SIM_NTP = "203.0.113.11"
OOB_PREFIX = "172.31.255.0/24"
IPS_QUEUE = 0
IPS_QUEUE_VERDICT = f"counter queue num {IPS_QUEUE}"
SURICATA_RULES = ROOT / "secure-edge" / "suricata" / "rules" / "netlab-local.rules"


def load_inventory(path: Path = INVENTORY) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sites = {item["id"]: item for item in data["sites"]}
    if not {"hq", "br1"}.issubset(sites):
        raise ValueError("SEC-170 requires the Phase 1 hq and br1 sites")
    return data


def _interface_map(node: dict[str, Any]) -> dict[str, str]:
    return {
        item["name"]: f"eth{index}"
        for index, item in enumerate(
            (
                item
                for item in node.get("interfaces", [])
                if item.get("kind") in PHYSICAL_KINDS
            ),
            start=1,
        )
    }


def _site_services(data: dict[str, Any], site_id: str) -> dict[str, str]:
    result = {}
    for service in ("dns", "ntp", "dhcp"):
        nodes = [
            item
            for item in data["nodes"]
            if item.get("site") == site_id and item.get("service") == service
        ]
        if len(nodes) != 1:
            raise ValueError(f"{site_id} must have exactly one local {service} service")
        result[service] = str(ipaddress.ip_interface(nodes[0]["service_address"]).ip)
    return result


def _linked_peer(
    data: dict[str, Any], node_id: str, link_kind: str
) -> list[tuple[str, str]]:
    nodes = {item["id"]: item for item in data["nodes"]}
    result = []
    for link in data["links"]:
        if link.get("kind") != link_kind:
            continue
        local = next(
            (item for item in link["endpoints"] if item["node"] == node_id), None
        )
        if local is None:
            continue
        remote = next(item for item in link["endpoints"] if item["node"] != node_id)
        if not remote.get("address"):
            continue
        result.append(
            (
                _interface_map(nodes[node_id])[local["interface"]],
                str(ipaddress.ip_interface(remote["address"]).ip),
            )
        )
    return result


def _oob_ipsec_guard(direction: str) -> list[str]:
    """Drop and count IKE and ESP on the OOB interface (ADR 0008).

    Overlays run only on the public underlay. The guard precedes the
    established/related accept because the output chain accepts, so IKE or ESP
    originated on eth0 by both ends would otherwise admit the replies.
    """
    match = f'{direction}name "eth0"'
    return [
        f"{match} udp dport {{ 500, 4500 }} counter drop",
        f"{match} udp sport {{ 500, 4500 }} counter drop",
        f"{match} ip protocol esp counter drop",
    ]


def _drop_chain(
    node_id: str,
    chain: str,
    rules: list[str],
    hook: str,
    tag: str = "SEC170",
    guard: list[str] | None = None,
) -> list[str]:
    return [
        f"  chain {chain} {{",
        f"    type filter hook {hook} priority -10; policy drop;",
        *(f"    {rule}" for rule in guard or []),
        "    ct state established,related counter accept",
        *rules,
        f'    limit rate 10/second burst 20 packets log prefix "{tag}|{node_id}|{chain}|deny " level warn',
        "    ct state invalid counter drop",
        "    counter drop",
        "  }",
    ]


def _guarded_output() -> list[str]:
    return [
        "  chain output {",
        "    type filter hook output priority -10; policy accept;",
        *(f"    {rule}" for rule in _oob_ipsec_guard("oif")),
        "  }",
    ]


def _render_dist(data: dict[str, Any], node: dict[str, Any]) -> str:
    site_id = node["site"]
    remote_sites = [item for item in data["sites"] if item["id"] != site_id]
    vlans = sorted(
        (item for item in data["vlans"] if item["site"] == site_id),
        key=lambda item: int(item["vlan_id"]),
    )
    services = _site_services(data, site_id)
    own_users = next(item["prefix"] for item in vlans if int(item["vlan_id"]) == 10)
    own_guests = next(item["prefix"] for item in vlans if int(item["vlan_id"]) == 30)
    remote_users = [
        item["prefix"]
        for item in data["vlans"]
        if item["site"] != site_id and int(item["vlan_id"]) == 10
    ]
    input_rules = [
        f'iifname "eth0" ip saddr {OOB_PREFIX} tcp dport 22 counter accept',
        f'iifname "eth0" ip saddr {OOB_PREFIX} ip protocol icmp counter accept',
        'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
    ]
    for vlan in vlans:
        iface = f"vlan{int(vlan['vlan_id'])}"
        vlan_id = int(vlan["vlan_id"])
        input_rules.append(
            f'iifname "{iface}" udp sport 68 udp dport 67 counter accept'
        )
        input_rules.append(
            f'iifname "{iface}" ip protocol icmp icmp type echo-request counter accept'
        )
        local = next(item for item in node["interfaces"] if item["name"] == iface)
        local_ip = str(ipaddress.ip_interface(local["addresses"][0]).ip)
        peer_ip = next(
            address for address in vlan["distribution_addresses"] if address != local_ip
        )
        input_rules.append(
            f'iifname "{iface}" ip saddr {peer_ip} ip protocol vrrp counter accept'
        )
    dhcp = services["dhcp"]
    input_rules.append(
        f'iifname "vlan20" ip saddr {dhcp} udp sport 67 udp dport 67 counter accept'
    )
    for iface, _peer in _linked_peer(data, node["id"], "routed"):
        input_rules.extend(
            [
                f'iifname "{iface}" ip protocol ospf counter accept',
                f'iifname "{iface}" udp dport {{ 3784, 3785 }} counter accept',
            ]
        )
    forward_rules = []
    for vlan in vlans:
        vlan_id = int(vlan["vlan_id"])
        iface = f"vlan{vlan_id}"
        if vlan_id == 10:
            forward_rules.extend(
                [
                    f'iifname "{iface}" ip daddr {services["dns"]} udp dport 53 counter accept',
                    f'iifname "{iface}" ip daddr {services["dns"]} tcp dport 53 counter accept',
                    f'iifname "{iface}" ip daddr {services["ntp"]} udp dport 123 counter accept',
                    *(
                        f'iifname "{iface}" ip saddr {vlan["prefix"]} ip daddr {remote["aggregate"]} ip protocol icmp counter accept'
                        for remote in remote_sites
                    ),
                ]
            )
        elif vlan_id == 20:
            forward_rules.extend(
                [
                    f'iifname "{iface}" ip saddr {services["dns"]} ip daddr {SIM_DNS} udp dport 53 counter accept',
                    f'iifname "{iface}" ip saddr {services["dns"]} ip daddr {SIM_DNS} tcp dport 53 counter accept',
                    f'iifname "{iface}" ip saddr {services["ntp"]} ip daddr {SIM_NTP} udp dport 123 counter accept',
                ]
            )
        elif vlan_id == 30:
            forward_rules.extend(
                [
                    f'iifname "{iface}" ip daddr {services["dns"]} udp dport 53 counter accept',
                    f'iifname "{iface}" ip daddr {services["dns"]} tcp dport 53 counter accept',
                    f'iifname "{iface}" ip daddr {services["ntp"]} udp dport 123 counter accept',
                    f'iifname "{iface}" ip daddr {SIM_DNS} udp dport 53 counter accept',
                    f'iifname "{iface}" ip daddr {SIM_DNS} tcp dport 53 counter accept',
                    f'iifname "{iface}" ip daddr {SIM_NTP} udp dport 123 counter accept',
                ]
            )
    for iface, _peer in _linked_peer(data, node["id"], "routed"):
        forward_rules.extend(
            f'iifname "{iface}" ip saddr {prefix} ip daddr {own_users} ip protocol icmp counter accept'
            for prefix in remote_users
        )
        forward_rules.extend(
            [
                f'iifname "{iface}" ip saddr {SIM_DNS} ip daddr {services["dns"]} udp sport 53 counter accept',
                f'iifname "{iface}" ip saddr {SIM_DNS} ip daddr {services["dns"]} tcp sport 53 counter accept',
                f'iifname "{iface}" ip saddr {SIM_NTP} ip daddr {services["ntp"]} udp sport 123 counter accept',
                # Return traffic for SNATed guest queries can use either distribution
                # node on the way back. Conntrack state is local to each node, so
                # permit only the exact simulated DNS/NTP replies to the guest VLAN.
                f'iifname "{iface}" ip saddr {SIM_DNS} ip daddr {own_guests} udp sport 53 counter accept',
                f'iifname "{iface}" ip saddr {SIM_DNS} ip daddr {own_guests} tcp sport 53 counter accept',
                f'iifname "{iface}" ip saddr {SIM_NTP} ip daddr {own_guests} udp sport 123 counter accept',
            ]
        )
    lines = [
        "table inet netlab_sec170 {",
        *_drop_chain(node["id"], "input", input_rules, "input"),
        *_drop_chain(node["id"], "forward", forward_rules, "forward"),
        "  chain output { type filter hook output priority -10; policy accept; }",
        "}",
        "",
    ]
    return "\n".join(lines)


def _render_edge(data: dict[str, Any], node: dict[str, Any]) -> tuple[str, str]:
    site_id = node["site"]
    mapping = _interface_map(node)
    public_ip = str(ipaddress.ip_interface(node["public_endpoint"]).ip)
    own_users = next(
        item["prefix"]
        for item in data["vlans"]
        if item["site"] == site_id and int(item["vlan_id"]) == 10
    )
    own_guests = next(
        item["prefix"]
        for item in data["vlans"]
        if item["site"] == site_id and int(item["vlan_id"]) == 30
    )
    isp_links = [
        link
        for link in data["links"]
        if link["kind"] == "ebgp"
        and any(item["node"] == node["id"] for item in link["endpoints"])
    ]
    wan_ifaces: dict[str, str] = {}
    for link in isp_links:
        local = next(item for item in link["endpoints"] if item["node"] == node["id"])
        peer = next(item for item in link["endpoints"] if item["node"] != node["id"])
        wan_ifaces[local["interface"]] = mapping[local["interface"]]
    ospf_peers = _linked_peer(data, node["id"], "routed")
    xfrm_links = [
        link
        for link in data["links"]
        if link.get("kind") == "xfrm"
        and any(item["node"] == node["id"] for item in link["endpoints"])
    ]
    input_rules = [
        f'iifname "eth0" ip saddr {OOB_PREFIX} tcp dport 22 counter accept',
        f'iifname "eth0" ip saddr {OOB_PREFIX} ip protocol icmp counter accept',
        'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
    ]
    for link in data["links"]:
        if link.get("kind") != "ebgp":
            continue
        local = next(
            (
                endpoint
                for endpoint in link["endpoints"]
                if endpoint["node"] == node["id"]
            ),
            None,
        )
        if local is None:
            continue
        peer = next(
            endpoint for endpoint in link["endpoints"] if endpoint["node"] != node["id"]
        )
        input_rules.append(
            f'iifname "{mapping[local["interface"]]}" ip saddr '
            f"{ipaddress.ip_interface(peer['address']).ip} tcp dport 179 counter accept"
        )
    xfrm_forward: list[str] = []
    for link in xfrm_links:
        local_xfrm = next(
            item for item in link["endpoints"] if item["node"] == node["id"]
        )
        remote_xfrm = next(
            item for item in link["endpoints"] if item["node"] != node["id"]
        )
        remote_xfrm_ip = str(ipaddress.ip_interface(remote_xfrm["address"]).ip)
        remote_edge = next(
            item for item in data["nodes"] if item["id"] == remote_xfrm["node"]
        )
        remote_public = str(ipaddress.ip_interface(remote_edge["public_endpoint"]).ip)
        input_rules.extend(
            [
                *(
                    f'iifname "{interface}" ip saddr {remote_public} udp dport {{ 500, 4500 }} counter accept'
                    for interface in wan_ifaces.values()
                ),
                *(
                    f'iifname "{interface}" ip saddr {remote_public} ip protocol esp counter accept'
                    for interface in wan_ifaces.values()
                ),
                f'iifname "{local_xfrm["interface"]}" ip saddr {remote_xfrm_ip} ip protocol ospf counter accept',
                f'iifname "{local_xfrm["interface"]}" ip saddr {remote_xfrm_ip} udp dport {{ 3784, 3785 }} counter accept',
                f'iifname "{local_xfrm["interface"]}" ip saddr {remote_xfrm_ip} ip protocol icmp icmp type echo-request counter accept',
            ]
        )
        remote_prefixes = [
            vlan["prefix"]
            for vlan in data["vlans"]
            if vlan["site"] == remote_edge["site"] and int(vlan["vlan_id"]) == 10
        ]
        xfrm_forward.extend(
            [
                f'iifname "{local_xfrm["interface"]}" oifname {{ {", ".join(sorted(set(mapping.values()) - set(wan_ifaces.values())))} }} ip daddr {own_users} ip protocol icmp counter accept',
                *(
                    f'oifname "{local_xfrm["interface"]}" ip saddr {own_users} ip daddr {prefix} ip protocol icmp counter accept'
                    for prefix in remote_prefixes
                ),
            ]
        )
    for iface, address in ospf_peers:
        input_rules.extend(
            [
                f'iifname "{iface}" ip saddr {address} ip protocol ospf counter accept',
                f'iifname "{iface}" ip saddr {address} udp dport {{ 3784, 3785 }} counter accept',
                f'iifname "{iface}" ip saddr {address} ip protocol icmp icmp type echo-request counter accept',
            ]
        )
    forward_rules = [
        *xfrm_forward,
        f"ip saddr {own_guests} ip daddr {SIM_DNS} udp dport 53 counter accept",
        f"ip saddr {own_guests} ip daddr {SIM_DNS} tcp dport 53 counter accept",
        f"ip saddr {own_guests} ip daddr {SIM_NTP} udp dport 123 counter accept",
    ]
    services = _site_services(data, site_id)
    forward_rules.extend(
        [
            f"ip saddr {services['dns']} ip daddr {SIM_DNS} udp dport 53 counter accept",
            f"ip saddr {services['dns']} ip daddr {SIM_DNS} tcp dport 53 counter accept",
            f"ip saddr {services['ntp']} ip daddr {SIM_NTP} udp dport 123 counter accept",
        ]
    )
    inet_table = [
        "table inet netlab_sec170 {",
        *_drop_chain(
            node["id"], "input", input_rules, "input", guard=_oob_ipsec_guard("iif")
        ),
        *_drop_chain(node["id"], "forward", forward_rules, "forward"),
        *_guarded_output(),
        "}",
        "",
    ]
    nat_table = [
        "table ip netlab_sec170_nat {",
        "  chain postrouting {",
        "    type nat hook postrouting priority srcnat; policy accept;",
        *(
            f'    oifname "{interface}" ip saddr {own_guests} ip daddr {SIM_DNS} udp dport 53 counter snat to {public_ip}'
            for interface in wan_ifaces.values()
        ),
        *(
            f'    oifname "{interface}" ip saddr {own_guests} ip daddr {SIM_DNS} tcp dport 53 counter snat to {public_ip}'
            for interface in wan_ifaces.values()
        ),
        *(
            f'    oifname "{interface}" ip saddr {own_guests} ip daddr {SIM_NTP} udp dport 123 counter snat to {public_ip}'
            for interface in wan_ifaces.values()
        ),
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
        'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
    ]
    for link in data["links"]:
        if link.get("kind") != "ebgp":
            continue
        local = next(
            (
                endpoint
                for endpoint in link["endpoints"]
                if endpoint["node"] == node["id"]
            ),
            None,
        )
        if local is None:
            continue
        peer = next(
            endpoint for endpoint in link["endpoints"] if endpoint["node"] != node["id"]
        )
        input_rules.append(
            f'iifname "{mapping[local["interface"]]}" ip saddr '
            f"{ipaddress.ip_interface(peer['address']).ip} tcp dport 179 counter accept"
        )
    for iface, peer_ip in _linked_peer(data, node["id"], "routed"):
        input_rules.extend(
            [
                f'iifname "{iface}" ip saddr {peer_ip} ip protocol ospf counter accept',
                f'iifname "{iface}" ip saddr {peer_ip} udp dport {{ 3784, 3785 }} counter accept',
            ]
        )
    return "\n".join(
        [
            "table inet netlab_sec170 {",
            *_drop_chain(
                node["id"], "input", input_rules, "input", guard=_oob_ipsec_guard("iif")
            ),
            "  chain forward { type filter hook forward priority -10; policy drop; }",
            *_guarded_output(),
            "}",
            "",
        ]
    )


def _render_isp(data: dict[str, Any], node: dict[str, Any]) -> str:
    input_rules = [
        f'iifname "eth0" ip saddr {OOB_PREFIX} tcp dport 22 counter accept',
        f'iifname "eth0" ip saddr {OOB_PREFIX} ip protocol icmp counter accept',
        'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
    ]
    for link in data["links"]:
        if link.get("kind") != "ebgp":
            continue
        local = next(
            (
                endpoint
                for endpoint in link["endpoints"]
                if endpoint["node"] == node["id"]
            ),
            None,
        )
        if local is None:
            continue
        peer = next(
            endpoint for endpoint in link["endpoints"] if endpoint["node"] != node["id"]
        )
        iface = _interface_map(node)[local["interface"]]
        peer_ip = str(ipaddress.ip_interface(peer["address"]).ip)
        input_rules.append(
            f'iifname "{iface}" ip saddr {peer_ip} tcp dport 179 counter accept'
        )
    forward_rules: list[str] = []
    nodes = {item["id"]: item for item in data["nodes"]}
    for link in data["links"]:
        if link.get("kind") != "ebgp":
            continue
        local = next(
            (item for item in link["endpoints"] if item["node"] == node["id"]), None
        )
        if local is None:
            continue
        edge_ep = next(item for item in link["endpoints"] if item["node"] != node["id"])
        edge = nodes[edge_ep["node"]]
        iface = _interface_map(node)[local["interface"]]
        source_public = str(ipaddress.ip_interface(edge["public_endpoint"]).ip)
        for overlay in data["links"]:
            if overlay.get("kind") != "xfrm":
                continue
            overlay_edge = next(
                (item for item in overlay["endpoints"] if item["node"] == edge["id"]),
                None,
            )
            if overlay_edge is None:
                continue
            remote_endpoint = next(
                item for item in overlay["endpoints"] if item["node"] != edge["id"]
            )
            remote = nodes[remote_endpoint["node"]]
            destination_public = str(
                ipaddress.ip_interface(remote["public_endpoint"]).ip
            )
            forward_rules.extend(
                [
                    f'iifname "{iface}" ip saddr {source_public} ip daddr {destination_public} udp dport {{ 500, 4500 }} counter accept',
                    f'iifname "{iface}" ip saddr {source_public} ip daddr {destination_public} ip protocol esp counter accept',
                ]
            )
        # Only advertised endpoint sources may reach simulated DNS and NTP.
        forward_rules.extend(
            [
                f'iifname "{iface}" ip saddr {source_public} ip daddr {SIM_DNS} udp dport 53 counter accept',
                f'iifname "{iface}" ip saddr {source_public} ip daddr {SIM_DNS} tcp dport 53 counter accept',
                f'iifname "{iface}" ip saddr {source_public} ip daddr {SIM_NTP} udp dport 123 counter accept',
            ]
        )
    lines = [
        "table inet netlab_sec170 {",
        *_drop_chain(node["id"], "input", input_rules, "input"),
        *_drop_chain(node["id"], "forward", forward_rules, "forward"),
        "  chain output { type filter hook output priority -10; policy accept; }",
        "}",
        "",
    ]
    return "\n".join(lines)


def _render_firewall(data: dict[str, Any], node: dict[str, Any]) -> str:
    """EDGE-410 SecureEdge tier (ADR 0017): zone policy between site and edges."""
    site_id = node["site"]
    nodes = {item["id"]: item for item in data["nodes"]}
    mapping = _interface_map(node)
    outside: list[str] = []
    inside: list[str] = []
    for link in data["links"]:
        if link.get("kind") != "routed":
            continue
        local = next(
            (item for item in link["endpoints"] if item["node"] == node["id"]), None
        )
        if local is None:
            continue
        peer = next(item for item in link["endpoints"] if item["node"] != node["id"])
        role = nodes[peer["node"]].get("role")
        if role == "edge":
            outside.append(mapping[local["interface"]])
        elif role == "dist":
            inside.append(mapping[local["interface"]])
        else:
            raise ValueError(f"{node['id']} has an unexpected routed peer {peer['node']}")
    if not outside or not inside:
        raise ValueError(f"{node['id']} must sit between site distribution and edges")

    def prefix(vlan_id: int, site: str = site_id) -> str:
        return next(
            item["prefix"]
            for item in data["vlans"]
            if item["site"] == site and int(item["vlan_id"]) == vlan_id
        )

    users, guests = prefix(10), prefix(30)
    remote_users = sorted(
        item["prefix"]
        for item in data["vlans"]
        if item["site"] != site_id and int(item["vlan_id"]) == 10
    )
    to_outside = f'iifname {{ {", ".join(sorted(inside))} }} oifname {{ {", ".join(sorted(outside))} }}'
    to_inside = f'iifname {{ {", ".join(sorted(outside))} }} oifname {{ {", ".join(sorted(inside))} }}'
    services = _site_services(data, site_id)

    input_rules = [
        f'iifname "eth0" ip saddr {OOB_PREFIX} tcp dport 22 counter accept',
        f'iifname "eth0" ip saddr {OOB_PREFIX} ip protocol icmp counter accept',
        'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
    ]
    for iface, address in _linked_peer(data, node["id"], "routed"):
        input_rules.extend(
            [
                f'iifname "{iface}" ip saddr {address} ip protocol ospf counter accept',
                f'iifname "{iface}" ip saddr {address} udp dport {{ 3784, 3785 }} counter accept',
                f'iifname "{iface}" ip saddr {address} ip protocol icmp icmp type echo-request counter accept',
            ]
        )
    forward_rules = [
        # inside <-> remote corporate users: ICMP only, as on the site edges.
        *(
            f"{to_outside} ip saddr {users} ip daddr {remote} ip protocol icmp counter accept"
            for remote in remote_users
        ),
        f"{to_inside} ip daddr {users} ip protocol icmp counter accept",
        # guest -> simulated Internet DNS/NTP only.
        f"{to_outside} ip saddr {guests} ip daddr {SIM_DNS} udp dport 53 counter accept",
        f"{to_outside} ip saddr {guests} ip daddr {SIM_DNS} tcp dport 53 counter accept",
        f"{to_outside} ip saddr {guests} ip daddr {SIM_NTP} udp dport 123 counter accept",
        # service zone resolvers and time source -> simulated Internet.
        f"{to_outside} ip saddr {services['dns']} ip daddr {SIM_DNS} udp dport 53 counter accept",
        f"{to_outside} ip saddr {services['dns']} ip daddr {SIM_DNS} tcp dport 53 counter accept",
        f"{to_outside} ip saddr {services['ntp']} ip daddr {SIM_NTP} udp dport 123 counter accept",
    ]
    # EDGE-420 (ADR 0018): every permitted forward verdict, including
    # established traffic, goes to the inline Suricata on queue 0. Without the
    # bypass flag the kernel drops queued packets when Suricata is absent.
    forward_chain = [
        line.replace("counter accept", IPS_QUEUE_VERDICT)
        for line in _drop_chain(node["id"], "forward", forward_rules, "forward", "EDGE410")
    ]
    return "\n".join(
        [
            "table inet netlab_sec170 {",
            *_drop_chain(node["id"], "input", input_rules, "input", "EDGE410"),
            *forward_chain,
            "  chain output { type filter hook output priority -10; policy accept; }",
            "}",
            "",
        ]
    )


def render_plan(data: dict[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for node in data["nodes"]:
        role = node.get("role")
        if role == "dist":
            result[node["id"]] = {"inet": _render_dist(data, node)}
        elif role == "firewall":
            result[node["id"]] = {"inet": _render_firewall(data, node)}
        elif role == "edge":
            has_xfrm = any(
                link.get("kind") == "xfrm"
                and any(
                    endpoint["node"] == node["id"] for endpoint in link["endpoints"]
                )
                for link in data["links"]
            )
            if has_xfrm:
                inet, nat = _render_edge(data, node)
                result[node["id"]] = {"inet": inet, "nat": nat}
            else:
                result[node["id"]] = {"inet": _render_secondary_edge(data, node)}
        elif role in {"access", "isp"}:
            # Access nodes have no routed data interfaces; they still expose management only on OOB.
            result[node["id"]] = {
                "inet": _render_isp(data, node)
                if role == "isp"
                else _render_access(node)
            }
    return result


def _render_access(node: dict[str, Any]) -> str:
    rules = [
        f'iifname "eth0" ip saddr {OOB_PREFIX} tcp dport 22 counter accept',
        f'iifname "eth0" ip saddr {OOB_PREFIX} ip protocol icmp counter accept',
        'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept',
    ]
    return "\n".join(
        [
            "table inet netlab_sec170 {",
            *_drop_chain(node["id"], "input", rules, "input"),
            "  chain forward { type filter hook forward priority -10; policy drop; }",
            "  chain output { type filter hook output priority -10; policy accept; }",
            "}",
            "",
        ]
    )


def _run(
    docker: str, container: str, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [docker, "exec", container, *args], text=True, capture_output=True, check=False
    )
    if check and result.returncode:
        raise RuntimeError(
            f"{container}: {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def check_policy(
    data: dict[str, Any], docker: str = "docker", lab_name: str = "netlab-phase-1"
) -> None:
    """Validate every generated nftables transaction against the live kernels without applying it."""
    for node_id, tables in render_plan(data).items():
        container = f"clab-{lab_name}-{node_id}"
        for family, content in tables.items():
            table_name = "netlab_sec170_nat" if family == "nat" else "netlab_sec170"
            table_family = "ip" if family == "nat" else "inet"
            exists = (
                _run(
                    docker,
                    container,
                    "nft",
                    "list",
                    "table",
                    table_family,
                    table_name,
                    check=False,
                ).returncode
                == 0
            )
            transaction = (
                f"delete table {table_family} {table_name}\n{content}"
                if exists
                else content
            )
            _write_in_container(
                docker, container, f"/tmp/{table_name}.nft", transaction
            )
            _run(docker, container, "nft", "-c", "-f", f"/tmp/{table_name}.nft")


def _write_in_container(
    docker: str,
    container: str,
    destination: str,
    content: str,
    source: Path | None = None,
) -> None:
    if source is None:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", delete=False
        ) as stream:
            stream.write(content)
            source = Path(stream.name)
        cleanup = True
    else:
        cleanup = False
    try:
        subprocess.run(
            [docker, "cp", str(source), f"{container}:{destination}"], check=True
        )
    finally:
        if cleanup:
            source.unlink(missing_ok=True)


SURICATA_RULES_DEST = "/etc/netlab/suricata/netlab-local.rules"
SURICATA_PIDFILE = "/run/suricata.pid"
SURICATA_ARGS = [
    "suricata",
    "-c", "/etc/suricata/suricata.yaml",
    "-S", SURICATA_RULES_DEST,
    "-q", str(IPS_QUEUE),
    "-k", "none",
    "-l", "/var/log/suricata",
]


def _suricata_bound(docker: str, container: str) -> bool:
    queues = _run(
        docker, container, "cat", "/proc/net/netfilter/nfnetlink_queue", check=False
    ).stdout
    return any(line.split()[:1] == [str(IPS_QUEUE)] for line in queues.splitlines())


def _ensure_suricata(docker: str, container: str) -> None:
    """Run inline Suricata on the IPS queue with the curated local rules (ADR 0018).

    Idempotent: an unchanged rule file on a running, bound engine is a no-op.
    """
    _run(docker, container, "mkdir", "-p", "/etc/netlab/suricata", "/var/log/suricata")
    staged = "/tmp/netlab-local.rules"
    _write_in_container(docker, container, staged, "", source=SURICATA_RULES)
    changed = (
        _run(docker, container, "cmp", "-s", staged, SURICATA_RULES_DEST, check=False).returncode
        != 0
    )
    if changed:
        _run(
            docker, container,
            "suricata", "-T", "-c", "/etc/suricata/suricata.yaml", "-S", staged, "-k", "none",
        )
        _run(docker, container, "cp", staged, SURICATA_RULES_DEST)
    pid = _run(
        docker,
        container,
        "sh",
        "-c",
        f'test -s {SURICATA_PIDFILE} && kill -0 "$(cat {SURICATA_PIDFILE})" && cat {SURICATA_PIDFILE}',
        check=False,
    ).stdout.strip()
    if not pid:
        _run(docker, container, "rm", "-f", SURICATA_PIDFILE)
        _run(docker, container, *SURICATA_ARGS, "-D", "--pidfile", SURICATA_PIDFILE)
    elif changed:
        # SIGUSR2 reloads rules without detaching from the queue.
        _run(docker, container, "kill", "-USR2", pid)
    deadline = time.monotonic() + 60
    while not _suricata_bound(docker, container):
        if time.monotonic() > deadline:
            raise RuntimeError(f"{container}: Suricata did not bind NFQUEUE {IPS_QUEUE}")
        time.sleep(0.5)


def apply(
    data: dict[str, Any],
    docker: str = "docker",
    lab_name: str = "netlab-phase-1",
    roles: set[str] | None = None,
) -> None:
    nodes = {item["id"]: item for item in data["nodes"]}
    for node_id, tables in render_plan(data).items():
        container = f"clab-{lab_name}-{node_id}"
        role = nodes[node_id].get("role")
        if roles is not None and role not in roles:
            continue
        if role in {"dist", "edge", "isp"}:
            forwarding = _run(
                docker, container, "cat", "/proc/sys/net/ipv4/ip_forward"
            ).stdout.strip()
            if forwarding != "1":
                _run(docker, container, "sysctl", "-w", "net.ipv4.ip_forward=1")
        _run(docker, container, "mkdir", "-p", "/run/netlab")
        if role == "firewall":
            # The firewall policy queues permitted traffic to Suricata; start
            # the engine first so loading the policy never blackholes HQ.
            _ensure_suricata(docker, container)
        for family, content in tables.items():
            table_name = "netlab_sec170_nat" if family == "nat" else "netlab_sec170"
            table_family = "ip" if family == "nat" else "inet"
            destination = f"/tmp/{table_name}.nft"
            marker = f"/run/netlab/{table_name}.nft"
            _write_in_container(docker, container, destination, content)
            table_exists = (
                _run(
                    docker,
                    container,
                    "nft",
                    "list",
                    "table",
                    table_family,
                    table_name,
                    check=False,
                ).returncode
                == 0
            )
            same_source = (
                _run(
                    docker, container, "cmp", "-s", destination, marker, check=False
                ).returncode
                == 0
            )
            if table_exists and same_source:
                continue
            transaction = (
                f"delete table {table_family} {table_name}\n{content}"
                if table_exists
                else content
            )
            _write_in_container(docker, container, destination, transaction)
            _run(docker, container, "nft", "-c", "-f", destination)
            _run(docker, container, "nft", "-f", destination)
            _write_in_container(docker, container, marker, content)
        _run(docker, container, "nft", "list", "table", "inet", "netlab_sec170")
        if role == "firewall":
            # Fail closed (ADR 0017): forward only once the drop-by-default
            # policy above is loaded and listed.
            forwarding = _run(
                docker, container, "cat", "/proc/sys/net/ipv4/ip_forward"
            ).stdout.strip()
            if forwarding != "1":
                _run(docker, container, "sysctl", "-w", "net.ipv4.ip_forward=1")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--lab-name", default="netlab-phase-1")
    parser.add_argument("--render-dir", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate against live kernels without changing policy",
    )
    parser.add_argument(
        "--role",
        action="append",
        help="apply only to nodes with this inventory role (repeatable); "
        "e.g. --role firewall opens the SecureEdge tier before later suites",
    )
    args = parser.parse_args()
    data = load_inventory(args.inventory)
    plan = render_plan(data)
    if args.check:
        check_policy(data, args.docker, args.lab_name)
    elif args.render_dir:
        args.render_dir.mkdir(parents=True, exist_ok=True)
        for node_id, tables in plan.items():
            for family, content in tables.items():
                (args.render_dir / f"{node_id}-{family}.nft").write_text(
                    content, encoding="utf-8"
                )
    else:
        apply(data, args.docker, args.lab_name, set(args.role) if args.role else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
