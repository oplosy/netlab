#!/usr/bin/env python3
"""Verify bidirectional ARP adjacency between distribution bridges on each VLAN."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from config.switching.apply import LAB_NAME, build_plan, load_inventory

BRIDGE = "netlab-br0"
VLANS = (10, 20, 30, 99)
ARP_PROBE = r'''
import socket, subprocess, sys, time
iface, source_ip, target_ip = sys.argv[1:]
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((source_ip, 0))
deadline = time.monotonic() + 4
while time.monotonic() < deadline:
    try:
        sock.sendto(b"netlab-arp-probe", (target_ip, 9))
    except OSError:
        pass
    neighbor = subprocess.run(
        ["ip", "-4", "neigh", "show", "to", target_ip, "dev", iface],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    if " lladdr " in neighbor and " INCOMPLETE " not in neighbor and " FAILED " not in neighbor:
        print(neighbor)
        sys.exit(0)
    time.sleep(0.1)
print(f"no resolved neighbor for {target_ip} on {iface}", file=sys.stderr)
sys.exit(1)
'''


def _exec(docker: str, container: str, *argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [docker, "exec", container, *argv], text=True, capture_output=True, check=False
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{container} {' '.join(argv)} failed: {detail}")
    return result


def _container(site: str, distribution: int) -> str:
    return f"clab-{LAB_NAME}-{site}-dist-{distribution}"


def _remove_probe(docker: str, container: str, ovs_port: str) -> None:
    _exec(docker, container, "ovs-vsctl", "--if-exists", "del-port", BRIDGE, ovs_port, check=False)
    _exec(docker, container, "ip", "link", "del", "dev", ovs_port, check=False)


def _add_probe(docker: str, container: str, vlan: int, address: str) -> tuple[str, str]:
    ovs_port = f"peer{vlan}"
    host_interface = f"probe{vlan}"
    # OVS internal Ports do not participate in RSTP. Use a veth system Port so
    # the probe is an edge Port that reaches Forwarding before it sends ARP.
    _remove_probe(docker, container, ovs_port)
    try:
        _exec(
            docker, container, "ip", "link", "add", ovs_port,
            "type", "veth", "peer", "name", host_interface,
        )
        _exec(
            docker,
            container,
            "ovs-vsctl", "add-port", BRIDGE, ovs_port,
            "--", "set", "Port", ovs_port,
            "vlan_mode=access", f"tag={vlan}",
            "other_config:rstp-enable=true",
            "other_config:rstp-port-admin-edge=true",
        )
        _exec(docker, container, "ip", "link", "set", "dev", ovs_port, "up")
        _exec(docker, container, "ip", "link", "set", "dev", host_interface, "up")
        _exec(docker, container, "ip", "address", "replace", address, "dev", host_interface)
    except RuntimeError:
        _remove_probe(docker, container, ovs_port)
        raise
    return ovs_port, host_interface


def _wait_forwarding(docker: str, container: str, port: str) -> None:
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        rstp = _exec(docker, container, "ovs-appctl", "rstp/show", BRIDGE, check=False).stdout
        line = next((line for line in rstp.splitlines() if line.strip().startswith(f"{port} ")), "")
        if "Forwarding" in line:
            return
        time.sleep(0.5)
    neighbors = _exec(docker, container, "ip", "neigh", "show", check=False).stdout.strip()
    rstp = _exec(docker, container, "ovs-appctl", "rstp/show", BRIDGE, check=False).stdout.strip()
    raise TimeoutError(f"{container} port {port} did not reach RSTP Forwarding; neighbors={neighbors}; rstp={rstp}")


def _arp_probe(
    docker: str,
    container: str,
    peer: str,
    interface: str,
    ovs_port: str,
    source: str,
    target: str,
) -> None:
    result = _exec(docker, container, "python3", "-c", ARP_PROBE, interface, source, target, check=False)
    if result.returncode:
        diagnostics = {}
        for node in (container, peer):
            diagnostics[node] = {
                "neighbors": _exec(docker, node, "ip", "neigh", "show", check=False).stdout.strip(),
                "probe_interface": _exec(
                    docker, node, "ip", "-s", "link", "show", "dev", interface, check=False
                ).stdout.strip(),
                "ovs_interface": _exec(
                    docker, node, "ovs-vsctl", "list", "interface", ovs_port, check=False
                ).stdout.strip(),
                "rstp": _exec(docker, node, "ovs-appctl", "rstp/show", BRIDGE, check=False).stdout.strip(),
                "switch_ports": _exec(docker, node, "ovs-ofctl", "show", BRIDGE, check=False).stdout.strip(),
                "port_counters": _exec(docker, node, "ovs-ofctl", "dump-ports", BRIDGE, check=False).stdout.strip(),
                "peer_bond": _exec(docker, node, "ovs-vsctl", "list", "port", "bond2", check=False).stdout.strip(),
                "port": _exec(docker, node, "ovs-vsctl", "list", "port", ovs_port, check=False).stdout.strip(),
                "mac_table": _exec(docker, node, "ovs-appctl", "fdb/show", BRIDGE, check=False).stdout.strip(),
            }
        raise RuntimeError(f"{container} {interface} ARP to {target} failed: {result.stderr.strip()}; {diagnostics}")


def verify_site(docker: str, site: str, plan: list[dict[str, Any]], bundles: list[dict[str, Any]]) -> None:
    left, right = _container(site, 1), _container(site, 2)
    peer_bundle = next(
        bundle["id"]
        for bundle in bundles
        if bundle.get("kind") == "distribution-peer" and set(bundle["peer_nodes"]) == {f"{site}-dist-1", f"{site}-dist-2"}
    )
    peer_ports = {
        item["node"]: item["argv"][4]
        for item in plan
        if item.get("bundle") == peer_bundle
    }
    created: list[tuple[str, str]] = []
    probes: dict[tuple[int, str], tuple[str, str]] = {}
    try:
        _wait_forwarding(docker, left, peer_ports[f"{site}-dist-1"])
        _wait_forwarding(docker, right, peer_ports[f"{site}-dist-2"])
        for vlan in VLANS:
            prefix = f"198.18.{vlan}"
            for container, host in ((left, 1), (right, 2)):
                ovs_port, interface = _add_probe(docker, container, vlan, f"{prefix}.{host}/24")
                created.append((container, ovs_port))
                _wait_forwarding(docker, container, ovs_port)
                probes[(vlan, container)] = (ovs_port, interface)
        for vlan in VLANS:
            prefix = f"198.18.{vlan}"
            left_port, left_interface = probes[(vlan, left)]
            right_port, right_interface = probes[(vlan, right)]
            _arp_probe(docker, left, right, left_interface, left_port, f"{prefix}.1", f"{prefix}.2")
            print(f"{site} VLAN {vlan} dist-1 -> dist-2: ARP resolved")
            _arp_probe(docker, right, left, right_interface, right_port, f"{prefix}.2", f"{prefix}.1")
            print(f"{site} VLAN {vlan} dist-2 -> dist-1: ARP resolved")
    finally:
        for container, ovs_port in reversed(created):
            _remove_probe(docker, container, ovs_port)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=ROOT / "inventory" / "inventory.yaml")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--site", choices=("hq", "br1", "all"), default="all")
    args = parser.parse_args()
    try:
        data = load_inventory(args.inventory)
        declared = {int(vlan["vlan_id"]) for vlan in data["vlans"]}
        if declared != set(VLANS):
            raise ValueError(f"expected site VLANs {VLANS}; inventory declares {sorted(declared)}")
        plan = build_plan(data, LAB_NAME)
        sites = sorted({site["id"] for site in data["sites"]}) if args.site == "all" else [args.site]
        for site in sites:
            verify_site(args.docker, site, plan, data["bundles"])
        print(json.dumps({"sites": sites, "vlans": list(VLANS), "peer_arp": "pass"}, indent=2))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, TimeoutError, RuntimeError) as exc:
        print(f"L2 peer adjacency failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
