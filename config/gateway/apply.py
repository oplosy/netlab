#!/usr/bin/env python3
"""Converge inventory-derived site gateways, VRRP, and routed distribution links."""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = ROOT / "inventory" / "inventory.yaml"
LAB_NAME = "netlab-phase-1"
BRIDGE = "netlab-br0"
PHYSICAL_KINDS = {"routed", "ebgp", "l2", "access"}


def load_inventory(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT))
    from scripts.validate.validate_inventory import validate_inventory

    errors = validate_inventory(data, ROOT / "schemas" / "inventory.schema.json")
    if errors:
        raise ValueError("invalid inventory:\n" + "\n".join(errors))
    return data


def _physical_interfaces(data: dict[str, Any]) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for node in data["nodes"]:
        index = 1
        for interface in node.get("interfaces", []):
            if interface.get("kind") in PHYSICAL_KINDS:
                result[(node["id"], interface["name"])] = f"eth{index}"
                index += 1
    return result


def _keepalived_config(node: dict[str, Any], gateways: list[dict[str, Any]]) -> str:
    site = node["site"].upper()
    suffix = node["id"].rsplit("-", 1)[-1]
    lines = ["! Generated from inventory; do not edit.", "global_defs {", f"  router_id {site}_{suffix}", "}", ""]
    for gateway in gateways:
        lines.extend([
            f"vrrp_instance VI_{gateway['vlan_id']} {{",
            f"  state {gateway['state']}",
            f"  interface {gateway['interface']}",
            f"  virtual_router_id {gateway['virtual_router_id']}",
            f"  priority {gateway['priority']}",
            f"  unicast_src_ip {gateway['unicast_src_ip']}",
            "  unicast_peer {",
            f"    {gateway['unicast_peer']}",
            "  }",
            "  check_unicast_src",
            "  advert_int 1",
            "  garp_master_delay 1",
            "  garp_master_repeat 3",
            "  virtual_ipaddress {",
            f"    {gateway['virtual_ip']} dev {gateway['interface']}",
            "  }",
            "}",
            "",
        ])
    return "\n".join(lines)


def _nftables_config(destinations: list[str], vlans: list[dict[str, Any]]) -> str:
    blocked = ", ".join(destinations)
    lines = [
        "table inet netlab_gateway {",
        "  chain input {",
        "    type filter hook input priority filter; policy accept;",
    ]
    for vlan in vlans:
        vlan_id = int(vlan["vlan_id"])
        peers = ", ".join(sorted(vlan["distribution_addresses"]))
        lines.append(
            f'    iifname "vlan{vlan_id}" ip saddr {{ {peers} }} ip protocol 112 accept'
        )
    lines.extend([
        f'    iifname "vlan30" ip daddr {{ {blocked} }} counter drop',
        "  }",
        "  chain forward {",
        "    type filter hook forward priority filter; policy accept;",
        f'    iifname "vlan30" ip daddr {{ {blocked} }} counter drop',
        "  }",
        "}",
        "",
    ])
    return "\n".join(lines)

