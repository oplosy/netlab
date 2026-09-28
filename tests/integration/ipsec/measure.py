#!/usr/bin/env python3
"""Live certificate, XFRM, ESP, MTU/MSS, rekey, and recovery acceptance."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from config.ipsec.apply import (
    LAB_NAME,
    _read_remote,
    _reported_xfrm_if_id,
    _run,
    _write_remote,
    build_plan,
    load_inventory,
)
from config.routing.bgp.render import PRIMARY_PROVIDER, _interface_map

CAPTURE_SCRIPT = ROOT / "tests" / "integration" / "ipsec" / "capture.py"
TRAFFIC_SCRIPT = ROOT / "tests" / "integration" / "ipsec" / "traffic.py"
CAPTURE_FILE = "/tmp/netlab-ipsec-underlay.pcap"
CAPTURE_LOG = "/tmp/netlab-ipsec-capture.json"
CHARON_LOG = "/tmp/netlab-ipsec-charon.log"
CAPTURE_DURATION = 18
MAX_REKEY_GAP_SECONDS = 3.0


def _exec(
    docker: str, container: str, *argv: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([docker, "exec", container, *argv], text=True, capture_output=True, check=False)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{container} {' '.join(argv)} failed: {detail}")
    return result


def primary_overlay(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the (HQ, BR1) peers of the lowest-cost HQ-BR1 XFRM link."""
    links: dict[str, dict[str, dict[str, Any]]] = {}
    for peer in plan["peers"]:
        links.setdefault(peer["link"], {})[peer["site"]] = peer
    candidates = sorted(
        (pair for pair in links.values() if set(pair) == {"hq", "br1"}),
        key=lambda pair: pair["hq"]["ospf_cost"],
    )
    if not candidates:
        raise ValueError("inventory has no HQ-BR1 XFRM overlay")
    if len(candidates) > 1 and candidates[0]["hq"]["ospf_cost"] == candidates[1]["hq"]["ospf_cost"]:
        raise ValueError("HQ-BR1 overlays share the lowest OSPF cost; no primary overlay")
    return candidates[0]["hq"], candidates[0]["br1"]


def capture_interfaces(data: dict[str, Any], provider: str, nodes: tuple[str, ...]) -> list[str]:
    """Return the provider's kernel interfaces facing the given edge nodes."""
    mapping = _interface_map(next(node for node in data["nodes"] if node["id"] == provider))
    interfaces = [
        mapping[local["interface"]]
        for link in data["links"]
        if link.get("kind") == "ebgp"
        and {endpoint["node"] for endpoint in link["endpoints"]} & set(nodes)
        for local in link["endpoints"]
        if local["node"] == provider
    ]
    if len(interfaces) != len(nodes):
        raise ValueError(f"{provider} does not peer with every node in {nodes}")
    return sorted(interfaces)


def sa_state(output: str, connection: str) -> tuple[bool, bool]:
    """Return (IKE SA established, CHILD SA installed) for one connection.

    The IKE SA shares the child's name, so only an indented child line with
    INSTALLED proves the tunnel carries traffic.
    """
    name = re.escape(connection)
    ike = re.search(rf"^{name}: #\d+, ESTABLISHED", output, re.M) is not None
    child = re.search(rf"^\s+{name}: #\d+, reqid \d+, INSTALLED", output, re.M) is not None
    return ike, child


def _wait_route(docker: str, peer: dict[str, Any]) -> str:
    endpoint = str(peer["remote_public_endpoint"].ip)
    for _ in range(60):
        result = _exec(docker, peer["container"], "ip", "route", "get", endpoint, check=False)
        if result.returncode == 0 and not re.search(r"\bdev xfrm\d+\b", result.stdout):
            return result.stdout.strip()
        time.sleep(0.5)
    raise TimeoutError(f"{peer['node']} has no underlay route to IKE endpoint {endpoint}")


