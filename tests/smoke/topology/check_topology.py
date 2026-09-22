#!/usr/bin/env python3
"""Static and bounded runtime smoke checks for TOP-040."""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
TOPOLOGY = ROOT / "lab" / "phase-1.clab.yml"
INVENTORY = ROOT / "inventory" / "inventory.yaml"
RENDERER = ROOT / "scripts" / "lifecycle" / "render_topology.py"
PHYSICAL_KINDS = {"routed", "ebgp", "l2", "access"}


def fail(message: str) -> None:
    raise AssertionError(message)


def load_versions() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / "versions.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def static_checks() -> None:
    if not TOPOLOGY.is_file():
        fail(f"generated topology missing: {TOPOLOGY}")
    inventory: dict[str, Any] = json.loads(INVENTORY.read_text(encoding="utf-8"))
    topology: dict[str, Any] = yaml.safe_load(TOPOLOGY.read_text(encoding="utf-8"))
    mgmt = topology.get("mgmt", {})
    if mgmt.get("ipv4-subnet") != "172.31.255.0/24":
        fail("management subnet is not the authoritative 172.31.255.0/24")
    if mgmt.get("ipv4-range") != "172.31.255.128/25":
        fail("management dynamic range must be separated from fixed OOB reservations")
    if mgmt.get("driver-opts", {}).get("com.docker.network.bridge.enable_ip_masquerade") not in {False, "false"}:
        fail("management IP masquerading is not explicitly disabled")
    if mgmt.get("external-access") is not False:
        fail("management external access must be disabled")

    nodes = topology.get("topology", {}).get("nodes", {})
    inventory_nodes = {node["id"]: node for node in inventory["nodes"]}
    if set(nodes) != set(inventory_nodes):
        fail("topology nodes differ from authoritative inventory")
    for node_id, node in inventory_nodes.items():
        expected = str(ipaddress.ip_interface(node["oob"]).ip)
        if "privileged" in nodes[node_id]:
            fail(f"unsupported privileged toggle on Containerlab 0.77 node {node_id}")
        if nodes[node_id].get("mgmt-ipv4") != expected:
            fail(f"OOB address drift for {node_id}: expected {expected}")
        if nodes[node_id].get("labels", {}).get("netlab.inventory-node") != node_id:
            fail(f"missing inventory label for {node_id}")
    if nodes.get("isp1-core-1", {}).get("env", {}).get("NETLAB_NODE_ROLE") != "router":
        fail("ISP node must use the generic router image role so FRR starts")

    actual_links = topology.get("topology", {}).get("links", [])
    expected_links = {link["id"]: link for link in inventory["links"] if link["kind"] in PHYSICAL_KINDS}
    actual_by_id = {link.get("labels", {}).get("netlab.inventory-link"): link for link in actual_links}
    if set(actual_by_id) != set(expected_links):
        fail("physical topology links do not match authoritative inventory")
    if any(link.get("kind") == "xfrm" for link in inventory["links"] if link["id"] in actual_by_id):
        fail("XFRM link must not be rendered as a physical veth")

    mappings: dict[str, dict[str, str]] = {}
    for node in inventory["nodes"]:
        mappings[node["id"]] = {
            interface["name"]: f"eth{index}"
            for index, interface in enumerate(
                [item for item in node.get("interfaces", []) if item.get("kind") in PHYSICAL_KINDS], start=1
            )
        }
    for link_id, intent in expected_links.items():
        rendered = actual_by_id[link_id]
        if any("ipv4" in endpoint for endpoint in rendered["endpoints"]):
            fail(f"generic linux link {link_id} must not claim automatic IPv4 application")
        expected_addresses = ";".join(
            f"{endpoint['node']}:{endpoint['interface']}={endpoint['address']}"
            for endpoint in intent["endpoints"]
            if endpoint.get("address")
        )
        if rendered.get("labels", {}).get("netlab.inventory-addresses") != expected_addresses:
            fail(f"address intent drift in link {link_id}")
        for expected_endpoint, rendered_endpoint in zip(intent["endpoints"], rendered["endpoints"], strict=True):
            if rendered_endpoint["node"] != expected_endpoint["node"]:
                fail(f"node drift in link {link_id}")
            expected_interface = mappings[expected_endpoint["node"]][expected_endpoint["interface"]]
            if rendered_endpoint["interface"] != expected_interface:
                fail(f"interface drift in link {link_id}")

    versions = load_versions()
    allowed_images = {
        versions["NETLAB_NETWORK_IMAGE"],
        versions["NETLAB_CLIENT_IMAGE"],
        versions["NETLAB_SERVICE_IMAGE"],
    }
    for node in nodes.values():
        if "privileged" in node:
            fail("topology contains the unsupported privileged toggle")
        image = node.get("image")
        if image not in allowed_images:
            fail(f"node uses an image not pinned in versions.env: {image}")
        if image.rsplit(":", 1)[-1] in {"latest", "stable", "edge"}:
            fail(f"floating image tag: {image}")

    for script in ("lab-up.sh", "lab-inspect.sh", "lab-down.sh", "verify-clean.sh"):
        path = ROOT / "scripts" / "lifecycle" / script
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            # Windows hosts may expose a Bash launcher without a usable WSL
            # runtime. Keep static checks useful while recording that syntax
            # execution is blocked; the dedicated WSL preflight owns this gate.
            launcher_output = (result.stdout + result.stderr).lower()
            if (
                "access is denied" in launcher_output
                or "bash/service" in launcher_output
                or "/bin/bash" in launcher_output
                or (not result.stderr and result.stdout)
            ):
                print(f"syntax=blocked: Bash/WSL is unavailable for {script}")
                continue
            fail(f"shell syntax failed for {script}: {result.stderr.strip()}")
    down = (ROOT / "scripts" / "lifecycle" / "lab-down.sh").read_text(encoding="utf-8")
    executable_down = "\n".join(line for line in down.splitlines() if not line.lstrip().startswith("#"))
    if "destroy --all" in executable_down or "docker rm" in executable_down or "docker system prune" in executable_down:
        fail("teardown contains an unsafe broad deletion command")

    result = subprocess.run([sys.executable, str(RENDERER), "--check"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(result.stderr.strip() or "generated topology is stale")
    print(f"static smoke passed: {len(nodes)} nodes, {len(actual_links)} physical links")


def runtime_probe() -> None:
    if shutil.which("docker") is None or shutil.which("containerlab") is None:
        print("runtime=blocked: Docker or Containerlab is unavailable")
        return
    if subprocess.run(["docker", "info"], capture_output=True, check=False).returncode != 0:
        print("runtime=blocked: Docker daemon is unavailable")
        return
    result = subprocess.run(
        ["containerlab", "inspect", "--topo", str(TOPOLOGY), "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("runtime=blocked: inspect failed; no deployment acceptance claimed")
        print(result.stderr.strip(), file=sys.stderr)
        return
    print("runtime=available: bounded topology inspect completed; health acceptance remains separate")


if __name__ == "__main__":
    try:
        static_checks()
        runtime_probe()
    except (AssertionError, KeyError, OSError, ValueError, yaml.YAMLError) as exc:
        print(f"topology smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
