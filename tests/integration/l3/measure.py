#!/usr/bin/env python3
"""Live L3 gateway, VRRP failover, routed-link, and guest-deny acceptance."""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from config.gateway.apply import LAB_NAME, build_plan, load_inventory


def _exec(
    docker: str, container: str, *argv: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([docker, "exec", container, *argv], text=True, capture_output=True, check=False)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{container} {' '.join(argv)} failed: {detail}")
    return result


def _vip_present(docker: str, container: str, interface: str, address: str) -> bool:
    result = _exec(docker, container, "ip", "-o", "-4", "address", "show", "dev", interface)
    return any(f"inet {address}/" in line for line in result.stdout.splitlines())


def _wait_vip_owner(
    docker: str, primary: dict[str, Any], backup: dict[str, Any], expected: dict[str, Any]
) -> None:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        backup_owns_all = all(
            _vip_present(docker, backup["container"], gateway["interface"], gateway["virtual_ip"].split("/")[0])
            for gateway in expected["gateways"]
        )
        primary_owns_none = all(
            not _vip_present(docker, primary["container"], gateway["interface"], gateway["virtual_ip"].split("/")[0])
            for gateway in expected["gateways"]
        )
        if backup_owns_all and primary_owns_none:
            return
        time.sleep(0.25)
    raise TimeoutError(f"{expected['site']} VRRP VIP ownership did not converge")


def _wait_preferred_owner(docker: str, primary: dict[str, Any], backup: dict[str, Any]) -> None:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        primary_owns_all = all(
            _vip_present(docker, primary["container"], gateway["interface"], gateway["virtual_ip"].split("/")[0])
            for gateway in primary["gateways"]
        )
        backup_owns_none = all(
            not _vip_present(docker, backup["container"], gateway["interface"], gateway["virtual_ip"].split("/")[0])
            for gateway in backup["gateways"]
        )
        if primary_owns_all and backup_owns_none:
            return
        time.sleep(0.25)
    primary_addresses = {
        gateway["interface"]: _exec(
            docker, primary["container"], "ip", "-o", "-4", "address", "show", "dev", gateway["interface"]
        ).stdout.strip()
        for gateway in primary["gateways"]
    }
    backup_addresses = {
        gateway["interface"]: _exec(
            docker, backup["container"], "ip", "-o", "-4", "address", "show", "dev", gateway["interface"]
        ).stdout.strip()
        for gateway in backup["gateways"]
    }
    neighbors = {
        "primary": _exec(docker, primary["container"], "ip", "neigh", "show", check=False).stdout.strip(),
        "backup": _exec(docker, backup["container"], "ip", "neigh", "show", check=False).stdout.strip(),
    }
    rstp = {
        "primary": _exec(
            docker, primary["container"], "ovs-appctl", "rstp/show", "netlab-br0", check=False
        ).stdout.strip(),
        "backup": _exec(
            docker, backup["container"], "ovs-appctl", "rstp/show", "netlab-br0", check=False
        ).stdout.strip(),
    }
    log = _exec(docker, primary["container"], "tail", "-12", "/var/log/netlab/keepalived.log", check=False).stdout.strip()
    backup_log = _exec(docker, backup["container"], "tail", "-12", "/var/log/netlab/keepalived.log", check=False).stdout.strip()
    raise TimeoutError(
        f"{primary['site']} preferred VRRP owner did not recover; "
        f"primary_addresses={primary_addresses}; backup_addresses={backup_addresses}; "
        f"neighbors={neighbors}; rstp={rstp}; primary_log={log}; backup_log={backup_log}"
    )


def _ping(docker: str, container: str, address: str, *, expected: bool) -> str:
    result = _exec(docker, container, "ping", "-n", "-c", "3", "-W", "1", address, check=False)
    if expected and result.returncode:
        raise RuntimeError(f"{container} could not reach {address}: {result.stdout}{result.stderr}")
    if not expected:
        output = result.stdout + result.stderr
        if result.returncode == 0 or "100% packet loss" not in output:
            raise RuntimeError(f"denied traffic unexpectedly reached {address}: {output}")
    return result.stdout.strip()


def _route_to_vip(docker: str, client: dict[str, Any], address: str, vip: str) -> tuple[list[str], str]:
    container = f"clab-{LAB_NAME}-{client['id']}"
    existing = _exec(docker, container, "ip", "-o", "-4", "address", "show", "dev", "eth1")
    if existing.stdout.strip():
        raise RuntimeError(f"{container} eth1 already has IPv4 configuration: {existing.stdout.strip()}")
    original_defaults = _exec(docker, container, "ip", "-4", "route", "show", "default").stdout.splitlines()
    _exec(docker, container, "ip", "link", "set", "dev", "eth1", "up")
    _exec(docker, container, "ip", "address", "add", address, "dev", "eth1")
    _exec(docker, container, "ip", "route", "replace", "default", "via", vip, "dev", "eth1")
    return original_defaults, address


def _restore_client(docker: str, container: str, address: str, defaults: list[str]) -> None:
    _exec(docker, container, "ip", "route", "del", "default", "dev", "eth1", check=False)
    _exec(docker, container, "ip", "address", "del", address, "dev", "eth1", check=False)
    for route in defaults:
        _exec(docker, container, "ip", "-4", "route", "replace", *route.split(), check=False)


def _check_routed_links(docker: str, plan: dict[str, Any], data: dict[str, Any]) -> None:
    nodes = {node["id"]: node for node in data["nodes"]}
    for item in plan["nodes"]:
        for routed in item["routed_interfaces"]:
            local = ipaddress.ip_interface(routed["address"])
            link = next(
                link for link in data["links"]
                if link.get("kind") == "routed"
                and any(endpoint["node"] == item["node"] and endpoint["interface"] == routed["interface_name"] for endpoint in link["endpoints"])
            )
            peer_endpoint = next(endpoint for endpoint in link["endpoints"] if endpoint["node"] != item["node"])
            peer_node = nodes[peer_endpoint["node"]]
            physical_kinds = {"routed", "ebgp", "l2", "access"}
            peer_index = 1
            for interface in peer_node["interfaces"]:
                if interface["kind"] in physical_kinds:
                    if interface["name"] == peer_endpoint["interface"]:
                        break
                    peer_index += 1
            peer_device = f"eth{peer_index}"
            peer = ipaddress.ip_interface(peer_endpoint["address"])
            local_container = item["container"]
            peer_container = f"clab-{LAB_NAME}-{peer_node['id']}"
            local_addresses = _exec(docker, local_container, "ip", "-o", "-4", "address", "show", "dev", routed["interface"]).stdout
            peer_addresses = _exec(docker, peer_container, "ip", "-o", "-4", "address", "show", "dev", peer_device).stdout
            if str(local) not in local_addresses or str(peer) not in peer_addresses:
                raise RuntimeError(f"routed link addresses are missing on {item['node']} or {peer_node['id']}")
            route = _exec(docker, local_container, "ip", "route", "get", str(peer.ip)).stdout
            if f"dev {routed['interface']}" not in route or f"src {local.ip}" not in route:
                raise RuntimeError(f"{item['node']} route lookup for {peer.ip} did not use its routed edge link")
            print(f"{item['node']} routed /31 {local} <-> {peer} configured and selected by the route table")

def verify_site(docker: str, site_id: str, data: dict[str, Any], plan: dict[str, Any]) -> None:
    planned = [item for item in plan["nodes"] if item["site"] == site_id]
    primary = next(item for item in planned if item["node"].endswith("dist-1"))
    backup = next(item for item in planned if item["node"].endswith("dist-2"))
    users = next(node for node in data["nodes"] if node.get("site") == site_id and node["id"].endswith("client-users-1"))
    guest = next(node for node in data["nodes"] if node.get("site") == site_id and node["id"].endswith("client-guest-1"))
    server = next(node for node in data["nodes"] if node.get("site") == site_id and node.get("role") == "server")
    user_vlan = next(vlan for vlan in data["vlans"] if vlan["site"] == site_id and vlan["vlan_id"] == 10)
    guest_vlan = next(vlan for vlan in data["vlans"] if vlan["site"] == site_id and vlan["vlan_id"] == 30)
    server_vlan = next(vlan for vlan in data["vlans"] if vlan["site"] == site_id and vlan["vlan_id"] == 20)
    user_address = f"{ipaddress.ip_network(user_vlan['prefix']).network_address + 100}/24"
    guest_address = f"{ipaddress.ip_network(guest_vlan['prefix']).network_address + 100}/24"
    server_address = f"{ipaddress.ip_network(server_vlan['prefix']).network_address + 100}/24"
    vip = user_vlan["gateway"]

    tracked: list[tuple[str, str, list[str]]] = []
    try:
        for node, address, gateway in (
            (users, user_address, user_vlan["gateway"]),
            (server, server_address, server_vlan["gateway"]),
            (guest, guest_address, guest_vlan["gateway"]),
        ):
            defaults, configured = _route_to_vip(docker, node, address, gateway)
            tracked.append((f"clab-{LAB_NAME}-{node['id']}", configured, defaults))

        user_container = f"clab-{LAB_NAME}-{users['id']}"
        for address in user_vlan["distribution_addresses"]:
            _ping(docker, user_container, address, expected=True)
        print(f"{site_id} user VLAN client can reach both distribution SVI addresses")
        _ping(
            docker,
            f"clab-{LAB_NAME}-{guest['id']}",
            user_address.split("/")[0],
            expected=False,
        )
        print(f"{site_id} guest VLAN 30 to user VLAN 10: denied (100% packet loss)")

        _wait_preferred_owner(docker, primary, backup)
        _ping(docker, f"clab-{LAB_NAME}-{users['id']}", vip, expected=True)
        _ping(docker, f"clab-{LAB_NAME}-{users['id']}", server_address.split("/")[0], expected=True)
        print(f"{site_id} user client uses VRRP VIP {vip} and reaches the server VLAN")

        _exec(docker, primary["container"], "pkill", "-TERM", "-o", "-x", "keepalived")
        _wait_vip_owner(docker, primary, backup, primary)
        for container, _, _ in tracked:
            _exec(docker, container, "ip", "neigh", "flush", "dev", "eth1", check=False)
        _ping(docker, f"clab-{LAB_NAME}-{users['id']}", server_address.split("/")[0], expected=True)
        print(f"{site_id} VRRP failover: distribution 2 owns all four VIPs and forwards client traffic")

        print(f"{site_id} VRRP failover verified; standby remains active until teardown")
    finally:
        for container, address, defaults in reversed(tracked):
            _restore_client(docker, container, address, defaults)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=ROOT / "inventory" / "inventory.yaml")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--site", choices=("hq", "br1", "all"), default="all")
    args = parser.parse_args()
    try:
        data = load_inventory(args.inventory)
        plan = build_plan(data, LAB_NAME)
        _check_routed_links(args.docker, plan, data)
        sites = sorted({site["id"] for site in data["sites"]}) if args.site == "all" else [args.site]
        for site in sites:
            verify_site(args.docker, site, data, plan)
        print(json.dumps({"sites": sites, "gateway": "pass", "failover": "pass", "guest_deny": "pass"}, indent=2))
    except (OSError, ValueError, KeyError, StopIteration, subprocess.CalledProcessError, TimeoutError, RuntimeError) as exc:
        print(f"L3 gateway acceptance failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
