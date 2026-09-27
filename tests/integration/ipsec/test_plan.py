from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from config.ipsec.apply import _reported_xfrm_if_id, build_plan, load_inventory
sys.path.insert(0, str(ROOT / "tests" / "integration" / "ipsec"))
from traffic import build_echo_request


def test_both_xfrm_paths_are_derived_from_inventory() -> None:
    plan = build_plan(load_inventory())
    peers = {peer["node"]: peer for peer in plan["peers"]}

    assert set(peers) == {"hq-edge-1", "br1-edge-1", "hq-edge-2", "br1-edge-2"}
    assert peers["hq-edge-1"]["address"] == "10.255.0.0/31"
    assert peers["br1-edge-1"]["address"] == "10.255.0.1/31"
    assert peers["hq-edge-2"]["address"] == "10.255.0.2/31"
    assert peers["br1-edge-2"]["address"] == "10.255.0.3/31"
    assert str(peers["hq-edge-1"]["public_endpoint"]) == "203.0.113.129/32"
    assert str(peers["br1-edge-1"]["public_endpoint"]) == "203.0.113.130/32"
    assert str(peers["hq-edge-2"]["public_endpoint"]) == "203.0.113.131/32"
    assert str(peers["br1-edge-2"]["public_endpoint"]) == "203.0.113.132/32"
    assert peers["hq-edge-1"]["remote_public_endpoint"] == peers["br1-edge-1"]["public_endpoint"]
    assert peers["br1-edge-2"]["remote_public_endpoint"] == peers["hq-edge-2"]["public_endpoint"]
    assert peers["hq-edge-1"]["ospf_cost"] == peers["br1-edge-1"]["ospf_cost"] == 10
    assert peers["hq-edge-2"]["ospf_cost"] == peers["br1-edge-2"]["ospf_cost"] == 100


def test_swanctl_uses_certificate_ikev2_and_the_accepted_crypto_profile() -> None:
    plan = build_plan(load_inventory())
    for peer in plan["peers"]:
        config = peer["swanctl_config"]
        assert "version = 2" in config
        assert "auth = pubkey" in config
        assert "aes256gcm16-prfsha384-ecp384" in config
        assert "esp_proposals = aes256gcm16-ecp384" in config
        expected_id = str(peer["xfrm_if_id"])
        assert f"if_id_in = {expected_id}" in config and f"if_id_out = {expected_id}" in config
        assert "0.0.0.0/0" in config
        assert "psk" not in config.lower()


def test_overlay_mtu_and_mss_are_derived_consistently() -> None:
    plan = build_plan(load_inventory())
    assert plan["mtu"] == 1400
    assert plan["tcp_mss"] == 1360


def test_xfrm_if_id_accepts_iproute2_decimal_and_hex_output() -> None:
    assert _reported_xfrm_if_id("xfrm if_id 42 addrgenmode random") == 42
    assert _reported_xfrm_if_id("xfrm if_id 0x2a addrgenmode random") == 42
    assert _reported_xfrm_if_id("xfrm if_id 0x2b addrgenmode random") == 43


def test_xfrm_topology_requires_two_peer_links() -> None:
    data = load_inventory()
    data["links"] = [link for link in data["links"] if link.get("kind") != "xfrm"]
    with pytest.raises(ValueError, match="exactly two HQ-to-BR1 XFRM links"):
        build_plan(data)


def test_pki_generator_only_writes_runtime_paths() -> None:
    script = (ROOT / "scripts" / "pki" / "generate.sh").read_text(encoding="utf-8")
    assert "PKI_DIR=/run/netlab/ipsec-pki" in script
    assert "chmod 0600" in script
    assert "umask 077" in script
    assert 'out "${PKI_DIR}/' in script
def test_ping_socket_request_has_linux_icmp_echo_header() -> None:
    packet = build_echo_request(7, b"payload")
    assert packet[:2] == bytes((8, 0))
    assert packet[2:4] == b"\x00\x00"
    assert packet[4:8] == b"\x00\x00\x00\x07"
    assert packet[8:] == b"payload"