def _sa_output(docker: str, peer: dict[str, Any]) -> str:
    result = _exec(
        docker, peer["container"], "swanctl", "--list-sas", "--ike", peer["connection"], check=False
    )
    if result.returncode:
        return ""
    return result.stdout


def _wait_sa(docker: str, peer: dict[str, Any], established: bool) -> str:
    for _ in range(60):
        output = _sa_output(docker, peer)
        ike, child = sa_state(output, peer["connection"])
        if (ike and child) if established else not (ike or child):
            return output
        time.sleep(0.25)
    state = "established with an installed CHILD SA" if established else "down"
    raise TimeoutError(f"{peer['node']} {peer['connection']} did not become {state}")


def _traffic(
    docker: str,
    peer: dict[str, Any],
    mode: str,
    target: str,
    *,
    count: int = 3,
    size: int = 32,
    expect: str = "up",
    interval: float = 0.2,
    check: bool = True,
) -> dict[str, Any]:
    result = _exec(
        docker,
        peer["container"],
        "python3", "/tmp/netlab-ipsec-traffic.py", mode, target,
        "--interface", peer["xfrm_interface"],
        "--count", str(count), "--size", str(size), "--timeout", "0.5",
        "--interval", str(interval), "--expect", expect,
        check=False,
    )
    payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{peer['node']} {mode} to {target} failed: {detail}")
    return payload


def _prepare_capture(docker: str, isp_container: str, interfaces: list[str]) -> None:
    _write_remote(docker, isp_container, "/tmp/netlab-ipsec-capture.py", CAPTURE_SCRIPT.read_bytes(), "0700")
    _exec(docker, isp_container, "rm", "-f", CAPTURE_FILE, CAPTURE_LOG)
    _run(
        docker,
        "exec", "-d", isp_container, "sh", "-c",
        f"python3 /tmp/netlab-ipsec-capture.py {CAPTURE_FILE} {CAPTURE_DURATION} {' '.join(interfaces)} >{CAPTURE_LOG} 2>&1",
    )
    for _ in range(40):
        result = _exec(docker, isp_container, "test", "-e", CAPTURE_FILE, check=False)
        if result.returncode == 0:
            return
        time.sleep(0.1)
    log = _exec(docker, isp_container, "cat", CAPTURE_LOG, check=False).stdout.strip()
    raise RuntimeError(f"ISP packet capture failed to start: {log or 'capture file was not created'}")


def _capture_result(docker: str, isp_container: str) -> tuple[dict[str, Any], Path]:
    deadline = time.monotonic() + CAPTURE_DURATION + 5
    output = ""
    while time.monotonic() < deadline:
        result = _exec(docker, isp_container, "cat", CAPTURE_LOG, check=False)
        output = result.stdout.strip()
        if output.startswith("{"):
            break
        if output and "failed" in output.lower():
            raise RuntimeError(f"ISP packet capture failed: {output}")
        time.sleep(0.25)
    else:
        raise TimeoutError(f"ISP capture did not complete: {output}")

    metadata = json.loads(output)
    capture = _read_remote(docker, isp_container, CAPTURE_FILE)
    artifact = Path(tempfile.gettempdir()) / "netlab-ipsec-underlay.pcap"
    artifact.write_bytes(capture)
    return metadata, artifact


