#!/usr/bin/env python3
"""Bring up every inventory-defined site overlay and wait for installed CHILD SAs.

The swanctl connections have no start_action, so only overlays that a suite
initiates carry traffic. OSPF acceptance expects adjacencies on every XFRM
link (WAN-230), so the phase-1 flow initiates each overlay from its HQ edge.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure import _exec, _sa_output, _wait_sa, build_plan, load_inventory, sa_state


def initiators(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the HQ-side peer of each XFRM link."""
    result = [peer for peer in plan["peers"] if peer["site"] == "hq"]
    links = [peer["link"] for peer in result]
    if len(links) != len(set(links)) or set(links) != {peer["link"] for peer in plan["peers"]}:
        raise ValueError("every XFRM link needs exactly one HQ edge endpoint")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=Path(__file__).resolve().parents[3] / "inventory" / "inventory.yaml")
    parser.add_argument("--docker", default="docker")
    args = parser.parse_args()
    try:
        plan = build_plan(load_inventory(args.inventory))
        peers = {(peer["node"], peer["connection"]): peer for peer in plan["peers"]}
        for hq in initiators(plan):
            remote = peers[(hq["remote_node"], hq["connection"])]
            if all(sa_state(_sa_output(args.docker, hq), hq["connection"])):
                print(f"{hq['connection']}: already installed")
            else:
                initiated = _exec(
                    args.docker, hq["container"], "swanctl", "--initiate", "--child", hq["connection"], check=False
                )
                if initiated.returncode:
                    print(f"{hq['connection']}: initiate returned {initiated.returncode}; checking SA state")
            for peer in (hq, remote):
                _wait_sa(args.docker, peer, True)
            print(f"{hq['connection']}: installed on {hq['node']} and {remote['node']}")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, TimeoutError, RuntimeError) as exc:
        print(f"IPsec overlay establishment failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
