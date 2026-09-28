#!/usr/bin/env python3
"""EDGE-430: correlate SecureEdge decisions with packets, IDS events, and IPFIX.

One run ID ties three probes from a DHCP-leased HQ guest client together:

- ``allowed``: DNS query ``<run>.ids-test.netlab`` (firewall queues it, the IPS
  alerts and forwards it, the resolver answers).
- ``ips-blocked``: DNS query ``<run>.ips-block.netlab`` (firewall queues it, the
  IPS drops it).
- ``fw-blocked``: ICMP echo carrying the run ID from an HQ *users* client to
  the Branch 1 server subnet. The distribution SEC-170 policy permits users'
  ICMP to remote site aggregates, but hq-fw-1 only permits it to remote user
  subnets, so the firewall itself denies it before the IPS; no IDS event is
  expected. (A guest probe would be denied earlier, at the distribution.)

Each probe uses a fixed source port (UDP) or ICMP identifier. For every case
the run collects four records and joins them by run ID and flow key:

- firewall decision: ``nft monitor trace`` on hq-fw-1, for this client only;
- IDS event: Suricata ``eve.json`` on hq-fw-1;
- packet: AF_PACKET capture on hq-fw-1's distribution-facing interfaces;
- IPFIX record: OVS on hq-access-1 through GoFlow2 in the observability stack.
  Sampling on hq-access-1 is set to 1 for the run and restored afterwards.

Needs the running project lab (phase-3 applied, Suricata bound) and
``make observability-up``.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import struct
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
LAB = "netlab-phase-1"
FIREWALL = "hq-fw-1"
ACCESS = "hq-access-1"
GUEST = "hq-client-guest-1"
USERS = "hq-client-users-1"
ALLOY = "netlab-observability-alloy-1"
HELPER_IMAGE = "netlab/service:0.1.0"
SIM_DNS = "203.0.113.10"
BR1_SERVERS_SVI = "10.20.20.2"
CAPTURE_SCRIPT = ROOT / "tests" / "integration" / "ipsec" / "capture.py"
FIREWALL_INSIDE = ("eth3", "eth4")  # hq-fw-1 links to hq-dist-1/2 (ADR 0017)
TRACE_TABLE = "netlab_edge430_trace"
IDS_SID, IPS_SID = 9420001, 9420002

CASES = [
    # sport is the UDP source port or, for ICMP, the echo identifier.
    {"name": "allowed", "client": GUEST, "proto": "UDP", "dst": SIM_DNS,
     "sport": 40431, "dport": 53, "kind": "dns-ids", "reply": True},
    {"name": "ips-blocked", "client": GUEST, "proto": "UDP", "dst": SIM_DNS,
     "sport": 40432, "dport": 53, "kind": "dns-ips", "reply": False},
    {"name": "fw-blocked", "client": USERS, "proto": "ICMP", "dst": BR1_SERVERS_SVI,
     "sport": 40433, "dport": 0, "kind": "raw", "reply": False},
]


# --------------------------------------------------------------------------
# Pure helpers (unit tested in tests/unit/evidence/test_edge430_correlation.py)
# --------------------------------------------------------------------------

def dns_query(name: str, query_id: int = 0x4300) -> bytes:
    labels = b"".join(bytes([len(part)]) + part.encode() for part in name.split("."))
    return (
        struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
        + labels
        + b"\0"
        + struct.pack("!HH", 1, 1)
    )


def case_payload(case: dict[str, Any], run_id: str) -> bytes:
    if case["kind"] == "dns-ids":
        return dns_query(f"{run_id}.ids-test.netlab")
    if case["kind"] == "dns-ips":
        return dns_query(f"{run_id}.ips-block.netlab")
    return f"NETLAB-EDGE430 {run_id}".encode()


def parse_pcap(data: bytes) -> list[dict[str, Any]]:
    """IPv4 UDP/TCP/ICMP packets from a classic little-endian Ethernet pcap.

    For ICMP, ``sport`` is the echo identifier and ``dport`` the ICMP type.
    """
    if len(data) < 24 or struct.unpack("<I", data[:4])[0] != 0xA1B2C3D4:
        raise ValueError("not a little-endian microsecond pcap")
    packets: list[dict[str, Any]] = []
    offset = 24
    while offset + 16 <= len(data):
        seconds, micros, captured, _ = struct.unpack("<IIII", data[offset:offset + 16])
        frame = data[offset + 16:offset + 16 + captured]
        offset += 16 + captured
        if len(frame) < 34 or frame[12:14] != b"\x08\x00":
            continue
        ip = frame[14:]
        header = (ip[0] & 0x0F) * 4
        proto = ip[9]
        if proto not in (1, 6, 17) or len(ip) < header + 8:
            continue
        if proto == 1:
            icmp_type = ip[header]
            (identifier,) = struct.unpack("!H", ip[header + 4:header + 6])
            sport, dport, body = identifier, icmp_type, ip[header + 8:]
        else:
            sport, dport = struct.unpack("!HH", ip[header:header + 4])
            body = ip[header + 8:] if proto == 17 else ip[header + (ip[header + 12] >> 4) * 4:]
        packets.append(
            {
                "time": seconds + micros / 1_000_000,
                "src": str(ipaddress.IPv4Address(ip[12:16])),
                "dst": str(ipaddress.IPv4Address(ip[16:20])),
                "proto": {1: "ICMP", 6: "TCP", 17: "UDP"}[proto],
                "sport": sport,
                "dport": dport,
                "payload": body,
            }
        )
    return packets


TRACE_LINE = re.compile(r"^trace id (\S+) (\S+) (\S+) (\S+) (.*)$")


def parse_trace(text: str) -> dict[str, list[dict[str, str]]]:
    """Group ``nft monitor trace`` lines by trace id."""
    traces: dict[str, list[dict[str, str]]] = {}
    for line in text.splitlines():
        match = TRACE_LINE.match(line.strip())
        if not match:
            continue
        trace_id, family, table, chain, rest = match.groups()
        traces.setdefault(trace_id, []).append(
            {"family": family, "table": table, "chain": chain, "event": rest}
        )
    return traces


def firewall_decision(
    traces: dict[str, list[dict[str, str]]], client: str, sport: int, table: str
) -> dict[str, Any] | None:
    """The forward-chain verdict of ``table`` for the client's packet from sport."""
    for trace_id, events in traces.items():
        packet = next(
            (
                e["event"]
                for e in events
                if e["event"].startswith("packet:")
                and f"ip saddr {client} " in e["event"] + " "
                and re.search(rf"\b(?:(?:udp|tcp) sport|icmp id) {sport}\b", e["event"])
            ),
            None,
        )
        if packet is None:
            continue
        verdicts = [
            e["event"]
            for e in events
            if e["table"] == table and e["chain"] == "forward"
            and re.search(r"\(verdict (\w+)\)|^policy (\w+)", e["event"])
        ]
        final = None
        for event in verdicts:
            match = re.search(r"\(verdict (\w+)\)", event) or re.search(r"^policy (\w+)", event)
            if match and match.group(1) != "continue":
                final = match.group(1)
        if final:
            return {"trace_id": trace_id, "verdict": final, "packet": packet,
                    "rules": verdicts}
    return None


