from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "sec170_apply", ROOT / "config" / "security" / "apply.py"
)
sec170 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(sec170)


def test_all_infrastructure_nodes_default_deny_and_correlated_rate_limited_logs() -> (
    None
):
    data = sec170.load_inventory()
    plan = sec170.render_plan(data)
    assert set(plan) == {
        node["id"]
        for node in data["nodes"]
        if node.get("role") in {"dist", "edge", "access", "isp"}
    }
    for node_id, tables in plan.items():
        policy = tables["inet"]
        assert policy.count("policy drop") >= 2, node_id
        assert f"SEC170|{node_id}|" in policy
        assert "limit rate 10/second burst 20 packets log" in policy
        assert "ct state established,related" in policy


def test_guest_policy_allows_only_approved_services_and_keeps_enterprise_scopes_closed() -> (
    None
):
    plan = sec170.render_plan(sec170.load_inventory())
    for node_id in ("hq-dist-1", "br1-dist-1", "br2-dist-1"):
        policy = plan[node_id]["inet"]
        guest_forward = policy.split("chain forward {", 1)[1].split("chain output", 1)[
            0
        ]
        assert "ip daddr 203.0.113.10 udp dport 53 counter accept" in guest_forward
        assert "ip daddr 203.0.113.10 tcp dport 53 counter accept" in guest_forward
        assert "ip daddr 203.0.113.11 udp dport 123 counter accept" in guest_forward
        assert "10.10.0.0/16" not in guest_forward.split('iifname "vlan30"', 1)[-1]
        assert "10.20.0.0/16" not in guest_forward.split('iifname "vlan30"', 1)[-1]
        assert "10.30.0.0/16" not in guest_forward.split('iifname "vlan30"', 1)[-1]
        assert "172.31.255.0/24" not in guest_forward.split('iifname "vlan30"', 1)[-1]
        assert "tcp dport 22 counter accept" not in guest_forward


def test_guest_nat_is_exactly_scoped_to_simulated_dns_and_ntp() -> None:
    plan = sec170.render_plan(sec170.load_inventory())
    for node_id, public_ip, guest_prefix in (
        ("hq-edge-1", "203.0.113.129", "10.10.30.0/24"),
        ("br1-edge-1", "203.0.113.130", "10.20.30.0/24"),
        ("br2-edge-1", "203.0.113.133", "10.30.30.0/24"),
    ):
        nat = plan[node_id]["nat"]
        assert (
            f"ip saddr {guest_prefix} ip daddr 203.0.113.10 udp dport 53 counter snat to {public_ip}"
            in nat
        )
        assert (
            f"ip saddr {guest_prefix} ip daddr 203.0.113.10 tcp dport 53 counter snat to {public_ip}"
            in nat
        )
        assert (
            f"ip saddr {guest_prefix} ip daddr 203.0.113.11 udp dport 123 counter snat to {public_ip}"
            in nat
        )
        assert "masquerade" not in nat
        assert "203.0.113.0/25" not in nat


def test_control_plane_is_interface_and_peer_scoped_and_ssh_is_oob_only() -> None:
    plan = sec170.render_plan(sec170.load_inventory())
    hq_edge = plan["hq-edge-1"]["inet"]
    assert 'iifname "eth3" ip saddr 192.0.2.1 tcp dport 179 counter accept' in hq_edge
    assert (
        'iifname "eth4" ip saddr 198.51.100.1 tcp dport 179 counter accept' in hq_edge
    )
    assert (
        'iifname "eth1" ip saddr 10.10.252.1 ip protocol ospf counter accept' in hq_edge
    )
    assert (
        'iifname "eth2" ip saddr 10.10.252.3 ip protocol ospf counter accept' in hq_edge
    )
    assert (
        'iifname "xfrm0" ip saddr 10.255.0.1 ip protocol ospf counter accept' in hq_edge
    )
    assert (
        'iifname "xfrm1" ip saddr 10.255.0.5 ip protocol ospf counter accept' in hq_edge
    )
    assert (
        'iifname "eth0" ip saddr 172.31.255.14 udp dport 161 counter accept' in hq_edge
    )
    assert "ip saddr 0.0.0.0/0 tcp dport 179" not in hq_edge
    for node_id, tables in plan.items():
        input_chain = (
            tables["inet"].split("chain input {", 1)[1].split("chain forward", 1)[0]
        )
        ssh_rules = [
            line for line in input_chain.splitlines() if "tcp dport 22" in line
        ]
        assert ssh_rules
        assert all(
            'iifname "eth0"' in line and "172.31.255.0/24" in line for line in ssh_rules
        )