def build_plan(data: dict[str, Any], lab_name: str = LAB_NAME) -> dict[str, Any]:
    """Build deterministic per-node L3 state from the validated source inventory."""
    sites = {site["id"]: site for site in data["sites"]}
    nodes = {node["id"]: node for node in data["nodes"]}
    physical = _physical_interfaces(data)
    vlans_by_site: dict[str, list[dict[str, Any]]] = {}
    for vlan in data["vlans"]:
        vlans_by_site.setdefault(vlan["site"], []).append(vlan)

    dhcp_services = [node for node in data["nodes"] if node.get("service") == "dhcp"]
    if len(dhcp_services) > 1:
        raise ValueError("at most one DHCP service node is supported")
    dhcp_server = None
    if dhcp_services and dhcp_services[0].get("service_address"):
        dhcp_server = str(ipaddress.ip_interface(dhcp_services[0]["service_address"]).ip)

    blocked_destinations = sorted(
        {str(ipaddress.ip_network(site["aggregate"])) for site in data["sites"]}
        | {
            str(ipaddress.ip_interface(node["oob"]).network)
            for node in data["nodes"]
            if node.get("oob")
        }
    )
    routed_endpoints: list[dict[str, Any]] = []
    for link in data["links"]:
        if link.get("kind") != "routed":
            continue
        for endpoint in link["endpoints"]:
            node = nodes[endpoint["node"]]
            if node.get("role") not in {"edge", "dist"}:
                raise ValueError(f"routed link {link['id']} must terminate on an edge and distribution node")
            interface = physical.get((node["id"], endpoint["interface"]))
            if interface is None or not endpoint.get("address"):
                raise ValueError(f"routed link {link['id']} has an incomplete endpoint")
            routed_endpoints.append({
                "node": node["id"],
                "container": f"clab-{lab_name}-{node['id']}",
                "interface": interface,
                "address": str(ipaddress.ip_interface(endpoint["address"])),
                "link": link["id"],
            })
    planned_nodes: list[dict[str, Any]] = []
    relay_hooks: list[dict[str, Any]] = []
    dist_nodes = sorted(
        (node for node in data["nodes"] if node.get("role") == "dist"),
        key=lambda node: node["id"],
    )
    for node in dist_nodes:
        site_id = node["site"]
        site_vlans = sorted(vlans_by_site.get(site_id, []), key=lambda vlan: int(vlan["vlan_id"]))
        nftables_config = _nftables_config(blocked_destinations, site_vlans)
        if len(site_vlans) != 4:
            raise ValueError(f"{site_id} must declare all four routed VLANs")
        siblings = sorted(
            (item for item in dist_nodes if item["site"] == site_id),
            key=lambda item: item["id"],
        )
        if len(siblings) != 2:
            raise ValueError(f"{site_id} must have exactly two distribution gateways")
        priority = 150 if node["id"] == siblings[0]["id"] else 100
        gateways = []
        for vlan in site_vlans:
            interface = f"vlan{int(vlan['vlan_id'])}"
            node_interface = next(
                (item for item in node.get("interfaces", []) if item["name"] == interface and item["kind"] == "svi"),
                None,
            )
            if node_interface is None or len(node_interface.get("addresses", [])) != 1:
                raise ValueError(f"{node['id']} is missing one inventory SVI address for {interface}")
            svi = ipaddress.ip_interface(node_interface["addresses"][0])
            gateway_ip = ipaddress.ip_address(vlan["gateway"])
            peer_ip = next(ipaddress.ip_address(value) for value in vlan["distribution_addresses"] if ipaddress.ip_address(value) != svi.ip)
            if svi.network != ipaddress.ip_network(vlan["prefix"]):
                raise ValueError(f"{node['id']} {interface} is outside VLAN {vlan['id']}")
            if gateway_ip in {svi.ip, *(ipaddress.ip_address(value) for value in vlan["distribution_addresses"])}:
                raise ValueError(f"VLAN {vlan['id']} gateway conflicts with a distribution address")
            entry = {
                "interface": interface,
                "vlan_id": int(vlan["vlan_id"]),
                "address": str(svi),
                "virtual_ip": f"{gateway_ip}/{svi.network.prefixlen}",
                "unicast_src_ip": str(svi.ip),
                "unicast_peer": str(peer_ip),
                "virtual_router_id": int(vlan["vlan_id"]),
                "priority": priority,
                "state": "MASTER" if priority == 150 else "BACKUP",
            }
            gateways.append(entry)
            relay_hooks.append({
                "site": site_id,
                "node": node["id"],
                "interface": interface,
                "vlan_id": entry["vlan_id"],
                "server": dhcp_server,
                "enabled": dhcp_server is not None,
            })

        routed = []
        for interface in node.get("interfaces", []):
            if interface.get("kind") != "routed":
                continue
            addresses = interface.get("addresses", [])
            if len(addresses) != 1:
                raise ValueError(f"{node['id']} routed interface {interface['name']} must have one address")
            endpoint = physical.get((node["id"], interface["name"]))
            if endpoint is None:
                raise ValueError(f"{node['id']} routed interface {interface['name']} has no physical mapping")
            routed.append({"interface": endpoint, "address": str(ipaddress.ip_interface(addresses[0]))})
        if len(routed) != 1:
            raise ValueError(f"{node['id']} must have exactly one routed edge link")

        planned_nodes.append({
            "node": node["id"],
            "site": site_id,
            "container": f"clab-{lab_name}-{node['id']}",
            "routed_interfaces": routed,
            "gateways": gateways,
            "keepalived_config": _keepalived_config(node, gateways),
            "nftables_config": nftables_config,
        })

    if len(planned_nodes) != len(sites) * 2:
        raise ValueError("every declared site must have exactly two distribution nodes")
    return {"nodes": planned_nodes, "routed_endpoints": routed_endpoints, "dhcp_relay_hooks": relay_hooks}


