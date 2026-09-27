#!/usr/bin/env python3
"""Converge the inventory-defined HQ/BR1 IKEv2 XFRM overlay."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = ROOT / "inventory" / "inventory.yaml"
PKI_HELPER = ROOT / "scripts" / "pki" / "generate.sh"
LAB_NAME = "netlab-phase-1"
XFRM_INTERFACE = "xfrm0"
XFRM_IF_ID = 42
OVERLAY_MTU = 1400
TCP_MSS = OVERLAY_MTU - 40
PEER_CONNECTION = "site-overlay"
PKI_DIR = "/run/netlab/ipsec-pki"
CONFIG_PATH = "/etc/swanctl/swanctl.conf"


def load_inventory(path: Path = DEFAULT_INVENTORY) -> dict[str, Any]:
    import yaml

    with path.open(encoding="utf-8") as stream:
        result = yaml.safe_load(stream)
    if not isinstance(result, dict):
        raise TypeError("inventory root must be a mapping")
    return result


def _ip(value: str, label: str) -> ipaddress.IPv4Interface:
    interface = ipaddress.ip_interface(value)
    if not isinstance(interface, ipaddress.IPv4Interface):
        raise TypeError(f"{label} must be IPv4: {value}")
    return interface


def _reported_xfrm_if_id(output: str) -> int | None:
    match = re.search(r"\bxfrm\s+if_id\s+(0x[0-9a-fA-F]+|[0-9]+)\b", output)
    return int(match.group(1), 0) if match else None


def _render_swanctl(peer: dict[str, Any], remote: dict[str, Any]) -> str:
    connection = peer["connection"]
    return f"""connections {{
  {connection} {{
    version = 2
    local_addrs = {peer['public_endpoint'].ip}
    remote_addrs = {remote['public_endpoint'].ip}
    proposals = aes256gcm16-prfsha384-ecp384
    rekey_time = 4h

    local {{
      auth = pubkey
      id = {peer['identity']}
      certs = {peer['certificate']}
    }}
    remote {{
      auth = pubkey
      id = {remote['identity']}
      cacerts = netlab-ca.cert.pem
    }}

    children {{
      {connection} {{
        local_ts = 0.0.0.0/0
        remote_ts = 0.0.0.0/0
        esp_proposals = aes256gcm16-ecp384
        if_id_in = {peer['xfrm_if_id']}
        if_id_out = {peer['xfrm_if_id']}
        rekey_time = 6m
        life_time = 7m
        rand_time = 0s
      }}
    }}
  }}
}}
"""


def build_plan(data: dict[str, Any], lab_name: str = LAB_NAME) -> dict[str, Any]:
    sites = {site["id"]: site for site in data["sites"]}
    edges = {node["id"]: node for node in data["nodes"] if node.get("role") == "edge"}
    peers: list[dict[str, Any]] = []
    xfrm_links = [link for link in data["links"] if link.get("kind") == "xfrm"]
    if len(xfrm_links) != 2:
        raise ValueError("WAN-230 requires exactly two HQ-to-BR1 XFRM links")
    seen_nodes: set[str] = set()
    for link in xfrm_links:
        endpoint_map = {endpoint["node"]: endpoint for endpoint in link["endpoints"]}
        if len(endpoint_map) != 2 or len(link["endpoints"]) != 2:
            raise ValueError(f"XFRM link {link.get('id')} must have exactly two distinct endpoints")
        local_id, remote_id = sorted(endpoint_map)
        local_node, remote_node = edges.get(local_id), edges.get(remote_id)
        if local_node is None or remote_node is None or {local_node.get("site"), remote_node.get("site")} != {"hq", "br1"}:
            raise ValueError(f"XFRM link {link.get('id')} must connect one HQ and one BR1 edge")
        if local_id in seen_nodes or remote_id in seen_nodes:
            raise ValueError("each edge must terminate exactly one XFRM link")
        seen_nodes.update((local_id, remote_id))
        pair_id = link["id"].replace("-", "_")
        if_id = XFRM_IF_ID if local_node["id"].endswith("-edge-1") else XFRM_IF_ID + 1
        for node, remote_node_data in ((local_node, remote_node), (remote_node, local_node)):
            site_id = node["site"]
            endpoint = endpoint_map[node["id"]]
            remote_endpoint = endpoint_map[remote_node_data["id"]]
            xfrm_address = _ip(endpoint["address"], f"{site_id} XFRM address")
            remote_address = _ip(remote_endpoint["address"], f"{remote_node_data['id']} XFRM address")
            if xfrm_address.network != remote_address.network or xfrm_address.ip == remote_address.ip:
                raise ValueError("XFRM peers must use distinct addresses from the same prefix")
            public_endpoint = _ip(node["public_endpoint"], f"{node['id']} public endpoint")
            remote_public = _ip(remote_node_data["public_endpoint"], f"{remote_node_data['id']} public endpoint")
            peers.append(
                {
                    "site": site_id,
                    "node": node["id"],
                    "container": f"clab-{lab_name}-{node['id']}",
                    "identity": f"{node['id']}.netlab",
                    "certificate": f"{node['id']}.cert.pem",
                    "xfrm_interface": XFRM_INTERFACE,
                    "xfrm_if_id": if_id,
                    "connection": f"{PEER_CONNECTION}-{pair_id}",
                    "link": link["id"],
                    "ospf_cost": int(link.get("ospf_cost", 100)),
                    "address": str(xfrm_address),
                    "peer_address": str(remote_address.ip),
                    "public_endpoint": public_endpoint,
                    "remote_public_endpoint": remote_public,
                    "remote_node": remote_node_data["id"],
                    "remote_container": f"clab-{lab_name}-{remote_node_data['id']}",
                    "remote_identity": f"{remote_node_data['id']}.netlab",
                    "remote_certificate": f"{remote_node_data['id']}.cert.pem",
                    "swanctl_config": "",
                }
            )
    if seen_nodes != set(edges):
        raise ValueError(f"every active edge must terminate one XFRM link; missing {sorted(set(edges) - seen_nodes)}")
    for peer in peers:
        remote = next(item for item in peers if item["node"] == peer["remote_node"])
        peer["swanctl_config"] = _render_swanctl(peer, remote)
    return {"lab_name": lab_name, "peers": peers, "mtu": OVERLAY_MTU, "tcp_mss": TCP_MSS}


def _run(docker: str, *argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([docker, *argv], text=True, capture_output=True, check=False)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{docker} {' '.join(argv)} failed: {detail}")
    return result


def _exec(docker: str, container: str, *argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(docker, "exec", container, *argv, check=check)


def _write_remote(docker: str, container: str, path: str, content: bytes, mode: str = "0600") -> None:
    result = subprocess.run(
        [docker, "exec", "-i", container, "sh", "-ec", 'umask 077; cat > "$1"; chmod "$2" "$1"', "sh", path, mode],
        input=content,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"{container} could not write {path}: {detail}")


def _read_remote(docker: str, container: str, path: str) -> bytes:
    result = subprocess.run([docker, "exec", container, "cat", path], capture_output=True, check=False)
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"{container} could not read {path}: {detail}")
    return result.stdout


def _prepare_pki(docker: str, plan: dict[str, Any]) -> None:
    ca_peer = next(peer for peer in plan["peers"] if peer["node"] == "hq-edge-1")
    helper = PKI_HELPER.read_bytes()
    for peer in plan["peers"]:
        _write_remote(docker, peer["container"], "/tmp/netlab-ipsec-pki.sh", helper, "0700")
    _exec(docker, ca_peer["container"], "bash", "/tmp/netlab-ipsec-pki.sh", "init-ca", ca_peer["identity"])
    for peer in plan["peers"]:
        if peer["node"] == ca_peer["node"]:
            continue
        _exec(docker, peer["container"], "bash", "/tmp/netlab-ipsec-pki.sh", "create-request", peer["identity"])
        csr = _read_remote(docker, peer["container"], f"{PKI_DIR}/{peer['node']}.csr.pem")
        _write_remote(docker, ca_peer["container"], f"{PKI_DIR}/{peer['node']}.csr.pem", csr)
        _exec(docker, ca_peer["container"], "bash", "/tmp/netlab-ipsec-pki.sh", "sign-request", peer["identity"])

    for peer in plan["peers"]:
        _exec(docker, peer["container"], "install", "-d", "-m", "0755", "/etc/swanctl/x509ca", "/etc/swanctl/x509")
        _exec(docker, peer["container"], "install", "-d", "-m", "0700", "/etc/swanctl/private")
        _exec(
            docker,
            peer["container"],
            "install", "-m", "0600",
            f"{PKI_DIR}/{peer['node']}.key.pem",
            f"/etc/swanctl/private/{peer['node']}.key.pem",
        )
        cert = _read_remote(docker, ca_peer["container"], f"{PKI_DIR}/{peer['node']}.cert.pem")
        ca_cert = _read_remote(docker, ca_peer["container"], f"{PKI_DIR}/ca.cert.pem")
        _write_remote(docker, peer["container"], f"/etc/swanctl/x509/{peer['certificate']}", cert, "0644")
        _write_remote(docker, peer["container"], "/etc/swanctl/x509ca/netlab-ca.cert.pem", ca_cert, "0644")
        _write_remote(docker, peer["container"], CONFIG_PATH, peer["swanctl_config"].encode(), "0644")


def _prepare_xfrm(docker: str, peer: dict[str, Any], mtu: int) -> None:
    interface = peer["xfrm_interface"]
    existing = _exec(docker, peer["container"], "ip", "-d", "-o", "link", "show", "dev", interface, check=False)
    if existing.returncode:
        _exec(
            docker,
            peer["container"],
            "ip", "link", "add", interface, "type", "xfrm", "if_id", str(peer["xfrm_if_id"]),
        )
    else:
        if _reported_xfrm_if_id(existing.stdout) != peer["xfrm_if_id"]:
            raise ValueError(f"{peer['container']} {interface} exists with incompatible XFRM settings")
    _exec(docker, peer["container"], "ip", "address", "replace", peer["address"], "dev", interface)
    _exec(docker, peer["container"], "ip", "link", "set", "dev", interface, "mtu", str(mtu), "up")
    _exec(docker, peer["container"], "sysctl", "-w", "net.ipv4.tcp_mtu_probing=1")


def _prepare_mss(docker: str, peer: dict[str, Any], mss: int) -> None:
    container = peer["container"]
    table = _exec(docker, container, "nft", "list", "table", "inet", "netlab_ipsec_mss", check=False)
    if table.returncode == 0:
        _exec(docker, container, "nft", "delete", "table", "inet", "netlab_ipsec_mss")
    rules = f"""table inet netlab_ipsec_mss {{
  chain forward {{
    type filter hook forward priority mangle; policy accept;
    oifname \"{XFRM_INTERFACE}\" tcp flags syn tcp option maxseg size set {mss}
    iifname \"{XFRM_INTERFACE}\" tcp flags syn tcp option maxseg size set {mss}
  }}
}}
"""
    result = subprocess.run(
        [docker, "exec", "-i", container, "nft", "-f", "-"],
        input=rules.encode(),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"{container} MSS rules failed: {detail}")


def _load_swanctl(docker: str, peer: dict[str, Any]) -> None:
    last_error = "VICI socket not ready"
    for _ in range(50):
        result = _exec(docker, peer["container"], "swanctl", "--load-all", "--noprompt", check=False)
        if result.returncode == 0:
            return
        last_error = result.stderr.strip() or result.stdout.strip()
        time.sleep(0.2)
    raise RuntimeError(f"{peer['container']} swanctl config load timed out: {last_error}")


def apply(plan: dict[str, Any], docker: str = "docker") -> None:
    for peer in plan["peers"]:
        _run(docker, "inspect", "--type", "container", peer["container"])
    _prepare_pki(docker, plan)
    for peer in plan["peers"]:
        _prepare_xfrm(docker, peer, plan["mtu"])
        _prepare_mss(docker, peer, plan["tcp_mss"])
        _load_swanctl(docker, peer)
    print(f"IPsec configuration converged on {len(plan['peers'])} edge nodes; XFRM MTU={plan['mtu']} MSS={plan['tcp_mss']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--plan", action="store_true", help="print inventory-derived configuration without contacting Docker")
    args = parser.parse_args()
    try:
        plan = build_plan(load_inventory(args.inventory))
        if args.plan:
            print(json.dumps(plan, indent=2, default=str))
        else:
            apply(plan, args.docker)
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"IPsec apply failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
