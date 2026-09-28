"""EDGE-410 static checks for the HQ SecureEdge firewall tier (ADR 0017)."""

from __future__ import annotations

import importlib.util
import ipaddress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "security_apply", ROOT / "config" / "security" / "apply.py"
)
assert SPEC and SPEC.loader
security = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(security)

FIREWALL = "hq-fw-1"


def _data() -> dict:
    return security.load_inventory()


def _nodes(data: dict) -> dict[str, dict]:
    return {node["id"]: node for node in data["nodes"]}


def _forward_rules(policy: str) -> list[str]:
    chain = policy.split("chain forward {", 1)[1].split("\n  }", 1)[0]
    return [line.strip() for line in chain.splitlines() if line.strip()]


def test_hq_edges_and_distribution_have_no_direct_routed_link() -> None:
    data = _data()
    nodes = _nodes(data)
    for link in data["links"]:
        if link.get("kind") != "routed":
            continue
        roles = {
            (nodes[e["node"]].get("site"), nodes[e["node"]].get("role"))
            for e in link["endpoints"]
        }
        assert roles != {("hq", "edge"), ("hq", "dist")}, (
            f"{link['id']} bypasses the SecureEdge tier"
        )


def test_firewall_is_the_only_hq_edge_to_distribution_transit() -> None:
    data = _data()
    nodes = _nodes(data)
    peers: dict[str, set[str]] = {}
    for link in data["links"]:
        ids = [e["node"] for e in link["endpoints"]]
        if FIREWALL in ids:
            other = next(node for node in ids if node != FIREWALL)
            peers.setdefault(nodes[other]["role"], set()).add(other)
    assert peers == {
        "edge": {"hq-edge-1", "hq-edge-2"},
        "dist": {"hq-dist-1", "hq-dist-2"},
    }


def test_firewall_addresses_follow_adr_0017() -> None:
    node = _nodes(_data())[FIREWALL]
    assert node["role"] == "firewall"
    assert node["oob"] == "172.31.255.35/24"
    assert node["loopback"] == "10.10.255.6/32"
    pool = ipaddress.ip_network("10.10.252.8/29")
    assert len(node["interfaces"]) == 4
    for interface in node["interfaces"]:
        assert interface["kind"] == "routed"
        address = ipaddress.ip_interface(interface["addresses"][0])
        assert address.network.prefixlen == 31 and address.ip in pool


def test_firewall_policy_fails_closed_and_logs_denies() -> None:
    policy = security.render_plan(_data())[FIREWALL]["inet"]
    for chain in ("input", "forward"):
        assert f"type filter hook {chain} priority -10; policy drop;" in policy
        assert f'log prefix "EDGE410|{FIREWALL}|{chain}|deny "' in policy
    rules = _forward_rules(policy)
    assert rules[-1] == "counter drop"
    # EDGE-420 (ADR 0018): permitted traffic, established included, is queued
    # to Suricata instead of accepted, and never with the bypass flag.
    assert "ct state established,related counter queue num 0" in rules
    assert not any(rule.endswith("counter accept") for rule in rules), rules
    assert not any("bypass" in rule for rule in rules), rules


def test_firewall_forward_allowlist_matches_hq_edge_intent() -> None:
    policy = security.render_plan(_data())[FIREWALL]["inet"]
    rules = [
        rule
        for rule in _forward_rules(policy)
        if rule.endswith("counter queue num 0") and "ct state" not in rule
    ]
    # Guests reach only simulated DNS and NTP.
    guest = [rule for rule in rules if "10.10.30.0/24" in rule]
    allowed = (
        "ip daddr 203.0.113.10 udp dport 53",
        "ip daddr 203.0.113.10 tcp dport 53",
        "ip daddr 203.0.113.11 udp dport 123",
    )
    assert len(guest) == 3
    assert all(
        "ip saddr 10.10.30.0/24" in rule and any(a in rule for a in allowed)
        for rule in guest
    ), guest
    # Management and server subnets are never a forward destination or source
    # toward the outside zone.
    for prefix in ("10.10.99.0/24", "10.10.20.0/24"):
        assert not any(prefix in rule for rule in rules), prefix
    # Inter-site corporate traffic is ICMP-only, as on the HQ edges.
    corporate = [rule for rule in rules if "10.10.10.0/24" in rule]
    assert corporate and all("ip protocol icmp" in rule for rule in corporate)


def test_firewall_input_admits_only_link_peers_and_oob() -> None:
    policy = security.render_plan(_data())[FIREWALL]["inet"]
    chain = policy.split("chain input {", 1)[1].split("\n  }", 1)[0]
    peers = {"10.10.252.8", "10.10.252.10", "10.10.252.13", "10.10.252.15"}
    for peer in peers:
        assert f"ip saddr {peer} ip protocol ospf counter accept" in chain
    for line in chain.splitlines():
        line = line.strip()
        if line.endswith("accept") and "ct state" not in line:
            assert "172.31.255." in line or any(peer in line for peer in peers), line


def test_firewall_boots_with_forwarding_disabled() -> None:
    topology = (ROOT / "lab" / "phase-1.clab.yml").read_text(encoding="utf-8")
    entry = topology.split(f"    {FIREWALL}:\n", 1)[1].split("\n    hq-", 1)[0]
    assert "sysctls:" in entry and "net.ipv4.ip_forward: 0" in entry, entry
    source = (ROOT / "config" / "security" / "apply.py").read_text(encoding="utf-8")
    apply_body = source.split("def apply(", 1)[1].split("\ndef ", 1)[0]
    # Suricata is attached before the queueing policy loads, and forwarding is
    # enabled only after the firewall table is loaded and listed.
    listed = apply_body.index('"nft", "list", "table", "inet", "netlab_sec170"')
    assert apply_body.index("_ensure_suricata(docker, container)") < apply_body.index(
        "for family, content in tables.items():"
    )
    assert apply_body.rindex('"net.ipv4.ip_forward=1"') > listed