def _inspect_capture(path: Path) -> dict[str, int]:
    raw = path.read_bytes()
    if len(raw) < 24 or raw[:4] != bytes.fromhex("d4c3b2a1"):
        raise ValueError("underlay capture is not a classic little-endian PCAP")
    offset = 24
    esp = ike = enterprise_plaintext = 0
    private_networks = tuple(
        ipaddress.ip_network(cidr)
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    )
    while offset + 16 <= len(raw):
        _, _, captured, _ = struct.unpack_from("<IIII", raw, offset)
        offset += 16
        frame = raw[offset : offset + captured]
        offset += captured
        if len(frame) < 34 or frame[12:14] != b"\x08\x00":
            continue
        ip_offset = 14
        ihl = (frame[ip_offset] & 15) * 4
        if ihl < 20 or len(frame) < ip_offset + ihl:
            continue
        protocol = frame[ip_offset + 9]
        source = ipaddress.ip_address(frame[ip_offset + 12 : ip_offset + 16])
        destination = ipaddress.ip_address(frame[ip_offset + 16 : ip_offset + 20])
        if protocol == 50:
            esp += 1
        elif protocol == 17 and len(frame) >= ip_offset + ihl + 4:
            source_port, destination_port = struct.unpack_from("!HH", frame, ip_offset + ihl)
            ike += int(source_port == 500 or destination_port == 500)
        if protocol != 50 and any(source in network or destination in network for network in private_networks):
            enterprise_plaintext += 1
    return {"esp_packets": esp, "ike_packets": ike, "enterprise_plaintext_packets": enterprise_plaintext}


