#!/usr/bin/env python3
"""Measure LACP member and RSTP active-path interruption on the live lab.

Run only while holding the shared runtime lock, after topology deployment and
``python config/switching/apply.py``. The procedure temporarily assigns an
unused test address to a client data interface and removes it during cleanup.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from config.switching.apply import build_plan, load_inventory  # noqa: E402

PING_INTERVAL = 0.05


def _docker(docker: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([docker, *args], text=True, capture_output=True, check=check)


def _exec(docker: str, container: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _docker(docker, "exec", container, *args, check=check)


def _replies(stdout: Any, values: list[float], lock: threading.Lock) -> None:
    for line in stdout:
        if " bytes from " in line:
            with lock:
                values.append(time.monotonic())


def _wait_for_reply(
    values: list[float], lock: threading.Lock, after: float, timeout: float
) -> float:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with lock:
            found = next((value for value in values if value >= after), None)
        if found is not None:
            return found
        time.sleep(0.01)
    raise TimeoutError(f"no ping reply returned within {timeout:.2f}s")


def _wait_for_baseline(values: list[float], lock: threading.Lock) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with lock:
            if len(values) >= 10:
                return
        time.sleep(0.02)
    raise TimeoutError("baseline ping did not produce 10 replies within 5 seconds")


def verify_vlan_isolation(docker: str, site: str) -> None:
    """Prove same-subnet guest-to-user frames do not cross access VLANs."""
    guest = f"clab-netlab-phase-1-{site}-client-guest-1"
    user = f"clab-netlab-phase-1-{site}-client-users-1"
    configured: list[tuple[str, str]] = []
    try:
        for container, address in ((guest, "198.18.0.1/24"), (user, "198.18.0.2/24")):
            existing = _exec(docker, container, "ip", "-o", "-4", "addr", "show", "dev", "eth1")
            if existing.stdout.strip():
                raise RuntimeError(f"{container} eth1 already has IPv4 configuration: {existing.stdout.strip()}")
            _exec(docker, container, "ip", "addr", "add", address, "dev", "eth1")
            configured.append((container, address))
            _exec(docker, container, "ip", "link", "set", "eth1", "up")
        result = _exec(docker, guest, "ping", "-n", "-c", "3", "-W", "1", "198.18.0.2", check=False)
        if result.returncode == 0 or "100% packet loss" not in result.stdout:
            raise RuntimeError(
                f"guest-to-user VLAN isolation failed on {site}: "
                f"exit={result.returncode}; output={result.stdout.strip()}"
            )
        print(f"{site} guest VLAN 30 to users VLAN 10: denied (100% packet loss)")
    finally:
        for container, address in reversed(configured):
            _exec(docker, container, "ip", "addr", "del", address, "dev", "eth1", check=False)


def measure_case(
    docker: str,
    site: str,
    access: str,
    dist1: str,
    client: str,
    access_members: list[str],
    test_vlan: int,
    test_prefix: str,
    failure_mode: str,
    limit_seconds: float,
) -> dict[str, Any]:
    probe = f"{test_prefix}.253"
    client_ip = f"{test_prefix}.254/24"
    access_container = f"clab-netlab-phase-1-{access}"
    dist_container = f"clab-netlab-phase-1-{dist1}"
    client_container = f"clab-netlab-phase-1-{client}"
    probe_created = False
    client_configured = False
    lowered: list[str] = []
    process: subprocess.Popen[str] | None = None
    values: list[float] = []
    lock = threading.Lock()
    try:
        existing = _exec(docker, client_container, "ip", "-o", "-4", "addr", "show", "dev", "eth1")
        if existing.stdout.strip():
            raise RuntimeError(f"{client} eth1 already has IPv4 configuration: {existing.stdout.strip()}")
        _exec(
            docker, dist_container, "ovs-vsctl", "--may-exist", "add-port", "netlab-br0", "l2probe",
            "--", "set", "Interface", "l2probe", "type=internal", "--", "set", "Port", "l2probe",
            "vlan_mode=access", f"tag={test_vlan}", "other_config:rstp-port-admin-edge=true",
        )
        probe_created = True
        _exec(docker, dist_container, "ip", "link", "set", "l2probe", "up")
        _exec(docker, dist_container, "ip", "addr", "add", f"{probe}/24", "dev", "l2probe")
        _exec(docker, client_container, "ip", "addr", "add", client_ip, "dev", "eth1")
        client_configured = True
        _exec(docker, client_container, "ip", "link", "set", "eth1", "up")

        process = subprocess.Popen(
            [docker, "exec", client_container, "ping", "-n", "-D", "-O", "-i", str(PING_INTERVAL), "-w", "15", probe],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        reader = threading.Thread(target=_replies, args=(process.stdout, values, lock), daemon=True)
        reader.start()
        _wait_for_baseline(values, lock)

        members = access_members if failure_mode == "rstp" else access_members[:1]
        failed_at: float | None = None
        for index, member in enumerate(members):
            if index == len(members) - 1:
                # The last member down is when the direct path actually fails.
                failed_at = time.monotonic()
            _exec(docker, access_container, "ip", "link", "set", "dev", member, "down")
            lowered.append(member)
        assert failed_at is not None
        first_reply = _wait_for_reply(values, lock, failed_at, limit_seconds)
        interruption = max(0.0, first_reply - failed_at)
        # Prove the alternate path or surviving LACP member carries traffic
        # while the injected failure is still present.
        _wait_for_reply(values, lock, first_reply + 0.9, 0.5)
        if interruption > limit_seconds:
            raise RuntimeError(f"{failure_mode} interruption {interruption:.3f}s exceeds {limit_seconds:.3f}s")
        print(f"{site} {failure_mode}: interruption={interruption:.3f}s limit={limit_seconds:.3f}s recovered_while_failed=true")
        return {"site": site, "failure_mode": failure_mode, "interruption_seconds": interruption, "limit_seconds": limit_seconds}
    finally:
        for member in lowered:
            _exec(docker, access_container, "ip", "link", "set", "dev", member, "up", check=False)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if client_configured:
            _exec(docker, client_container, "ip", "addr", "del", client_ip, "dev", "eth1", check=False)
        if probe_created:
            _exec(docker, dist_container, "ovs-vsctl", "--if-exists", "del-port", "netlab-br0", "l2probe", check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=ROOT / "inventory" / "inventory.yaml")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--site", choices=("hq", "br1", "all"), default="all")
    args = parser.parse_args()
    try:
        data = load_inventory(args.inventory)
        plan = build_plan(data)
        bundles = [item for item in plan if item.get("bundle")]
        results = []
        for site in (("hq", "br1") if args.site == "all" else (args.site,)):
            dist1 = f"{site}-dist-1"
            access = f"{site}-access-1"
            client = f"{site}-client-users-1"
            uplink = f"{site}-access-1-dist-1"
            access_members = next(
                item["members"] for item in bundles
                if item["node"] == access and item["bundle"] == uplink
            )
            vlan = next(item for item in data["vlans"] if item["id"] == f"{site}-users")
            prefix = vlan["prefix"].split(".")
            test_prefix = ".".join(prefix[:3])
            vlan_id = int(vlan["vlan_id"])
            verify_vlan_isolation(args.docker, site)
            results.append(measure_case(
                args.docker, site, access, dist1, client, access_members, vlan_id, test_prefix,
                "lacp", 1.0,
            ))
            results.append(measure_case(
                args.docker, site, access, dist1, client, access_members, vlan_id, test_prefix,
                "rstp", 5.0,
            ))
        print(json.dumps({"results": results}, indent=2))
    except (OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError, TimeoutError, RuntimeError) as exc:
        print(f"L2 failure measurement failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