def eve_events(lines: list[str], client: str, case: dict[str, Any]) -> list[dict[str, Any]]:
    """Suricata events for the case flow (ICMP has no ports: match proto + dst)."""
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("src_ip") != client:
            continue
        if case["proto"] == "ICMP":
            if event.get("proto") == "ICMP" and event.get("dest_ip") == case["dst"]:
                events.append(event)
        elif event.get("src_port") == case["sport"]:
            events.append(event)
    return events


def ipfix_records(
    lines: list[str], client: str, case: dict[str, Any], since_ns: int
) -> list[dict[str, Any]]:
    records = []
    for line in lines:
        try:
            flow = json.loads(line)
        except json.JSONDecodeError:
            continue
        if flow.get("src_addr") != client or flow.get("time_received_ns", 0) < since_ns:
            continue
        if case["proto"] == "ICMP":
            if flow.get("proto") == "ICMP" and flow.get("dst_addr") == case["dst"]:
                records.append(flow)
        elif flow.get("src_port") == case["sport"] and flow.get("dst_addr") == case["dst"]:
            records.append(flow)
    return records


def evaluate(case: dict[str, Any], record: dict[str, Any], run_id: str) -> list[str]:
    """Return failed expectations for one correlated case (empty = pass)."""
    failures = []
    fw = record.get("firewall")
    expected_verdict = "drop" if case["name"] == "fw-blocked" else "queue"
    if not fw or fw["verdict"] != expected_verdict:
        failures.append(f"firewall verdict {fw and fw['verdict']} != {expected_verdict}")
    alerts = [e for e in record.get("ids", []) if e.get("event_type") == "alert"]
    if case["name"] == "allowed":
        if [(a["alert"]["signature_id"], a["alert"]["action"]) for a in alerts] != [(IDS_SID, "allowed")]:
            failures.append(f"IDS alerts {alerts} != one allowed {IDS_SID}")
    elif case["name"] == "ips-blocked":
        if [(a["alert"]["signature_id"], a["alert"]["action"]) for a in alerts] != [(IPS_SID, "blocked")]:
            failures.append(f"IDS alerts {alerts} != one blocked {IPS_SID}")
    elif record.get("ids"):
        failures.append("firewall-denied traffic reached the IPS")
    if not any(run_id.encode() in packet["payload"] for packet in record.get("packets", [])):
        failures.append("no captured packet carries the run ID")
    if not record.get("ipfix"):
        failures.append("no IPFIX record for the probe 5-tuple")
    if record.get("reply") != case["reply"]:
        failures.append(f"reply {record.get('reply')} != {case['reply']}")
    return failures


