#!/usr/bin/env python3
"""Verify Branch 2 Area 0 summary, bidirectional routing, and isolation live."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = "netlab-phase-1"


def run(node: str, *command: str) -> str:
    result = subprocess.run(
        ["docker", "exec", f"clab-{LAB}-{node}", *command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"{node} {' '.join(command)} failed: {result.stderr or result.stdout}"
        )
    return result.stdout


def route(node: str, prefix: str, interface: str) -> str:
    output = run(node, "vtysh", "-c", f"show ip route {prefix}")
    entries = re.findall(rf"^Routing entry for {re.escape(prefix)}$", output, re.M)
    if (
        len(entries) != 1
        or 'Known via "ospf"' not in output
        or not re.search(rf"\bvia {re.escape(interface)},", output)
    ):
        raise RuntimeError(
            f"expected one OSPF summary route {prefix} via {interface} on {node}; got {output}"
        )
    return next(line.strip() for line in output.splitlines() if "via " in line)


def main() -> int:
    inventory = json.loads(
        (ROOT / "inventory" / "inventory.yaml").read_text(encoding="utf-8")
    )
    br2 = next(site["aggregate"] for site in inventory["sites"] if site["id"] == "br2")
    hq = next(site["aggregate"] for site in inventory["sites"] if site["id"] == "hq")
    sa = run("hq-edge-1", "swanctl", "--list-sas")
    if "site-overlay-hq_br2_xfrm" not in sa:
        run("hq-edge-1", "swanctl", "--initiate", "--child", "site-overlay-hq_br2_xfrm")
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        routes = run("hq-edge-1", "vtysh", "-c", f"show ip route {br2}")
        if "xfrm1" in routes:
            break
        time.sleep(1)
    hq_route = route("hq-edge-1", br2, "xfrm1")
    br2_route = route("br2-edge-1", hq, "xfrm0")
    if "10.255.0.5" not in run("hq-edge-1", "vtysh", "-c", "show ip ospf neighbor"):
        raise RuntimeError("HQ has no Branch 2 XFRM OSPF neighbor")
    if "10.255.0.4" not in run("br2-edge-1", "vtysh", "-c", "show ip ospf neighbor"):
        raise RuntimeError("Branch 2 has no HQ XFRM OSPF neighbor")
    for script in (
        "tests/security/acceptance.py",
        "tests/integration/services/acceptance.py",
    ):
        result = subprocess.run(
            [sys.executable, script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=1200,
            check=False,
        )
        print(result.stdout, end="")
        if result.returncode:
            raise RuntimeError(
                f"live acceptance failed: {script}\n{result.stderr or result.stdout}"
            )
    print(
        json.dumps(
            {
                "result": "PASS",
                "branch2_summary_area0_route_count": 1,
                "hq_to_branch2": hq_route,
                "branch2_to_hq": br2_route,
                "guest_and_oob_isolation": "live negative probes passed on hq, br1, br2",
                "branch1_regression": "DHCP, DNS, NTP, corporate ICMP, and guest-deny probes passed",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired,
        KeyError,
        ValueError,
    ) as exc:
        print(f"SITE-330 live acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
