from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from config.ipsec.apply import _reported_xfrm_if_id, build_plan, load_inventory


def test_phase_one_xfrm_peers_are_derived_from_inventory() -> None:
    plan = build_plan(load_inventory())
    peers = {peer["site"]: peer for peer in plan["peers"]}

    assert set(peers) == {"hq", "br1"}
    assert peers["hq"]["address"] == "10.255.0.0/31"
    assert peers["br1"]["address"] == "10.255.0.1/31"
    assert str(peers["hq"]["public_endpoint"]) == "203.0.113.129/32"
    assert str(peers["br1"]["public_endpoint"]) == "203.0.113.130/32"
    assert peers["hq"]["remote_public_endpoint"] == peers["br1"]["public_endpoint"]
    assert peers["br1"]["remote_public_endpoint"] == peers["hq"]["public_endpoint"]


def test_swanctl_uses_certificate_ikev2_and_the_accepted_crypto_profile() -> None:
    plan = build_plan(load_inventory())
    for peer in plan["peers"]:
        config = peer["swanctl_config"]
        assert "version = 2" in config
        assert "auth = pubkey" in config
        assert "aes256gcm16-prfsha384-ecp384" in config
        assert "esp_proposals = aes256gcm16-ecp384" in config
        assert "if_id_in = 42" in config and "if_id_out = 42" in config
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


def test_xfrm_topology_must_have_one_peer_link() -> None:
    data = load_inventory()
    data["links"] = [link for link in data["links"] if link.get("kind") != "xfrm"]
    with pytest.raises(ValueError, match="exactly one HQ-to-BR1 XFRM link"):
        build_plan(data)


def test_pki_generator_only_writes_runtime_paths() -> None:
    script = (ROOT / "scripts" / "pki" / "generate.sh").read_text(encoding="utf-8")
    assert "PKI_DIR=/run/netlab/ipsec-pki" in script
    assert "chmod 0600" in script
    assert "umask 077" in script
    assert 'out "${PKI_DIR}/' in script