# --------------------------------------------------------------------------
# Live collection
# --------------------------------------------------------------------------

def run(*args: str, check: bool = True, timeout: int = 120) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def dexec(node: str, *args: str, check: bool = True) -> str:
    return run("docker", "exec", f"clab-{LAB}-{node}", *args, check=check)


def in_client(client: str, *args: str, timeout: int = 90) -> str:
    """Run a helper container in an endpoint client's network namespace."""
    return run(
        "docker", "run", "--rm", "--network", f"container:clab-{LAB}-{client}",
        "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW", HELPER_IMAGE, *args,
        timeout=timeout,
    )


def lease(client: str, subnet: str) -> str:
    output = in_client(
        client, "bash", "-ec",
        "ip -4 address flush dev eth1; rm -f /tmp/edge430.leases; "
        "dhclient -4 -1 -lf /tmp/edge430.leases eth1 >/dev/null; ip -4 -o address show dev eth1",
    )
    match = re.search(rf"inet ({re.escape(subnet)}\.\d+)/", output)
    if not match:
        raise RuntimeError(f"{client} did not lease an address in {subnet}.0/24: {output}")
    return match.group(1)


# UDP probes bind the source port; ICMP datagram sockets bind the identifier.
PROBE = r"""
import json, socket, struct, sys
results = {}
for spec in json.loads(sys.argv[1]):
    payload = bytes.fromhex(spec["payload"])
    if spec["proto"] == "ICMP":
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
        s.bind(("", spec["sport"]))
        packet = struct.pack("!BBHHH", 8, 0, 0, 0, 1) + payload
        destination = (spec["dst"], 0)
    else:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("", spec["sport"]))
        packet, destination = payload, (spec["dst"], spec["dport"])
    s.settimeout(2.0)
    s.sendto(packet, destination)
    try:
        s.recvfrom(2048); results[spec["name"]] = True
    except OSError:
        results[spec["name"]] = False
    s.close()
print(json.dumps(results))
"""


def ipfix_sampling(value: str | None = None) -> str:
    uuid = dexec(ACCESS, "ovs-vsctl", "get", "Bridge", "netlab-br0", "ipfix").strip()
    if not re.fullmatch(r"[0-9a-f-]{36}", uuid):
        raise RuntimeError(f"{ACCESS} has no IPFIX exporter: {uuid!r}")
    current = dexec(ACCESS, "ovs-vsctl", "get", "IPFIX", uuid, "sampling").strip()
    if value is not None:
        dexec(ACCESS, "ovs-vsctl", "set", "IPFIX", uuid, f"sampling={value}")
    return current


