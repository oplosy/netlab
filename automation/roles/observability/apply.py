#!/usr/bin/env python3
"""Idempotently configure OVS IPFIX export to the OOB GoFlow2 listener."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INVENTORY = ROOT / "inventory" / "inventory.yaml"
TARGET = "172.31.255.14:2055"


def nodes() -> list[dict[str, object]]:
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    return [node for node in data["nodes"] if node.get("role") in {"edge", "dist", "access", "isp"}]


def run(node: str, *args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", "exec", f"clab-netlab-phase-1-{node}", "ovs-vsctl", *args], check=check, text=True, capture_output=True)
    return result.stdout.strip()


def plan() -> list[tuple[str, tuple[str, ...]]]:
    commands = []
    for index, node in enumerate(nodes(), start=1):
        commands.append((str(node["id"]), (
            "targets=" + json.dumps(TARGET), "sampling=400", f"obs_domain_id={index}",
            "cache_active_timeout=10", "template_interval=30")))
    return commands


def apply(check: bool) -> None:
    for node, columns in plan():
        print(f"{node}: Bridge netlab-br0 IPFIX {', '.join(columns)}")
        if check:
            continue
        current = run(node, "get", "Bridge", "netlab-br0", "ipfix")
        uuids = re.findall(r"[0-9a-fA-F-]{36}", current)
        if uuids:
            run(node, "set", "IPFIX", uuids[0], *columns)
        else:
            run(node, "--", "--id=@ipfix", "create", "IPFIX", *columns,
                "--", "set", "Bridge", "netlab-br0", "ipfix=@ipfix")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    apply(args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
