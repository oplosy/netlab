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


def _keepalived_config(
    node: dict[str, Any], gateways: list[dict[str, Any]], remote_aggregate: str
) -> str:
    site = node["site"].upper()
    suffix = node["id"].rsplit("-", 1)[-1]
    lines = [
        "! Generated from inventory; do not edit.",
        "global_defs {",
        f"  router_id {site}_{suffix}",
        "  enable_script_security",
        "  script_user root",
        "}",
        "",
        "vrrp_script chk_remote_ospf_route {",
        f"  script \"/usr/local/sbin/netlab-check-ospf-route {remote_aggregate}\"",
        "  interval 1",
        "  timeout 1",
        "  fall 1",
        "  rise 2",
        "  weight -60",
        "}",
        "",
    ]
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
            # Keepalived defaults to three adverts to follow the VRRP RFC.
            # The lab uses one to fit the measured site failover within its
            # three-second limit; every peer for these instances matches.
            "  down_timer_adverts 1",
            "  garp_master_delay 1",
            "  garp_master_repeat 3",
            "  virtual_ipaddress {",
            f"    {gateway['virtual_ip']} dev {gateway['interface']}",
            "  }",
            '  notify_master "/usr/local/sbin/netlab-dhcp-relay-reconcile"',
            '  notify_backup "/usr/local/sbin/netlab-dhcp-relay-reconcile"',
            '  notify_fault "/usr/local/sbin/netlab-dhcp-relay-reconcile"',
            "  track_script {",
            "    chk_remote_ospf_route",
            "  }",
            "}",
            "",
        ])
    return "\n".join(lines)


def _nftables_config(
    destinations: list[str],
    vlans: list[dict[str, Any]],
    services: dict[str, str],
) -> str:
    blocked = ", ".join(destinations)
    oob_network = "172.31.255.0/24"
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
        lines.append(f'    iifname "vlan{vlan_id}" ip daddr {oob_network} counter drop')
    lines.extend([
        f'    iifname "vlan30" ip daddr {services["dns"]} udp dport 53 accept',
        f'    iifname "vlan30" ip daddr {services["dns"]} tcp dport 53 accept',
        f'    iifname "vlan30" ip daddr {services["ntp"]} udp dport 123 accept',
    ])
    lines.extend([
        f'    iifname "vlan30" ip daddr {{ {blocked} }} counter drop',
        "  }",
        "  chain forward {",
        "    type filter hook forward priority filter; policy accept;",
        f'    iifname "vlan30" ip daddr {services["dns"]} udp dport 53 accept',
        f'    iifname "vlan30" ip daddr {services["dns"]} tcp dport 53 accept',
        f'    iifname "vlan30" ip daddr {services["ntp"]} udp dport 123 accept',
        *[
            f'    iifname "vlan{int(vlan["vlan_id"])}" ip daddr {oob_network} counter drop'
            for vlan in vlans
        ],
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

    dhcp_services = {
        node["site"]: node for node in data["nodes"]
        if node.get("service") == "dhcp" and node.get("site")
    }
    if len(dhcp_services) != len(sites):
        raise ValueError("each site must have exactly one local DHCP service node")

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
        endpoint_roles = {nodes[endpoint["node"]].get("role") for endpoint in link["endpoints"]}
        # ISP service networks are configured by the services role; this plan
        # owns only the site edge-to-distribution routed links.
        if endpoint_roles != {"edge", "dist"}:
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
        dhcp_server = str(ipaddress.ip_interface(dhcp_services[site_id]["service_address"]).ip)
        remote_site = next(site for site in data["sites"] if site["id"] != site_id)
        remote_aggregate = str(ipaddress.ip_network(remote_site["aggregate"]))
        site_vlans = sorted(vlans_by_site.get(site_id, []), key=lambda vlan: int(vlan["vlan_id"]))
        site_services: dict[str, str] = {}
        for service in ("dns", "ntp"):
            service_node = next(
                (
                    candidate for candidate in data["nodes"]
                    if candidate.get("site") == site_id
                    and candidate.get("role") == "service"
                    and candidate.get("service") == service
                ),
                None,
            )
            if service_node is None or not service_node.get("service_address"):
                raise ValueError(f"{site_id} is missing its local {service} service address")
            site_services[service] = str(ipaddress.ip_interface(service_node["service_address"]).ip)
        nftables_config = _nftables_config(blocked_destinations, site_vlans, site_services)
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
                "ovs_port": f"svi{int(vlan['vlan_id'])}",
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
                "virtual_ip": entry["virtual_ip"],
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
            "remote_aggregate": remote_aggregate,
            "keepalived_config": _keepalived_config(node, gateways, remote_aggregate),
            "nftables_config": nftables_config,
        })

    if len(planned_nodes) != len(sites) * 2:
        raise ValueError("every declared site must have exactly two distribution nodes")
    return {"nodes": planned_nodes, "routed_endpoints": routed_endpoints, "dhcp_relay_hooks": relay_hooks}


