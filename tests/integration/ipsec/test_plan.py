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
    peers = {(peer["link"], peer["node"]): peer for peer in plan["peers"]}

    assert len(peers) == 6
    assert peers[("hq-br1-xfrm", "hq-edge-1")]["address"] == "10.255.0.0/31"
    assert peers[("hq-br1-xfrm", "br1-edge-1")]["address"] == "10.255.0.1/31"
    assert peers[("hq-br1-edge2-xfrm", "hq-edge-2")]["address"] == "10.255.0.2/31"
    assert peers[("hq-br1-edge2-xfrm", "br1-edge-2")]["address"] == "10.255.0.3/31"
    assert peers[("hq-br2-xfrm", "hq-edge-1")]["address"] == "10.255.0.4/31"
    assert peers[("hq-br2-xfrm", "br2-edge-1")]["address"] == "10.255.0.5/31"
    assert peers[("hq-br2-xfrm", "br2-edge-1")]["xfrm_interface"] == "xfrm0"
    assert peers[("hq-br2-xfrm", "hq-edge-1")]["xfrm_interface"] == "xfrm1"
    assert (
        str(peers[("hq-br1-xfrm", "hq-edge-1")]["public_endpoint"])
        == "203.0.113.129/32"
    )
    assert (
        str(peers[("hq-br1-xfrm", "hq-edge-1")]["remote_public_endpoint"])
        == "203.0.113.130/32"
    )
    assert (
        str(peers[("hq-br1-edge2-xfrm", "hq-edge-2")]["remote_public_endpoint"])
        == "203.0.113.132/32"
    )
    assert (
        str(peers[("hq-br2-xfrm", "hq-edge-1")]["remote_public_endpoint"])
        == "203.0.113.133/32"
    )
    assert (
        peers[("hq-br1-xfrm", "hq-edge-1")]["ospf_cost"]
        == peers[("hq-br2-xfrm", "hq-edge-1")]["ospf_cost"]
        == 10
    )
    assert peers[("hq-br1-edge2-xfrm", "hq-edge-2")]["ospf_cost"] == 100


def test_swanctl_uses_certificate_ikev2_and_the_accepted_crypto_profile() -> None:
    plan = build_plan(load_inventory())
    for peer in plan["peers"]:
        config = peer["swanctl_config"]
        assert "version = 2" in config
        assert "auth = pubkey" in config
        assert "aes256gcm16-prfsha384-ecp384" in config
        assert "esp_proposals = aes256gcm16-ecp384" in config
        expected_id = str(peer["xfrm_if_id"])
        assert (
            f"if_id_in = {expected_id}" in config
            and f"if_id_out = {expected_id}" in config
        )
        assert "0.0.0.0/0" in config
        assert "psk" not in config.lower()
        # Overlays must stay on the public underlay: MOBIKE would advertise
        # and roam to the OOB management address (ADR 0008).
        assert "mobike = no" in config
        assert f"local_addrs = {peer['public_endpoint'].ip}\n" in config


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
    with pytest.raises(ValueError, match="two redundant HQ-to-BR1 XFRM links"):
        build_plan(data)


def test_pki_generator_only_writes_runtime_paths() -> None:
    script = (ROOT / "scripts" / "pki" / "generate.sh").read_text(encoding="utf-8")
    assert "PKI_DIR=/run/netlab/ipsec-pki" in script
    assert "chmod 0600" in script
    assert "umask 077" in script
    assert 'out "${PKI_DIR}/' in script
    assert "(hq|br1)-edge-[12]|br2-edge-1" in script
    assert "br2-edge-2" not in script


def test_ping_socket_request_has_linux_icmp_echo_header() -> None:
    packet = build_echo_request(7, b"payload")
    assert packet[:2] == bytes((8, 0))
    assert packet[2:4] == b"\x00\x00"
    assert packet[4:8] == b"\x00\x00\x00\x07"
    assert packet[8:] == b"payload"


def test_live_measurement_targets_the_primary_hq_br1_overlay() -> None:
    from measure import capture_interfaces, primary_overlay

    data = load_inventory()
    hq, br1 = primary_overlay(build_plan(data))

    assert (hq["node"], br1["node"]) == ("hq-edge-1", "br1-edge-1")
    assert hq["connection"] == br1["connection"] == "site-overlay-hq_br1_xfrm"
    assert hq["xfrm_interface"] == "xfrm0"
    assert capture_interfaces(data, "isp1-core-1", (hq["node"], br1["node"])) == [
        "eth1",
        "eth2",
    ]


def test_sa_state_requires_an_installed_child_of_the_named_connection() -> None:
    from measure import sa_state

    connection = "site-overlay-hq_br1_xfrm"
    installed = (
        "site-overlay-hq_br1_xfrm: #3, ESTABLISHED, IKEv2, 1a2b_i* 3c4d_r\n"
        "  local  'hq-edge-1.netlab' @ 203.0.113.129[4500]\n"
        "  AES_GCM_16-256/PRF_HMAC_SHA2_384/ECP_384\n"
        "  site-overlay-hq_br1_xfrm: #5, reqid 1, INSTALLED, TUNNEL, "
        "ESP:AES_GCM_16-256/ECP_384\n"
    )
    ike_only = installed.split("  site-overlay-hq_br1_xfrm: #5")[0]
    other = installed.replace("hq_br1_xfrm", "hq_br2_xfrm")

    assert sa_state(installed, connection) == (True, True)
    assert sa_state(ike_only, connection) == (True, False)
    assert sa_state(other, connection) == (False, False)
    assert sa_state("", connection) == (False, False)
