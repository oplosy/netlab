#!/usr/bin/env python3
"""AUTO-530: normalized live-state snapshots, material-change and drift reports.

Device drift is a difference between the normalized live state and the golden
snapshot recorded by the last successful postcheck. Intent drift is a
difference between the current render and the render recorded with the golden
snapshot (ADR 0019). Volatile state (counters, uptimes, VRRP virtual addresses
that move on failover) is excluded, so failover does not read as drift.

Subcommands:
  capture --output FILE [--render-digest]   snapshot every network node
  compare BASE CURRENT [--report FILE]      exit 3 when the snapshots differ
  drift --golden FILE [--report FILE]       live + render vs golden; exit 3 on drift
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import ipaddress
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "inventory" / "inventory.yaml"
LAB = "netlab-phase-1"
NETWORK_ROLES = {"edge", "dist", "firewall", "access", "isp"}
DRIFT_EXIT = 3

SECTIONS: dict[str, list[tuple[str, list[str]]]] = {
    # role -> [(section, command)]
    "common": [
        ("nftables", ["nft", "-s", "list", "ruleset"]),
        ("ipv4-addresses", ["ip", "-4", "-o", "address", "show"]),
        ("ip-forward", ["cat", "/proc/sys/net/ipv4/ip_forward"]),
    ],
    "frr": [("frr-running-config", ["vtysh", "-c", "show running-config"])],
    "ovs": [(
        "ovs-ports",
        ["ovs-vsctl", "--format=csv", "--columns=name,tag,trunks,vlan_mode,bond_mode,lacp",
         "list", "port"],
    )],
    "ipsec": [("swanctl-conns", ["swanctl", "--list-conns"])],
    "keepalived": [("keepalived-config", ["cat", "/etc/keepalived/keepalived.conf"])],
}
ROLE_SECTIONS = {
    "edge": ["common", "frr", "ipsec"],
    "dist": ["common", "frr", "ovs", "keepalived"],
    "firewall": ["common", "frr"],
    "access": ["common", "ovs"],
    "isp": ["common", "frr"],
}


def load_inventory(path: Path | None = None) -> dict[str, Any]:
    # NETLAB_INVENTORY lets the drift test present changed, not-yet-applied intent.
    path = path or Path(os.environ.get("NETLAB_INVENTORY", INVENTORY))
    return json.loads(path.read_text(encoding="utf-8"))


def vrrp_addresses(data: dict[str, Any]) -> set[str]:
    return {
        str(ipaddress.ip_interface(vlan["gateway"]).ip)
        for vlan in data["vlans"]
        if vlan.get("gateway")
    }


def normalize(section: str, text: str, volatile: set[str]) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    if section == "ipv4-addresses":
        kept = []
        for line in lines:
            parts = line.split()
            if len(parts) < 4 or parts[2] != "inet":
                continue
            address = parts[3].split("/")[0]
            if address in volatile or parts[1] == "lo" and address.startswith("127."):
                continue
            kept.append(f"{parts[1].split('@')[0]} {parts[3]}")
        return "\n".join(sorted(kept))
    if section == "frr-running-config":
        lines = [
            line for line in lines
            if not line.startswith(("Building configuration", "Current configuration", "frr version"))
        ]
    if section == "ovs-ports":
        header, rows = lines[:1], sorted(lines[1:])
        return "\n".join(header + rows)
    return "\n".join(lines).strip()


def capture(data: dict[str, Any], docker: str = "docker") -> dict[str, Any]:
    volatile = vrrp_addresses(data)
    nodes: dict[str, dict[str, str]] = {}
    for node in sorted(data["nodes"], key=lambda n: n["id"]):
        role = node.get("role")
        if role not in NETWORK_ROLES:
            continue
        state: dict[str, str] = {}
        for group in ROLE_SECTIONS[role]:
            for section, command in SECTIONS[group]:
                result = subprocess.run(
                    [docker, "exec", f"clab-{LAB}-{node['id']}", *command],
                    capture_output=True, text=True, check=False, timeout=60,
                )
                raw = result.stdout if result.returncode == 0 else f"ERROR {result.returncode}"
                state[section] = normalize(section, raw, volatile)
        nodes[node["id"]] = state
    return {
        "captured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodes": nodes,
        "digest": hashlib.sha256(json.dumps(nodes, sort_keys=True).encode()).hexdigest(),
    }


def render_digest() -> str:
    sys.path.insert(0, str(ROOT / "automation" / "render"))
    import render_all  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        render_all.write(render_all.render(load_inventory()), Path(tmp))
        digests = render_all.digest(Path(tmp))
    return hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest()


def compare(base: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    differences = []
    for node in sorted(set(base["nodes"]) | set(current["nodes"])):
        before, after = base["nodes"].get(node, {}), current["nodes"].get(node, {})
        for section in sorted(set(before) | set(after)):
            if before.get(section) != after.get(section):
                diff = list(difflib.unified_diff(
                    (before.get(section) or "").splitlines(),
                    (after.get(section) or "").splitlines(),
                    f"{node}/{section} (base)", f"{node}/{section} (current)", lineterm="", n=1,
                ))
                differences.append({"node": node, "section": section, "diff": diff[:60]})
    return differences


def _write(path: Path | None, payload: dict[str, Any]) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--output", type=Path, required=True)
    cap.add_argument("--render-digest", action="store_true", help="record the current render digest")
    cmp = sub.add_parser("compare")
    cmp.add_argument("base", type=Path)
    cmp.add_argument("current", type=Path)
    cmp.add_argument("--report", type=Path)
    drift = sub.add_parser("drift")
    drift.add_argument("--golden", type=Path, required=True)
    drift.add_argument("--report", type=Path)
    args = parser.parse_args()

    if args.command == "capture":
        snapshot = capture(load_inventory())
        if args.render_digest:
            snapshot["render_digest"] = render_digest()
        _write(args.output, snapshot)
        print(json.dumps({"nodes": len(snapshot["nodes"]), "digest": snapshot["digest"]}))
        return 0
    if args.command == "compare":
        base = json.loads(args.base.read_text(encoding="utf-8"))
        current = json.loads(args.current.read_text(encoding="utf-8"))
        differences = compare(base, current)
        report = {"material_change": bool(differences), "differences": differences}
        _write(args.report, report)
        print(json.dumps({"material_change": bool(differences),
                          "sections": [f"{d['node']}/{d['section']}" for d in differences]}))
        return DRIFT_EXIT if differences else 0
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    live = capture(load_inventory())
    device = compare(golden, live)
    current_render = render_digest()
    intent = golden.get("render_digest") != current_render
    report = {
        "checked_utc": live["captured_utc"],
        "golden_captured_utc": golden.get("captured_utc"),
        "device_drift": device,
        "intent_drift": {
            "detected": intent,
            "golden_render_digest": golden.get("render_digest"),
            "current_render_digest": current_render,
        },
        "result": "DRIFT" if device or intent else "CLEAN",
    }
    _write(args.report, report)
    for item in device:
        print("\n".join(item["diff"]))
    print(json.dumps({"device_drift_sections": [f"{d['node']}/{d['section']}" for d in device],
                      "intent_drift": intent, "result": report["result"]}))
    return DRIFT_EXIT if device or intent else 0


if __name__ == "__main__":
    raise SystemExit(main())