def _exec(docker: str, container: str, *argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([docker, "exec", container, *argv], text=True, capture_output=True, check=False)
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


def _prepare_svi(docker: str, container: str, gateway: dict[str, Any]) -> None:
    interface = gateway["interface"]
    ovs_port = gateway["ovs_port"]

    # Migrate the previous OVS internal SVI once. Internal Ports do not work
    # with RSTP, so use a veth system Port as an RSTP edge and keep the Linux
    # endpoint as the SVI that owns its address and VRRP instance.
    old_type = _exec(
        docker, container, "ovs-vsctl", "--if-exists", "get", "Interface", interface, "type", check=False
    )
    if old_type.returncode == 0 and old_type.stdout.strip().strip('"') == "internal":
        _exec(docker, container, "ovs-vsctl", "--if-exists", "del-port", BRIDGE, interface)
        _exec(docker, container, "ip", "link", "del", "dev", interface, check=False)

    linux = _exec(docker, container, "ip", "link", "show", "dev", interface, check=False)
    ovs_link = _exec(docker, container, "ip", "link", "show", "dev", ovs_port, check=False)
    if bool(linux.returncode == 0) != bool(ovs_link.returncode == 0):
        raise ValueError(f"{container} SVI veth pair {ovs_port}<->{interface} is incomplete")
    if linux.returncode != 0:
        _exec(
            docker, container, "ip", "link", "add", ovs_port,
            "type", "veth", "peer", "name", interface,
        )
    else:
        ovs_ifindex = _exec(docker, container, "cat", f"/sys/class/net/{ovs_port}/ifindex").stdout.strip()
        ovs_peer = _exec(docker, container, "cat", f"/sys/class/net/{ovs_port}/iflink").stdout.strip()
        linux_ifindex = _exec(docker, container, "cat", f"/sys/class/net/{interface}/ifindex").stdout.strip()
        linux_peer = _exec(docker, container, "cat", f"/sys/class/net/{interface}/iflink").stdout.strip()
        if ovs_peer != linux_ifindex or linux_peer != ovs_ifindex:
            raise ValueError(f"{container} SVI interfaces {ovs_port} and {interface} are not a veth pair")

    _exec(
        docker,
        container,
        "ovs-vsctl", "--may-exist", "add-port", BRIDGE, ovs_port,
        "--", "set", "Port", ovs_port,
        "vlan_mode=access", f"tag={gateway['vlan_id']}",
        "other_config:rstp-enable=true",
        "other_config:rstp-port-admin-edge=true",
    )
    _exec(docker, container, "ip", "link", "set", "dev", ovs_port, "up")
    _exec(docker, container, "ip", "link", "set", "dev", interface, "up")
    _exec(docker, container, "ip", "address", "replace", gateway["address"], "dev", interface)


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
        _prepare_svi(docker, container, gateway)
    hooks = [hook for hook in relay_hooks if hook["node"] == item["node"]]
    _copy_text(docker, container, "/run/netlab/dhcp-relay-hooks.json", json.dumps(hooks, indent=2) + "\n")
    _configure_dhcp_relay(container, hooks, docker)
    _copy_text(docker, container, "/tmp/netlab-gateway.nft", item["nftables_config"])
    table = _exec(docker, container, "nft", "list", "table", "inet", "netlab_gateway", check=False)
    if table.returncode == 0:
        _exec(docker, container, "nft", "delete", "table", "inet", "netlab_gateway")
    _exec(docker, container, "nft", "-f", "/tmp/netlab-gateway.nft")
    checker = ROOT / "config" / "gateway" / "check_ospf_route.sh"
    _copy_text(docker, container, "/tmp/netlab-check-ospf-route", checker.read_text(encoding="utf-8"))
    same_checker = _exec(
        docker, container, "cmp", "-s", "/tmp/netlab-check-ospf-route",
        "/usr/local/sbin/netlab-check-ospf-route", check=False,
    ).returncode == 0
    if not same_checker:
        _exec(
            docker, container, "install", "-D", "-m", "0755",
            "/tmp/netlab-check-ospf-route", "/usr/local/sbin/netlab-check-ospf-route",
        )
    _copy_text(docker, container, "/tmp/keepalived.conf", item["keepalived_config"])
    same_config = _exec(
        docker, container, "cmp", "-s", "/tmp/keepalived.conf", "/etc/keepalived/keepalived.conf", check=False
    ).returncode == 0
    if not same_config:
        _exec(docker, container, "install", "-D", "-m", "0644", "/tmp/keepalived.conf", "/etc/keepalived/keepalived.conf")
        _exec(docker, container, "touch", "/run/netlab/keepalived-config-changed")


def _configure_dhcp_relay(container: str, hooks: list[dict[str, Any]], docker: str) -> None:
    reconcile = ROOT / "config" / "gateway" / "dhcp_relay_reconcile.sh"
    _copy_text(docker, container, "/tmp/netlab-dhcp-relay-reconcile", reconcile.read_text(encoding="utf-8"))
    _exec(docker, container, "install", "-D", "-m", "0755",
          "/tmp/netlab-dhcp-relay-reconcile", "/usr/local/sbin/netlab-dhcp-relay-reconcile")
    _exec(docker, container, "/usr/local/sbin/netlab-dhcp-relay-reconcile")


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
