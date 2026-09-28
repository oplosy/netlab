#!/usr/bin/env python3
"""Wait for both remote site summaries to reach their distribution routers."""
from __future__ import annotations

import argparse
import json
import subprocess
import time

ROUTE_CHECKS = {
    "hq-dist-1": "10.20.0.0/16",
    "br1-dist-1": "10.10.0.0/16",
}


def route_is_present(output: str, prefix: str) -> bool:
    return prefix in output and "% Network not in table" not in output


def probe_routes() -> tuple[bool, dict[str, dict[str, str]]]:
    state: dict[str, dict[str, str]] = {}
    for node, prefix in ROUTE_CHECKS.items():
        result = subprocess.run(
            ["docker", "exec", f"clab-netlab-phase-1-{node}", "vtysh", "-c", f"show ip route {prefix}"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        output = result.stdout.strip() or result.stderr.strip()
        state[node] = {"prefix": prefix, "output": output}
    return all(route_is_present(item["output"], item["prefix"]) for item in state.values()), state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    started = time.monotonic()
    deadline = started + args.timeout
    last: dict[str, dict[str, str]] = {}
    while time.monotonic() < deadline:
        ready, last = probe_routes()
        if ready:
            print(json.dumps({"ready": True, "waited_seconds": round(time.monotonic() - started, 3), "routes": last}, indent=2))
            return 0
        time.sleep(args.interval)
    print(json.dumps({"ready": False, "waited_seconds": round(time.monotonic() - started, 3), "routes": last}, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
