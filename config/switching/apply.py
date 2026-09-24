#!/usr/bin/env python3
"""Apply inventory-derived site switching policy to running Containerlab nodes."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
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


def _endpoint_map(data: dict[str, Any]) -> dict[tuple[str, str], str]:
    """Match the stable ethN mapping used by the topology renderer."""
    mapping: dict[tuple[str, str], str] = {}
    for node in data["nodes"]:
        index = 1
        for interface in node.get("interfaces", []):
            if interface.get("kind") in PHYSICAL_KINDS:
                mapping[(node["id"], interface["name"])] = f"eth{index}"
                index += 1
    return mapping


def build_plan(data: dict[str, Any], lab_name: str = LAB_NAME) -> list[dict[str, Any]]:
    """Return the exact docker argv calls needed to converge switching state."""
    nodes = {node["id"]: node for node in data["nodes"]}
    ports = _endpoint_map(data)
    site_vlans = tuple(sorted({int(vlan["vlan_id"]) for vlan in data["vlans"]}))
    switch_nodes = {
        node_id: node
        for node_id, node in nodes.items()
        if node.get("role") in {"dist", "access"}
    }
    node_bundles: dict[str, dict[str, list[str]]] = defaultdict(dict)
    bundle_links: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in data["links"]:
        if link.get("kind") == "l2" and link.get("bundle"):
            bundle_links[link["bundle"]].append(link)

    bundles = {bundle["id"]: bundle for bundle in data["bundles"]}
    for bundle_id, links in bundle_links.items():
        bundle = bundles[bundle_id]
        if bundle.get("mode") != "lacp" or tuple(sorted(bundle.get("trunk_vlans", []))) != site_vlans:
            raise ValueError(f"unsupported switching policy for bundle {bundle_id}")
        if len(links) != 2:
            raise ValueError(f"LACP bundle {bundle_id} must have exactly two physical links")
        endpoints_by_node: dict[str, list[str]] = defaultdict(list)
        for link in links:
            if len(link["endpoints"]) != 2:
                raise ValueError(f"bundle link {link['id']} must have exactly two endpoints")
            for endpoint in link["endpoints"]:
                node_id = endpoint["node"]
                if node_id not in switch_nodes:
                    raise ValueError(f"LACP bundle {bundle_id} references non-switch {node_id}")
                endpoints_by_node[node_id].append(ports[(node_id, endpoint["interface"])])
        peers = sorted(endpoints_by_node)
        if peers != sorted(bundle.get("peer_nodes", [])) or len(peers) != 2:
            raise ValueError(f"LACP bundle {bundle_id} does not match its two declared peers")
        if any(len(members) != 2 for members in endpoints_by_node.values()):
            raise ValueError(f"LACP bundle {bundle_id} must have two members at each peer")
        if bundle.get("kind") == "access-uplink":
            if sum(nodes[peer].get("role") == "dist" for peer in peers) != 1:
                raise ValueError(f"access bundle {bundle_id} must terminate on one distribution node")
        elif bundle.get("kind") == "distribution-peer":
            if any(nodes[peer].get("role") != "dist" for peer in peers):
                raise ValueError(f"distribution peer bundle {bundle_id} must connect only distribution nodes")
        else:
            raise ValueError(f"unsupported L2 bundle kind {bundle.get('kind')!r}")
        for peer, members in endpoints_by_node.items():
            node_bundles[peer][bundle_id] = sorted(members)

    vlan_ids = {vlan["id"]: int(vlan["vlan_id"]) for vlan in data["vlans"]}
    access_links: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for link in data["links"]:
        if link.get("kind") != "access":
            continue
        switch_ends = [end for end in link["endpoints"] if end["node"] in switch_nodes]
        if len(switch_ends) != 1:
            raise ValueError(f"access link {link['id']} must have exactly one switch endpoint")
        switch_end = switch_ends[0]
        vlan_id = vlan_ids.get(link.get("vlan"))
        if vlan_id not in site_vlans:
            raise ValueError(f"access link {link['id']} references an unsupported site VLAN")
        access_links[switch_end["node"]].append((ports[(switch_end["node"], switch_end["interface"])], vlan_id))

    plan: list[dict[str, Any]] = []
    for node_id in sorted(switch_nodes):
        node = switch_nodes[node_id]
        site_nodes = [item for item in switch_nodes.values() if item.get("site") == node.get("site")]
        root_dist = min((item["id"] for item in site_nodes if item.get("role") == "dist"), default=None)
        priority = 4096 if node_id == root_dist else 8192 if node.get("role") == "dist" else 32768
        container = f"clab-{lab_name}-{node_id}"
        plan.append({
            "node": node_id,
            "container": container,
            "bridge_setup": True,
            "argv": [
                "ovs-vsctl", "set", "Bridge", BRIDGE,
                "rstp_enable=true", f"other_config:rstp-priority={priority}",
            ],
        })
        # Bond names stay within Linux's 15-character interface-name limit.
        for index, (bundle_id, members) in enumerate(sorted(node_bundles[node_id].items()), start=1):
            bond = f"bond{index}"
            trunks = ",".join(str(vlan) for vlan in site_vlans)
            plan.append({
                "node": node_id,
                "container": container,
                "argv": [
                    "ovs-vsctl", "--may-exist", "add-port", BRIDGE, bond,
                    "--", "set", "Port", bond,
                    "vlan_mode=trunk", f"trunks={trunks}",
                    "other_config:rstp-enable=true",
                ],
                "bundle": bundle_id,
                "members": members,
            })
        for interface, vlan_id in sorted(access_links[node_id]):
            plan.append({
                "node": node_id,
                "container": container,
                "argv": [
                    "ovs-vsctl", "--may-exist", "add-port", BRIDGE, interface,
                    "--", "set", "Port", interface,
                    "vlan_mode=access", f"tag={vlan_id}",
                    "other_config:rstp-port-admin-edge=true",
                ],
                "access_vlan": vlan_id,
            })
    return plan


def _exec(
    docker: str, container: str, *argv: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [docker, "exec", container, *argv], text=True, capture_output=True
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{container} {' '.join(argv)}: {detail}")
    return result


def _converge_bond(item: dict[str, Any], docker: str) -> None:
    container = item["container"]
    bond = item["argv"][4]
    members = item["members"]
    existing = _exec(docker, container, "ip", "link", "show", "dev", bond, check=False).returncode == 0
    if existing:
        state = _exec(docker, container, "cat", f"/proc/net/bonding/{bond}").stdout
        if "Bonding Mode: IEEE 802.3ad" not in state:
            raise ValueError(f"{container} {bond} is not an 802.3ad bond")
    masters = {}
    for member in members:
        master = _exec(
            docker, container, "readlink", f"/sys/class/net/{member}/master", check=False
        )
        masters[member] = Path(master.stdout.strip()).name if master.returncode == 0 else None
        if masters[member] not in (None, bond):
            raise ValueError(f"{container} {member} belongs to a different master")
    if not existing:
        _exec(
            docker, container, "ip", "link", "add", bond, "type", "bond",
            "mode", "802.3ad", "miimon", "100", "lacp_rate", "fast",
        )
    for member in members:
        if masters[member] == bond:
            continue
        _exec(docker, container, "ip", "link", "set", "dev", member, "down")
        _exec(docker, container, "ip", "link", "set", "dev", member, "master", bond)
        _exec(docker, container, "ip", "link", "set", "dev", member, "up")
    _exec(docker, container, "ip", "link", "set", "dev", bond, "up")
    state = _exec(docker, container, "cat", f"/proc/net/bonding/{bond}").stdout
    actual_members = re.findall(r"^Slave Interface: (\S+)$", state, re.MULTILINE)
    if "Bonding Mode: IEEE 802.3ad" not in state or sorted(actual_members) != sorted(members):
        raise ValueError(f"{container} {bond} does not match the two-member 802.3ad plan")


def apply(plan: list[dict[str, Any]], docker: str = "docker") -> None:
    containers = sorted({item["container"] for item in plan})
    for container in containers:
        subprocess.run([docker, "inspect", container], check=True, stdout=subprocess.DEVNULL)
    # Every bridge must run RSTP before admitting any bonded loop edge.
    for item in plan:
        if item.get("bridge_setup"):
            _exec(docker, item["container"], *item["argv"])
    for item in plan:
        if item.get("bridge_setup"):
            continue
        if item.get("bundle"):
            _converge_bond(item, docker)
        _exec(docker, item["container"], *item["argv"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--lab-name", default=LAB_NAME)
    parser.add_argument("--plan", action="store_true", help="print commands without contacting Docker")
    args = parser.parse_args()
    try:
        plan = build_plan(load_inventory(args.inventory), args.lab_name)
        if args.plan:
            print(json.dumps(plan, indent=2))
        else:
            apply(plan)
            print(f"switching policy converged on {len({item['node'] for item in plan})} site switches")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"switching apply failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
