#!/usr/bin/env python3
"""Wait until Prometheus and Loki answer their readiness endpoints."""
from __future__ import annotations

import argparse
import json
import subprocess
import time

OBS_NODE = "clab-netlab-phase-1-svc-observability-1"
CHECKS = {
    "prometheus": ("http://172.31.255.14:9090/-/ready", "ready"),
    "loki": ("http://172.31.255.14:3100/ready", "ready"),
}
FETCH = "import sys,urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=2).read().decode())"


def body_is_ready(body: str, expected: str) -> bool:
    return expected.casefold() in body.casefold()


def probe() -> tuple[bool, dict[str, str]]:
    state: dict[str, str] = {}
    for service, (url, expected) in CHECKS.items():
        try:
            result = subprocess.run(
                ["docker", "exec", OBS_NODE, "python3", "-c", FETCH, url],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            body = result.stdout.strip() or result.stderr.strip()
            state[service] = body[:240]
            if result.returncode or not body_is_ready(body, expected):
                return False, state
        except (OSError, subprocess.TimeoutExpired) as exc:
            state[service] = str(exc)[:240]
            return False, state
    return True, state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    started = time.monotonic()
    deadline = started + args.timeout
    last: dict[str, str] = {}
    while time.monotonic() < deadline:
        ready, last = probe()
        if ready:
            print(json.dumps({"ready": True, "waited_seconds": round(time.monotonic() - started, 3), "services": last}, indent=2))
            return 0
        time.sleep(args.interval)
    print(json.dumps({"ready": False, "waited_seconds": round(time.monotonic() - started, 3), "services": last}, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
