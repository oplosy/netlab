#!/usr/bin/env python3
"""Live EDGE-410 acceptance: no bypass, zone permits/denies, and fail-closed.

Requires the running project lab with gateway, OSPF, IPsec, security, and
service configuration applied. Writes a JSON result to --output.
"""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
LAB = "netlab-phase-1"
FIREWALL = "hq-fw-1"
SIM_DNS = "203.0.113.10"
REMOTE_USERS_VIP = "10.20.10.1"
HQ_USERS_VIP = "10.10.10.1"

# Probes run inside network-node containers, which ship python3 but no ping.
# The source address is bound explicitly so each probe belongs to one zone.
PROBE = r"""
import socket, struct, sys, time
kind, source, target, port, timeout = sys.argv[1:6]
timeout = float(timeout)
def checksum(data):
    if len(data) % 2: data += b"\0"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total >> 16) + (total & 0xFFFF); total += total >> 16
    return ~total & 0xFFFF
ok = False
try:
    if kind == "icmp":
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
        s.bind((source, 0)); s.settimeout(timeout)
        header = struct.pack("!BBHHH", 8, 0, 0, 0, 1)
        packet = struct.pack("!BBHHH", 8, 0, checksum(header + b"edge410"), 0, 1) + b"edge410"
        s.sendto(packet, (target, 0)); s.recvfrom(1024); ok = True
    elif kind == "dns":
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((source, 0)); s.settimeout(timeout)
        query = b"\x41\x10\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + b"\x00\x00\x02\x00\x01"
        s.sendto(query, (target, int(port))); s.recvfrom(1024); ok = True
    elif kind == "tcp":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((source, 0)); s.settimeout(timeout)
        s.connect((target, int(port))); ok = True
except OSError:
    ok = False
print("reply" if ok else "no-reply")
"""


def run(*args: str, check: bool = True, timeout: int = 60) -> str:
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )
    if check and result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"{result.stderr or result.stdout}"
        )
    return result.stdout


def container(node_id: str) -> str:
    return f"clab-{LAB}-{node_id}"


def dexec(node_id: str, *args: str, check: bool = True) -> str:
    return run("docker", "exec", container(node_id), *args, check=check)


def probe(node_id: str, kind: str, source: str, target: str, port: int = 0) -> bool:
    output = dexec(
        node_id, "python3", "-c", PROBE, kind, source, target, str(port), "1.5"
    )
    return output.strip() == "reply"


def route(node_id: str, destination: str) -> str:
    return dexec(node_id, "ip", "-4", "route", "get", destination, check=False)


def forward_counters() -> dict[str, int]:
    """Packet counters of the firewall forward chain keyed by rule text."""
    output = dexec(FIREWALL, "nft", "list", "chain", "inet", "netlab_sec170", "forward")
    counters: dict[str, int] = {}
    for line in output.splitlines():
        match = re.search(r"counter packets (\d+) bytes \d+", line)
        if match:
            key = re.sub(r"counter packets \d+ bytes \d+", "counter", line.strip())
            counters[key] = int(match.group(1))
    return counters


def deny_count() -> int:
    return forward_counters()["counter drop"]


def wait(predicate, timeout: float, interval: float = 0.25) -> float | None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if predicate():
            return round(time.monotonic() - started, 3)
        time.sleep(interval)
    return None


def load_inventory() -> dict[str, Any]:
    return json.loads((ROOT / "inventory" / "inventory.yaml").read_text("utf-8"))


def link_address(data: dict[str, Any], node: str, peer: str) -> str:
    link = next(
        item
        for item in data["links"]
        if {endpoint["node"] for endpoint in item["endpoints"]} == {node, peer}
    )
    endpoint = next(item for item in link["endpoints"] if item["node"] == node)
    return str(ipaddress.ip_interface(endpoint["address"]).ip)