def _verify_site(
    docker: str, hq: dict[str, Any], br1: dict[str, Any], isp: str, capture: list[str]
) -> dict[str, Any]:
    connection = hq["connection"]
    for peer in (hq, br1):
        interface = peer["xfrm_interface"]
        _wait_route(docker, peer)
        xfrm = _exec(docker, peer["container"], "ip", "-d", "-o", "link", "show", "dev", interface).stdout
        if _reported_xfrm_if_id(xfrm) != peer["xfrm_if_id"]:
            raise RuntimeError(f"{peer['node']} XFRM interface ID {peer['xfrm_if_id']} is missing on {interface}")
        address = _exec(docker, peer["container"], "ip", "-o", "-4", "address", "show", "dev", interface).stdout
        if peer["address"] not in address:
            raise RuntimeError(f"{peer['node']} is missing {peer['address']} on {interface}")
        rules = _exec(docker, peer["container"], "nft", "list", "chain", "inet", "netlab_ipsec_mss", "forward").stdout
        if "1360" not in rules or interface not in rules:
            raise RuntimeError(f"{peer['node']} TCP MSS clamp is missing")

    _traffic(docker, hq, "probe", hq["peer_address"], expect="down")
    for peer in (hq, br1):
        _run(docker, "exec", "-d", peer["container"], "sh", "-c", f"swanctl --log >{CHARON_LOG} 2>&1")
    _prepare_capture(docker, isp, capture)
    initiate = _exec(
        docker, hq["container"], "swanctl", "--initiate", "--child", connection, check=False
    )
    if initiate.returncode:
        charon_log = "\n".join(
            f"{peer['node']}: {_exec(docker, peer['container'], 'cat', CHARON_LOG, check=False).stdout[-3000:]}"
            for peer in (hq, br1)
        )
        detail = initiate.stderr.strip() or initiate.stdout.strip()
        raise RuntimeError(f"IKE initiation failed: {detail}\ncharon VICI log tail:\n{charon_log[-6000:]}")
    hq_sas = _wait_sa(docker, hq, True)
    br_sas = _wait_sa(docker, br1, True)
    if any("AES_GCM_16" not in output or "ECP_384" not in output for output in (hq_sas, br_sas)):
        raise RuntimeError(f"negotiated algorithms do not match ADR-0007: {hq_sas}")
    _traffic(docker, hq, "probe", hq["peer_address"], expect="up")
    _traffic(docker, br1, "probe", br1["peer_address"], expect="up")

    _traffic(docker, hq, "probe", hq["peer_address"], size=1372, expect="up", count=2)
    oversize = _exec(
        docker, hq["container"], "python3", "/tmp/netlab-ipsec-traffic.py", "probe", hq["peer_address"],
        "--interface", hq["xfrm_interface"], "--count", "1", "--size", "1373", "--timeout", "0.5", "--interval", "0", "--expect", "up",
        check=False,
    )
    if oversize.returncode == 0:
        raise RuntimeError("oversized DF packet unexpectedly crossed the 1400-byte XFRM MTU")
    if oversize.returncode not in (1, 2):
        raise RuntimeError(f"oversize MTU test failed unexpectedly: {oversize.stderr or oversize.stdout}")

    stream = subprocess.Popen(
        [
            docker, "exec", hq["container"], "python3", "/tmp/netlab-ipsec-traffic.py", "stream", hq["peer_address"],
            "--interface", hq["xfrm_interface"], "--count", "80", "--size", "32", "--timeout", "0.5", "--interval", "0.1",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.4)
    rekey_started = time.monotonic()
    _exec(docker, hq["container"], "swanctl", "--rekey", "--child", connection)
    rekey_duration = time.monotonic() - rekey_started
    stdout, stderr = stream.communicate(timeout=15)
    if stream.returncode:
        raise RuntimeError(f"continuous XFRM traffic failed during CHILD_SA rekey: {stderr.strip() or stdout.strip()}")
    stream_result = json.loads(stdout)
    if stream_result["max_gap_seconds"] > MAX_REKEY_GAP_SECONDS:
        raise RuntimeError(
            f"rekey traffic gap {stream_result['max_gap_seconds']}s exceeds {MAX_REKEY_GAP_SECONDS}s objective"
        )
    if stream_result["received"] < 70:
        raise RuntimeError(f"rekey stream received only {stream_result['received']}/80 probes")

    _exec(docker, hq["container"], "swanctl", "--terminate", "--ike", connection)
    _wait_sa(docker, hq, False)
    _wait_sa(docker, br1, False)
    _traffic(docker, hq, "probe", hq["peer_address"], expect="down")
    _exec(docker, hq["container"], "swanctl", "--initiate", "--child", connection)
    _wait_sa(docker, hq, True)
    _wait_sa(docker, br1, True)
    _traffic(docker, hq, "probe", hq["peer_address"], expect="up")

    capture_metadata, capture_path = _capture_result(docker, isp)
    captured = _inspect_capture(capture_path)
    if captured["esp_packets"] == 0 or captured["ike_packets"] == 0:
        raise RuntimeError(f"underlay PCAP lacks ESP or IKE packets: {captured}")
    if captured["enterprise_plaintext_packets"]:
        raise RuntimeError(f"underlay PCAP includes cleartext enterprise traffic: {captured}")
    if capture_metadata["packets"] == 0:
        raise RuntimeError("underlay PCAP is empty")

    return {
        "site_pair": [hq["site"], br1["site"]],
        "connection": connection,
        "ike_child_sa": "pass",
        "xfrm_peer_traffic_only_with_sa": "pass",
        "xfrm_mtu": 1400,
        "mss_clamp": 1360,
        "rekey_max_gap_seconds": stream_result["max_gap_seconds"],
        "rekey_command_seconds": round(rekey_duration, 3),
        "recovery": "pass",
        "underlay_capture": captured,
        "underlay_capture_path": str(capture_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=ROOT / "inventory" / "inventory.yaml")
    parser.add_argument("--docker", default="docker")
    args = parser.parse_args()
    try:
        data = load_inventory(args.inventory)
        hq, br1 = primary_overlay(build_plan(data))
        isp = f"clab-{LAB_NAME}-{PRIMARY_PROVIDER}"
        capture = capture_interfaces(data, PRIMARY_PROVIDER, (hq["node"], br1["node"]))
        _write_remote(args.docker, hq["container"], "/tmp/netlab-ipsec-traffic.py", TRAFFIC_SCRIPT.read_bytes(), "0700")
        _write_remote(args.docker, br1["container"], "/tmp/netlab-ipsec-traffic.py", TRAFFIC_SCRIPT.read_bytes(), "0700")
        result = _verify_site(args.docker, hq, br1, isp, capture)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError, TimeoutError, RuntimeError) as exc:
        print(f"IPsec live acceptance failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
