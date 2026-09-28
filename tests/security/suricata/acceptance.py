#!/usr/bin/env python3
"""Live EDGE-420 acceptance: deterministic IDS alerts, IPS drops, fail-closed.

Requires the running project lab with the firewall policy applied (which
starts Suricata on hq-fw-1). Writes a JSON result to --output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAB = "netlab-phase-1"
FIREWALL = "hq-fw-1"
# Probes run from a DHCP-leased HQ guest client: the permitted guest DNS flow.
# A distribution SVI source is not usable because hq-fw-1 reaches the guest
# subnet over ECMP and the reply may land on the other distribution router.
GUEST_CLIENT = "hq-client-guest-1"
HELPER_IMAGE = "netlab/service:0.1.0"
SIM_DNS = "203.0.113.10"
EVE = "/var/log/suricata/eve.json"
IDS_SID, IPS_SID = 9420001, 9420002

# Runs in a helper sharing the guest client's network namespace: one UDP DNS
# A query; prints "reply" if any DNS response arrives.
DNS_PROBE = r"""
import socket, struct, sys
server, name = sys.argv[1:3]
qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
query = struct.pack("!HHHHHH", 0x4200, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(2.0)
try:
    s.sendto(query, (server, 53)); s.recvfrom(1024); print("reply")
except OSError:
    print("no-reply")
"""


def run(*args: str, check: bool = True, timeout: int = 120) -> str:
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )
    if check and result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"{result.stderr or result.stdout}"
        )
    return result.stdout


def dexec(node: str, *args: str, check: bool = True) -> str:
    return run("docker", "exec", f"clab-{LAB}-{node}", *args, check=check)


def lease_guest() -> str:
    """DHCP-lease the guest client (same helper pattern as SEC-170)."""
    return run(
        "docker", "run", "--rm", "--network", f"container:clab-{LAB}-{GUEST_CLIENT}",
        "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW", HELPER_IMAGE, "bash", "-ec",
        "ip -4 address flush dev eth1; rm -f /tmp/dhclient.eth1.leases; "
        "dhclient -4 -1 -lf /tmp/dhclient.eth1.leases eth1 >/dev/null; "
        "ip -4 -o address show dev eth1",
    )


def dns(name: str) -> bool:
    output = run(
        "docker", "run", "--rm", "--network", f"container:clab-{LAB}-{GUEST_CLIENT}",
        HELPER_IMAGE, "python3", "-c", DNS_PROBE, SIM_DNS, name,
    )
    return output.strip() == "reply"


def alerts() -> list[dict]:
    text = dexec(FIREWALL, "cat", EVE, check=False)
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event_type") == "alert":
            events.append(event)
    return events


def new_alerts(before: int, sid: int | None = None) -> list[dict]:
    time.sleep(1.5)  # eve output is flushed asynchronously
    events = alerts()[before:]
    return [e for e in events if sid is None or e["alert"]["signature_id"] == sid]


def engine_state() -> dict:
    pid = dexec(
        FIREWALL, "sh", "-c",
        "test -s /run/suricata.pid && kill -0 $(cat /run/suricata.pid) && cat /run/suricata.pid",
        check=False,
    ).strip()
    queues = dexec(FIREWALL, "cat", "/proc/net/netfilter/nfnetlink_queue", check=False)
    bound = any(line.split()[:1] == ["0"] for line in queues.splitlines())
    return {"pid": pid or None, "queue_0_bound": bound}


def check_detection_and_prevention() -> list[dict]:
    results = []
    before = len(alerts())
    assert dns("www.internet.test"), "baseline guest DNS failed through the IPS"
    assert not new_alerts(before), "benign query raised an alert"
    results.append({"case": "benign query", "reply": True, "alerts": 0})
    for attempt in (1, 2):
        before = len(alerts())
        replied = dns("ids-test.netlab")
        hits = new_alerts(before)
        assert replied, "IDS test query must still be forwarded"
        assert [e["alert"]["signature_id"] for e in hits] == [IDS_SID], hits
        assert hits[0]["alert"]["action"] == "allowed", hits[0]["alert"]
        results.append(
            {"case": f"IDS query #{attempt}", "reply": True, "sid": IDS_SID,
             "action": "allowed", "alerts": 1}
        )
        before = len(alerts())
        replied = dns("ips-block.netlab")
        hits = new_alerts(before)
        assert not replied, "IPS test query was not blocked"
        assert [e["alert"]["signature_id"] for e in hits] == [IPS_SID], hits
        assert hits[0]["alert"]["action"] == "blocked", hits[0]["alert"]
        results.append(
            {"case": f"IPS query #{attempt}", "reply": False, "sid": IPS_SID,
             "action": "blocked", "alerts": 1}
        )
    return results


def check_fail_closed() -> dict:
    state = engine_state()
    assert state["pid"] and state["queue_0_bound"], state
    dexec(FIREWALL, "kill", state["pid"])
    started = time.monotonic()
    unbound = None
    while time.monotonic() - started < 30:
        if not engine_state()["queue_0_bound"]:
            unbound = round(time.monotonic() - started, 3)
            break
        time.sleep(0.25)
    assert unbound is not None, "Suricata did not release queue 0"
    blocked = not dns("www.internet.test")
    assert blocked, "traffic passed uninspected while Suricata was down"
    # Restore through the normal, idempotent security apply.
    restore_started = time.monotonic()
    run(
        sys.executable, str(ROOT / "config" / "security" / "apply.py"),
        "--role", "firewall", timeout=300,
    )
    recovered = None
    while time.monotonic() - restore_started < 90:
        if dns("www.internet.test"):
            recovered = round(time.monotonic() - restore_started, 3)
            break
        time.sleep(1)
    assert recovered is not None, "traffic did not recover after Suricata restart"
    return {
        "queue_released_seconds": unbound,
        "traffic_blocked_while_down": blocked,
        "recovery_seconds_after_apply_start": recovered,
        "engine_after_restore": engine_state(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "evidence" / "specs" / "secure-edge" / "edge420-latest.json",
    )
    args = parser.parse_args()
    engine = engine_state()
    assert engine["pid"] and engine["queue_0_bound"], f"IPS not running: {engine}"
    guest_address = lease_guest().split()[3]
    version = dexec(FIREWALL, "suricata", "-V").strip()
    result = {
        "source": "tests/security/suricata/acceptance.py",
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine": {**engine, "version": version},
        "probe_client": {"node": GUEST_CLIENT, "address": guest_address},
        "detection_and_prevention": check_detection_and_prevention(),
        "fail_closed": check_fail_closed(),
        "result": "PASS",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