def collect(run_id: str, out_dir: Path) -> dict[str, Any]:
    addresses = {GUEST: lease(GUEST, "10.10.30"), USERS: lease(USERS, "10.10.10")}
    eve_offset = len(dexec(FIREWALL, "cat", "/var/log/suricata/eve.json", check=False).splitlines())
    started_ns = time.time_ns()
    original_sampling = ipfix_sampling("1")
    dexec(FIREWALL, "mkdir", "-p", "/tmp/edge430")
    run("docker", "cp", str(CAPTURE_SCRIPT), f"clab-{LAB}-{FIREWALL}:/tmp/edge430/capture.py")
    try:
        dexec(FIREWALL, "nft", "add", "table", "inet", TRACE_TABLE)
        dexec(
            FIREWALL, "nft", "add", "chain", "inet", TRACE_TABLE, "pre",
            "{ type filter hook prerouting priority -350; policy accept; }",
        )
        dexec(FIREWALL, "nft", "add", "rule", "inet", TRACE_TABLE, "pre",
              "ip", "saddr", "{", ", ".join(sorted(addresses.values())), "}",
              "meta", "nftrace", "set", "1")
        dexec(FIREWALL, "sh", "-c",
              "timeout 40 nft monitor trace > /tmp/edge430/trace.txt 2>&1 &")
        dexec(FIREWALL, "sh", "-c",
              "python3 /tmp/edge430/capture.py /tmp/edge430/capture.pcap 30 "
              f"{' '.join(FIREWALL_INSIDE)} > /tmp/edge430/capture.json 2>&1 &")
        time.sleep(2)
        replies: dict[str, bool] = {}
        for client in (GUEST, USERS):
            specs = [
                {"name": c["name"], "proto": c["proto"], "dst": c["dst"],
                 "sport": c["sport"], "dport": c["dport"],
                 "payload": case_payload(c, run_id).hex()}
                for c in CASES
                if c["client"] == client
            ]
            replies |= json.loads(in_client(client, "python3", "-c", PROBE, json.dumps(specs)))
        time.sleep(3)
    finally:
        dexec(FIREWALL, "nft", "delete", "table", "inet", TRACE_TABLE, check=False)
        dexec(FIREWALL, "pkill", "-f", "nft monitor trace", check=False)
    # Wait for the IPFIX export while sampling is still 1, then restore it.
    try:
        deadline = time.monotonic() + 60
        flows: list[str] = []
        pattern = "|".join(f'"src_addr":"{a}"' for a in addresses.values())
        while time.monotonic() < deadline:
            flows = run("docker", "exec", ALLOY, "sh", "-c",
                        f"grep -E '{pattern}' /var/lib/netlab/flows/ipfix.json",
                        check=False).splitlines()
            if all(
                ipfix_records(flows, addresses[c["client"]], c, started_ns) for c in CASES
            ):
                break
            time.sleep(2)
    finally:
        ipfix_sampling(original_sampling)
    time.sleep(max(0.0, 32 - (time.time_ns() - started_ns) / 1e9))  # capture window
    out_dir.mkdir(parents=True, exist_ok=True)
    run("docker", "cp", f"clab-{LAB}-{FIREWALL}:/tmp/edge430/capture.pcap", str(out_dir / "capture.pcap"))
    trace_text = dexec(FIREWALL, "cat", "/tmp/edge430/trace.txt", check=False)
    (out_dir / "nft-trace.txt").write_text(trace_text, encoding="utf-8")
    eve_lines = dexec(FIREWALL, "cat", "/var/log/suricata/eve.json", check=False).splitlines()[eve_offset:]
    packets = parse_pcap((out_dir / "capture.pcap").read_bytes())
    traces = parse_trace(trace_text)
    cases = []
    for case in CASES:
        client = addresses[case["client"]]
        record = {
            "run_id": run_id,
            "case": case["name"],
            "five_tuple": {"src": client, "sport": case["sport"], "dst": case["dst"],
                           "dport": case["dport"], "proto": case["proto"]},
            "reply": replies.get(case["name"]),
            "firewall": firewall_decision(traces, client, case["sport"], "netlab_sec170"),
            "ids": eve_events(eve_lines, client, case),
            "packets": [
                p for p in packets
                if p["src"] == client and p["proto"] == case["proto"]
                and p["sport"] == case["sport"] and p["dst"] == case["dst"]
            ],
            "ipfix": ipfix_records(flows, client, case, started_ns),
        }
        failures = evaluate(case, record, run_id)
        record["packets"] = [
            {**p, "payload_has_run_id": run_id.encode() in p["payload"], "payload": p["payload"].hex()}
            for p in record["packets"]
        ]
        record["result"] = "PASS" if not failures else "FAIL"
        record["failures"] = failures
        cases.append(record)
    return {
        "run_id": run_id,
        "clients": addresses,
        "ipfix_sampling_during_run": 1,
        "ipfix_sampling_restored": original_sampling,
        "cases": cases,
        "result": "PASS" if all(c["result"] == "PASS" for c in cases) else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_id = "edge430-" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz")
    result = {
        "source": "tests/e2e/phase-4/correlate.py",
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **collect(run_id, args.output.parent / run_id),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=2))
    for case in result["cases"]:
        print(f"{case['case']}: {case['result']} {case['failures']}")
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