def test_guest_local_service_access_is_preserved_and_no_data_vlan_gets_oob_access() -> (
    None
):
    plan = sec170.render_plan(sec170.load_inventory())
    for node_id in ("hq-dist-1", "br1-dist-1", "br2-dist-1"):
        forward = (
            plan[node_id]["inet"]
            .split("chain forward {", 1)[1]
            .split("chain output", 1)[0]
        )
        assert 'iifname "vlan30" ip daddr 10.' in forward
        assert "172.31.255.0/24" not in forward


def test_isp_forwards_only_advertised_endpoints_to_simulated_services() -> None:
    isp = sec170.render_plan(sec170.load_inventory())["isp1-core-1"]["inet"]
    forward = isp.split("chain forward {", 1)[1].split("chain output", 1)[0]
    assert (
        'iifname "eth1" ip saddr 203.0.113.129 ip daddr 203.0.113.11 udp dport 123 counter accept'
        in forward
    )
    assert (
        'iifname "eth2" ip saddr 203.0.113.130 ip daddr 203.0.113.10 udp dport 53 counter accept'
        in forward
    )
    assert "ip saddr 10.0.0.0/8" not in forward


def test_secondary_edges_enforce_the_same_tunnel_policy_with_edge_specific_endpoints() -> (
    None
):
    plan = sec170.render_plan(sec170.load_inventory())
    for node_id, remote in (
        ("hq-edge-2", "203.0.113.132"),
        ("br1-edge-2", "203.0.113.131"),
    ):
        policy = plan[node_id]["inet"]
        assert policy.count("tcp dport 179 counter accept") == 2
        assert policy.count("ip protocol ospf counter accept") >= 3
        assert f"ip saddr {remote} udp dport {{ 500, 4500 }} counter accept" in policy
        assert 'iifname "xfrm0"' in policy
        assert "NAT" not in plan[node_id]
        assert (
            "203.0.113.131" in plan[node_id]["nat"]
            if node_id == "hq-edge-2"
            else "203.0.113.132" in plan[node_id]["nat"]
        )


def test_site_services_can_only_reach_their_upstream_dns_and_ntp() -> None:
    plan = sec170.render_plan(sec170.load_inventory())
    for node_id, local in (
        ("hq-dist-1", "10.10"),
        ("br1-dist-1", "10.20"),
        ("br2-dist-1", "10.30"),
    ):
        forward = (
            plan[node_id]["inet"]
            .split("chain forward {", 1)[1]
            .split("chain output", 1)[0]
        )
        assert (
            f'iifname "vlan20" ip saddr {local}.20.11 ip daddr 203.0.113.10 udp dport 53 counter accept'
            in forward
        )
        assert (
            f'iifname "vlan20" ip saddr {local}.20.11 ip daddr 203.0.113.10 tcp dport 53 counter accept'
            in forward
        )
        assert (
            f'iifname "vlan20" ip saddr {local}.20.12 ip daddr 203.0.113.11 udp dport 123 counter accept'
            in forward
        )


def test_site_service_upstream_replies_have_scoped_asymmetric_return_path() -> None:
    plan = sec170.render_plan(sec170.load_inventory())
    for node_id, local in (
        ("hq-dist-1", "10.10"),
        ("hq-dist-2", "10.10"),
        ("br1-dist-1", "10.20"),
        ("br1-dist-2", "10.20"),
    ):
        forward = (
            plan[node_id]["inet"]
            .split("chain forward {", 1)[1]
            .split("chain output", 1)[0]
        )
        assert (
            f'iifname "eth1" ip saddr 203.0.113.10 ip daddr {local}.20.11 udp sport 53 counter accept'
            in forward
        )
        assert (
            f'iifname "eth1" ip saddr 203.0.113.10 ip daddr {local}.20.11 tcp sport 53 counter accept'
            in forward
        )
        assert (
            f'iifname "eth1" ip saddr 203.0.113.11 ip daddr {local}.20.12 udp sport 123 counter accept'
            in forward
        )