def check_no_bypass(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dist in ("hq-dist-1", "hq-dist-2"):
        via = link_address(data, FIREWALL, dist)
        for destination in (REMOTE_USERS_VIP, SIM_DNS):
            text = route(dist, destination)
            assert f"via {via} " in text, f"{dist} -> {destination} bypasses {FIREWALL}: {text}"
            result[f"{dist}->{destination}"] = via
    for edge in ("hq-edge-1", "hq-edge-2"):
        via = link_address(data, FIREWALL, edge)
        text = route(edge, HQ_USERS_VIP)
        assert f"via {via} " in text, f"{edge} -> HQ users bypasses {FIREWALL}: {text}"
        result[f"{edge}->{HQ_USERS_VIP}"] = via
    return result


def _sec170_acceptance() -> Any:
    """Reuse SEC-170's client helpers for real DHCP-leased endpoint traffic."""
    spec = importlib.util.spec_from_file_location(
        "sec170_acceptance", ROOT / "tests" / "security" / "acceptance.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_corporate_icmp() -> list[dict[str, Any]]:
    """Inter-site corporate ICMP is permitted through the firewall both ways.

    Distribution routers accept ICMP to their own addresses only from VLAN
    interfaces, so the permitted cases use real HQ and Branch 1 clients.
    """
    sec170 = _sec170_acceptance()
    image = os.environ.get("NETLAB_SERVICE_IMAGE", "netlab/service:0.1.0")
    results = []
    for source_site, destination_site in (("hq", "br1"), ("br1", "hq")):
        before = forward_counters()
        evidence = sec170.corporate_icmp(source_site, destination_site, image)
        after = forward_counters()
        accepted = sum(
            after[rule] - before.get(rule, 0)
            for rule in after
            # Permitted verdicts queue to the inline IPS (ADR 0018); nft lists
            # the rendered "queue num 0" as "queue to 0".
            if "ip protocol icmp" in rule and rule.endswith("counter queue to 0")
        )
        denied = after["counter drop"] - before["counter drop"]
        assert accepted > 0, f"{source_site}->{destination_site} bypassed {FIREWALL}"
        assert denied == 0, f"{FIREWALL} dropped permitted corporate ICMP"
        results.append(
            {**evidence, "firewall_accepts": accepted, "firewall_denies": denied}
        )
    return results


def check_zone_policy(data: dict[str, Any]) -> list[dict[str, Any]]:
    edge_source = link_address(data, "hq-edge-1", FIREWALL)
    results = []
    # Permitted: guest DNS from a real guest client (see guest_dns).
    before = deny_count()
    replied = guest_dns(lease=True)
    denied = deny_count() - before
    assert replied, "guest DNS to simulated Internet: no answer"
    assert denied == 0, f"guest DNS: {FIREWALL} dropped permitted traffic"
    results.append(
        {"case": "guest DNS to simulated Internet", "reply": True,
         "firewall_denies": 0, "pass": True}
    )
    # Denied cases may originate on routers: they stop at the firewall, so
    # the return path does not matter.
    cases = [
        # name, node, kind, source, target, port, expect reply, expect fw deny
        ("outside ICMP to service zone", "hq-edge-1", "icmp", edge_source, "10.10.20.2", 0, False, True),
        ("outside ICMP to management zone", "hq-edge-1", "icmp", edge_source, "10.10.99.2", 0, False, True),
        ("inside TCP/80 to simulated Internet", "hq-dist-1", "tcp", "10.10.10.2", SIM_DNS, 80, False, True),
        ("guest ICMP to remote corporate users", "hq-dist-1", "icmp", "10.10.30.2", REMOTE_USERS_VIP, 0, False, True),
        ("management ICMP to remote users", "hq-dist-1", "icmp", "10.10.99.2", REMOTE_USERS_VIP, 0, False, True),
    ]
    for name, node, kind, source, target, port, want_reply, want_deny in cases:
        before = deny_count()
        replied = probe(node, kind, source, target, port)
        denied = deny_count() - before
        assert replied == want_reply, f"{name}: reply={replied}, expected {want_reply}"
        if want_deny:
            assert denied > 0, f"{name}: {FIREWALL} deny counter did not move"
        else:
            assert denied == 0, f"{name}: {FIREWALL} dropped permitted traffic"
        results.append(
            {"case": name, "reply": replied, "firewall_denies": denied, "pass": True}
        )
    return results


GUEST_CLIENT = "hq-client-guest-1"


def guest_dns(lease: bool = False) -> bool:
    """Guest DNS from a DHCP-leased HQ guest client returns the test answer.

    A distribution SVI source is not usable for permitted flows: hq-fw-1
    reaches the guest subnet over ECMP and the reply may land on the other
    distribution router, which has no conntrack state for it.
    """
    sec170 = _sec170_acceptance()
    image = os.environ.get("NETLAB_SERVICE_IMAGE", "netlab/service:0.1.0")
    if lease:
        sec170.lease(GUEST_CLIENT, image)
    try:
        output = sec170.helper(
            GUEST_CLIENT,
            f"dig +time=2 +tries=1 @{SIM_DNS} www.internet.test A +short",
            image,
        )
    except RuntimeError:  # dig exits non-zero when no answer arrives
        return False
    return "203.0.113.20" in output.split()


def wan_reachable() -> bool:
    """Guest DNS through the firewall, IPS, edge NAT, and ISP answers."""
    return guest_dns()


def branch_reaches_hq_clients() -> bool:
    sec170 = _sec170_acceptance()
    image = os.environ.get("NETLAB_SERVICE_IMAGE", "netlab/service:0.1.0")
    try:
        sec170.corporate_icmp("br1", "hq", image)
    except RuntimeError:
        return False
    return True


def check_fail_closed() -> dict[str, Any]:
    assert wan_reachable(), "baseline failed"
    run("docker", "pause", container(FIREWALL))
    try:
        blocked = wait(lambda: not wan_reachable(), timeout=20)
        assert blocked is not None, "HQ kept a WAN path while the firewall was down"
        # After BFD/OSPF drop the firewall adjacencies, no alternate path may appear.
        route_withdrawn = wait(
            lambda: "10.10.252." not in route("hq-dist-1", SIM_DNS), timeout=20
        )
        assert route_withdrawn is not None, "HQ distribution kept the firewall route"
        time.sleep(2)
        remaining = route("hq-dist-1", SIM_DNS)
        assert " via " not in remaining, f"alternate WAN path appeared: {remaining}"
        outbound_blocked = not wan_reachable()
        inbound_blocked = not branch_reaches_hq_clients()
        intra_site = probe("hq-dist-1", "icmp", "10.10.10.2", "10.10.10.3")
        assert outbound_blocked, "outbound traffic crossed a paused firewall"
        assert inbound_blocked, "Branch 1 reached HQ clients through a paused firewall"
        assert intra_site, "intra-HQ traffic must not depend on the firewall"
    finally:
        run("docker", "unpause", container(FIREWALL))
    recovered = wait(wan_reachable, timeout=60, interval=1)
    assert recovered is not None, "HQ WAN path did not recover after firewall resume"
    assert branch_reaches_hq_clients(), "Branch 1 to HQ did not recover"
    return {
        "wan_traffic_blocked_seconds": blocked,
        "firewall_route_withdrawn_seconds": route_withdrawn,
        "outbound_blocked": outbound_blocked,
        "inbound_blocked": inbound_blocked,
        "intra_site_continues": intra_site,
        "recovery_seconds": recovered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence" / "specs" / "secure-edge" / "edge410-latest.json",
    )
    args = parser.parse_args()
    data = load_inventory()
    result = {
        "source": "tests/security/secure_edge/acceptance.py",
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "no_bypass_routes": check_no_bypass(data),
        "permitted_corporate_icmp": check_corporate_icmp(),
        "zone_policy": check_zone_policy(data),
        "fail_closed": check_fail_closed(),
    }
    result["result"] = "PASS"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