def _exec(docker: str, container: str, *argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([docker, "exec", container, *argv], text=True, capture_output=True)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{container} {' '.join(argv)}: {detail}")
    return result


def _copy_text(docker: str, container: str, destination: str, content: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False) as stream:
        stream.write(content)
        source = Path(stream.name)
    try:
        subprocess.run([docker, "cp", str(source), f"{container}:{destination}"], check=True)
    finally:
        source.unlink(missing_ok=True)


def _prepare_node(item: dict[str, Any], relay_hooks: list[dict[str, Any]], docker: str) -> None:
    container = item["container"]
    for routed in item["routed_interfaces"]:
        interface = routed["interface"]
        master = _exec(docker, container, "readlink", f"/sys/class/net/{interface}/master", check=False)
        if master.returncode == 0:
            raise ValueError(f"{container} {interface} is enslaved to {Path(master.stdout.strip()).name}")
    for routed in item["routed_interfaces"]:
        interface = routed["interface"]
        _exec(docker, container, "ip", "link", "set", "dev", interface, "up")
        _exec(docker, container, "ip", "address", "replace", routed["address"], "dev", interface)
    _exec(docker, container, "sysctl", "-w", "net.ipv4.ip_forward=1")
    for gateway in item["gateways"]:
        interface = gateway["interface"]
        _exec(
            docker, container, "ovs-vsctl", "--may-exist", "add-port", BRIDGE, interface,
            "--", "set", "Interface", interface, "type=internal",
            "--", "set", "Port", interface, f"tag={gateway['vlan_id']}",
            "other_config:rstp-port-admin-edge=true",
        )
        _exec(docker, container, "ip", "link", "set", "dev", interface, "up")
        _exec(docker, container, "ip", "address", "replace", gateway["address"], "dev", interface)
    hooks = [hook for hook in relay_hooks if hook["node"] == item["node"]]
    _copy_text(docker, container, "/run/netlab/dhcp-relay-hooks.json", json.dumps(hooks, indent=2) + "\n")
    _copy_text(docker, container, "/tmp/netlab-gateway.nft", item["nftables_config"])
    table = _exec(docker, container, "nft", "list", "table", "inet", "netlab_gateway", check=False)
    if table.returncode == 0:
        _exec(docker, container, "nft", "delete", "table", "inet", "netlab_gateway")
    _exec(docker, container, "nft", "-f", "/tmp/netlab-gateway.nft")
    _copy_text(docker, container, "/tmp/keepalived.conf", item["keepalived_config"])
    same_config = _exec(
        docker, container, "cmp", "-s", "/tmp/keepalived.conf", "/etc/keepalived/keepalived.conf", check=False
    ).returncode == 0
    if not same_config:
        _exec(docker, container, "install", "-D", "-m", "0644", "/tmp/keepalived.conf", "/etc/keepalived/keepalived.conf")
        _exec(docker, container, "touch", "/run/netlab/keepalived-config-changed")


def _start_keepalived(item: dict[str, Any], docker: str) -> None:
    container = item["container"]
    _exec(
        docker, container, "sh", "-ec",
        "if pgrep -x keepalived >/dev/null; then "
        "if [ -f /run/netlab/keepalived-config-changed ]; then pid=$(pgrep -o -x keepalived); kill -HUP \"$pid\"; fi; "
        "else keepalived --dont-fork --log-console >/var/log/netlab/keepalived.log 2>&1 & fi; "
        "rm -f /run/netlab/keepalived-config-changed; "
        "for attempt in 1 2 3 4 5; do pgrep -x keepalived >/dev/null && exit 0; sleep 0.2; done; "
        "echo 'keepalived did not start' >&2; exit 1",
    )


def _prepare_routed_endpoint(endpoint: dict[str, Any], docker: str) -> None:
    container = endpoint["container"]
    interface = endpoint["interface"]
    master = _exec(docker, container, "readlink", f"/sys/class/net/{interface}/master", check=False)
    if master.returncode == 0:
        raise ValueError(f"{container} {interface} is enslaved to {Path(master.stdout.strip()).name}")
    _exec(docker, container, "ip", "link", "set", "dev", interface, "up")
    _exec(docker, container, "ip", "address", "replace", endpoint["address"], "dev", interface)


def apply(plan: dict[str, Any], docker: str = "docker") -> None:
    nodes = plan["nodes"]
    containers = {item["container"] for item in nodes}
    containers.update(endpoint["container"] for endpoint in plan["routed_endpoints"])
    for container in sorted(containers):
        subprocess.run([docker, "inspect", container], check=True, stdout=subprocess.DEVNULL)
    for endpoint in plan["routed_endpoints"]:
        _prepare_routed_endpoint(endpoint, docker)
    for item in nodes:
        _prepare_node(item, plan["dhcp_relay_hooks"], docker)
    for item in nodes:
        _start_keepalived(item, docker)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--lab-name", default=LAB_NAME)
    parser.add_argument("--plan", action="store_true", help="print the inventory-derived plan without contacting Docker")
    args = parser.parse_args()
    try:
        plan = build_plan(load_inventory(args.inventory), args.lab_name)
        if args.plan:
            print(json.dumps(plan, indent=2))
        else:
            apply(plan)
            enabled = sum(1 for hook in plan["dhcp_relay_hooks"] if hook["enabled"])
            print(f"gateway policy converged on {len(plan['nodes'])} distribution nodes; DHCP relay targets configured on {enabled} VLAN interfaces")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"gateway apply failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())