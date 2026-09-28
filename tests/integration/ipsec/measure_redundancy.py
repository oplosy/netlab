#!/usr/bin/env python3
"""Live acceptance for both encrypted edge paths and OSPF path preference."""

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
from config.ipsec.apply import (  # noqa: E402
    LAB_NAME,
    _read_remote,
    _run,
    _write_remote,
    build_plan,
    load_inventory,
)

CAPTURE_SCRIPT = ROOT / "tests" / "integration" / "ipsec" / "capture.py"
TRAFFIC_SCRIPT = ROOT / "tests" / "integration" / "ipsec" / "traffic.py"
CAPTURE_FILE = "/tmp/netlab-wan-230.pcap"
CAPTURE_LOG = "/tmp/netlab-wan-230-capture.json"
CAPTURE_SECONDS = 8
HQ_ROUTE = "10.20.0.0/16"


def _exec(docker: str, node: str, *argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(docker, "exec", f"clab-{LAB_NAME}-{node}", *argv, check=check)


def _wait_sa(docker: str, peer: dict[str, Any]) -> str:
    for _ in range(60):
        result = _exec(docker, peer["node"], "swanctl", "--list-sas", check=False)
        if result.returncode == 0 and "ESTABLISHED" in result.stdout and peer["connection"] in result.stdout:
            return result.stdout
        time.sleep(0.25)
    raise TimeoutError(f"{peer['node']} did not establish {peer['connection']}")


def _valid_outer_endpoints(peer: dict[str, Any], output: str) -> bool:
    expected = {str(peer["public_endpoint"].ip), str(peer["remote_public_endpoint"].ip)}
    observed = set(re.findall(r"@\s+([0-9.]+)\[", output))
    return observed == expected


def _route(docker: str, node: str) -> str:
    result = _exec(docker, node, "vtysh", "-c", f"show ip route {HQ_ROUTE}", check=False)
    return result.stdout.strip() or result.stderr.strip()


def _wait_route(docker: str, node: str, next_hop: str) -> str:
    deadline = time.monotonic() + 30
    last = ""
    while time.monotonic() < deadline:
        last = _route(docker, node)
        if next_hop in last:
            return last
        time.sleep(0.25)
    raise TimeoutError(f"{node} did not route {HQ_ROUTE} through {next_hop}: {last}")


def _start_capture(docker: str, container: str) -> None:
    _write_remote(docker, container, "/tmp/netlab-wan-230-capture.py", CAPTURE_SCRIPT.read_bytes(), "0700")
    _exec(docker, "isp1-core-1", "rm", "-f", CAPTURE_FILE, CAPTURE_LOG)
    _run(
        docker, "exec", "-d", container, "sh", "-c",
        f"python3 /tmp/netlab-wan-230-capture.py {CAPTURE_FILE} {CAPTURE_SECONDS} eth1 eth2 eth5 eth6 >{CAPTURE_LOG} 2>&1",
    )
    for _ in range(40):
        if _exec(docker, "isp1-core-1", "test", "-e", CAPTURE_FILE, check=False).returncode == 0:
            return
        time.sleep(0.1)
    raise RuntimeError("ISP-1 capture did not start")


def _wait_capture(docker: str, container: str) -> tuple[dict[str, Any], Path]:
    deadline = time.monotonic() + CAPTURE_SECONDS + 5
    output = ""
    while time.monotonic() < deadline:
        output = _exec(docker, "isp1-core-1", "cat", CAPTURE_LOG, check=False).stdout.strip()
        if output.startswith("{"):
            break
        time.sleep(0.25)
    else:
        raise TimeoutError(f"ISP-1 capture did not finish: {output}")
    artifact = Path(tempfile.gettempdir()) / "netlab-wan-230.pcap"
    artifact.write_bytes(_read_remote(docker, container, CAPTURE_FILE))
    return json.loads(output), artifact


def _esp_pairs(path: Path) -> dict[str, int]:
    raw = path.read_bytes()
    if len(raw) < 24 or raw[:4] != bytes.fromhex("d4c3b2a1"):
        raise ValueError("capture is not a classic little-endian PCAP")
    counts: dict[str, int] = {}
    offset = 24
    while offset + 16 <= len(raw):
        _, _, captured, _ = struct.unpack_from("<IIII", raw, offset)
        offset += 16
        frame = raw[offset : offset + captured]
        offset += captured
        if len(frame) < 34 or frame[12:14] != b"\x08\x00":
            continue
        ip_offset = 14
        ihl = (frame[ip_offset] & 15) * 4
        if ihl < 20 or len(frame) < ip_offset + ihl or frame[ip_offset + 9] != 50:
            continue
        source = str(ipaddress.ip_address(frame[ip_offset + 12 : ip_offset + 16]))
        destination = str(ipaddress.ip_address(frame[ip_offset + 16 : ip_offset + 20]))
        key = "-".join(sorted((source, destination)))
        counts[key] = counts.get(key, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    down: list[str] = []
    try:
        data = load_inventory()
        plan = build_plan(data)
        peers = {peer["node"]: peer for peer in plan["peers"]}
        for peer in peers.values():
            container = f"clab-{LAB_NAME}-{peer['node']}"
            _write_remote(args.docker, container, "/tmp/netlab-ipsec-traffic.py", TRAFFIC_SCRIPT.read_bytes(), "0700")

        for node in ("hq-edge-1", "hq-edge-2"):
            peer = peers[node]
            current = _exec(args.docker, node, "swanctl", "--list-sas", check=False)
            established = (
                current.returncode == 0
                and "ESTABLISHED" in current.stdout
                and peer["connection"] in current.stdout
                and _valid_outer_endpoints(peer, current.stdout)
            )
            if current.returncode == 0 and peer["connection"] in current.stdout and not _valid_outer_endpoints(peer, current.stdout):
                _exec(args.docker, node, "swanctl", "--terminate", "--ike", peer["connection"], check=False)
            if not established:
                initiated = _exec(args.docker, node, "swanctl", "--initiate", "--child", peer["connection"], check=False)
                if initiated.returncode:
                    # A concurrent/previous initiation may have completed as
                    # swanctl returned failure; confirm the resulting peer SAs.
                    try:
                        _wait_sa(args.docker, peer)
                    except TimeoutError as exc:
                        raise RuntimeError(initiated.stderr.strip() or initiated.stdout.strip()) from exc
        for peer in peers.values():
            sa = _wait_sa(args.docker, peer)
            if not _valid_outer_endpoints(peer, sa):
                raise RuntimeError(f"{peer['node']} negotiated the overlay over a non-public underlay: {sa}")

        dist1_primary = next(
            endpoint["address"].split("/")[0]
            for link in data["links"] if link["kind"] == "routed"
            and {item["node"] for item in link["endpoints"]} == {"hq-edge-1", "hq-dist-1"}
            for endpoint in link["endpoints"] if endpoint["node"] == "hq-edge-1"
        )
        dist1_secondary = next(
            endpoint["address"].split("/")[0]
            for link in data["links"] if link["kind"] == "routed"
            and {item["node"] for item in link["endpoints"]} == {"hq-edge-2", "hq-dist-1"}
            for endpoint in link["endpoints"] if endpoint["node"] == "hq-edge-2"
        )
        container = f"clab-{LAB_NAME}-isp1-core-1"
        _start_capture(args.docker, container)
        for hq_node, br_node in (("hq-edge-1", "br1-edge-1"), ("hq-edge-2", "br1-edge-2")):
            _exec(
                args.docker, hq_node, "python3", "/tmp/netlab-ipsec-traffic.py", "probe",
                peers[hq_node]["peer_address"], "--count", "20", "--size", "48", "--timeout", "0.5",
                "--interval", "0.05", "--expect", "up",
            )
            _exec(
                args.docker, br_node, "python3", "/tmp/netlab-ipsec-traffic.py", "probe",
                peers[br_node]["peer_address"], "--count", "20", "--size", "48", "--timeout", "0.5",
                "--interval", "0.05", "--expect", "up",
            )

        primary_route = _wait_route(args.docker, "hq-dist-1", dist1_primary)
        for node in ("hq-edge-1", "br1-edge-1"):
            _exec(args.docker, node, "ip", "link", "set", "dev", "xfrm0", "down")
            down.append(node)
        backup_route = _wait_route(args.docker, "hq-dist-1", dist1_secondary)
        for node in reversed(down):
            _exec(args.docker, node, "ip", "link", "set", "dev", "xfrm0", "up")
        down.clear()
        recovered_route = _wait_route(args.docker, "hq-dist-1", dist1_primary)

        capture_metadata, capture_path = _wait_capture(args.docker, container)
        esp = _esp_pairs(capture_path)
        required_pairs = {
            "-".join(sorted((str(peers[hq]["public_endpoint"].ip), str(peers[br]["public_endpoint"].ip))))
            for hq, br in (("hq-edge-1", "br1-edge-1"), ("hq-edge-2", "br1-edge-2"))
        }
        missing = required_pairs - {pair for pair, count in esp.items() if count > 0}
        if missing:
            raise RuntimeError(f"underlay capture did not observe ESP for both edge pairs: {sorted(missing)}")
        if capture_metadata["packets"] == 0:
            raise RuntimeError("underlay capture is empty")
        result = {
            "task": "WAN-230",
            "peer_sas": {node: peer["connection"] for node, peer in peers.items()},
            "esp_packets_by_endpoint_pair": {pair: esp[pair] for pair in sorted(required_pairs)},
            "ospf": {
                "healthy_primary_next_hop": dist1_primary,
                "primary_path_down_next_hop": dist1_secondary,
                "recovered_primary_next_hop": dist1_primary,
                "primary_route": primary_route,
                "backup_route": backup_route,
                "recovered_route": recovered_route,
            },
            "capture_packets": capture_metadata["packets"],
            "capture_path": str(capture_path),
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, KeyError, ValueError, RuntimeError, TimeoutError, subprocess.CalledProcessError) as exc:
        print(f"WAN-230 live acceptance failed: {exc}", file=sys.stderr)
        return 1
    finally:
        for node in reversed(down):
            _exec(args.docker, node, "ip", "link", "set", "dev", "xfrm0", "up", check=False)


if __name__ == "__main__":
    raise SystemExit(main())