def test_snat_guest_service_replies_are_allowed_on_both_distribution_nodes() -> None:
    plan = sec170.render_plan(sec170.load_inventory())
    for node_id, guest_prefix in (
        ("hq-dist-1", "10.10.30.0/24"),
        ("hq-dist-2", "10.10.30.0/24"),
        ("br1-dist-1", "10.20.30.0/24"),
        ("br1-dist-2", "10.20.30.0/24"),
        ("br2-dist-1", "10.30.30.0/24"),
        ("br2-dist-2", "10.30.30.0/24"),
    ):
        forward = (
            plan[node_id]["inet"]
            .split("chain forward {", 1)[1]
            .split("chain output", 1)[0]
        )
        assert (
            f'iifname "eth1" ip saddr 203.0.113.10 ip daddr {guest_prefix} udp sport 53 counter accept'
            in forward
        )
        assert (
            f'iifname "eth1" ip saddr 203.0.113.10 ip daddr {guest_prefix} tcp sport 53 counter accept'
            in forward
        )
        assert (
            f'iifname "eth1" ip saddr 203.0.113.11 ip daddr {guest_prefix} udp sport 123 counter accept'
            in forward
        )


def test_isp_forwards_only_exact_inter_site_ike_and_esp_peers() -> None:
    isp = sec170.render_plan(sec170.load_inventory())["isp1-core-1"]["inet"]
    forward = isp.split("chain forward {", 1)[1].split("chain output", 1)[0]
    assert (
        'iifname "eth1" ip saddr 203.0.113.129 ip daddr 203.0.113.130 udp dport { 500, 4500 } counter accept'
        in forward
    )
    assert (
        'iifname "eth1" ip saddr 203.0.113.129 ip daddr 203.0.113.130 ip protocol esp counter accept'
        in forward
    )
    assert (
        'iifname "eth2" ip saddr 203.0.113.130 ip daddr 203.0.113.129 udp dport { 500, 4500 } counter accept'
        in forward
    )
    assert (
        "ip saddr 203.0.113.131 ip daddr 203.0.113.132 udp dport { 500, 4500 } counter accept"
        in forward
    )
    assert (
        "ip saddr 203.0.113.131 ip daddr 203.0.113.132 ip protocol esp counter accept"
        in forward
    )
    assert (
        "ip saddr 203.0.113.129 ip daddr 203.0.113.133 udp dport { 500, 4500 } counter accept"
        in forward
    )
    assert "ip saddr 10.0.0.0/8" not in forward


def test_inter_site_corporate_icmp_has_a_scoped_return_path() -> None:
    plan = sec170.render_plan(sec170.load_inventory())
    assert (
        'iifname "eth1" ip saddr 10.20.10.0/24 ip daddr 10.10.10.0/24 ip protocol icmp counter accept'
        in plan["hq-dist-1"]["inet"]
    )
    assert (
        'iifname "eth1" ip saddr 10.10.10.0/24 ip daddr 10.20.10.0/24 ip protocol icmp counter accept'
        in plan["br1-dist-1"]["inet"]
    )
    assert (
        'iifname "eth1" ip saddr 10.30.10.0/24 ip daddr 10.10.10.0/24 ip protocol icmp counter accept'
        in plan["hq-dist-1"]["inet"]
    )
    assert (
        'iifname "eth1" ip saddr 10.10.10.0/24 ip daddr 10.30.10.0/24 ip protocol icmp counter accept'
        in plan["br2-dist-1"]["inet"]
    )


def test_explicit_permits_precede_invalid_default_deny_and_denials_remain_logged() -> (
    None
):
    plan = sec170.render_plan(sec170.load_inventory())
    forward = (
        plan["hq-dist-1"]["inet"]
        .split("chain forward {", 1)[1]
        .split("chain output", 1)[0]
    )
    remote_permit = 'iifname "eth1" ip saddr 10.20.10.0/24 ip daddr 10.10.10.0/24 ip protocol icmp counter accept'
    assert forward.index(remote_permit) < forward.index("limit rate 10/second")
    assert forward.index("limit rate 10/second") < forward.index(
        "ct state invalid counter drop"
    )
    assert forward.index("ct state invalid counter drop") < forward.rindex(
        "counter drop"
    )
