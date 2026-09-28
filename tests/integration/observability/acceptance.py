#!/usr/bin/env python3
"""Live OBS-180 acceptance. Requires the phase-1 lab and observability stack."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "automation" / "roles" / "observability"))

from targets import snmp_nodes  # noqa: E402

LAB = "netlab-phase-1"
OBS_NODE = f"clab-{LAB}-svc-observability-1"
ACCESS_NODE = f"clab-{LAB}-hq-access-1"
ALLOY = "netlab-observability-alloy-1"
PROM = "http://172.31.255.14:9090"
LOKI = "http://172.31.255.14:3100"
EXPORTER = "http://172.31.255.14:9116"
CLIENT = f"clab-{LAB}-hq-client-users-1"
CLIENT_IP_RE = re.compile(r"inet (10\.10\.10\.\d+)/")


def run(*args: str, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {args[0]} {args[1]}: "
                           f"{(result.stderr or result.stdout).strip()[:300]}")
    return result


def service_http(url: str) -> str:
    code = "import sys,urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=8).read().decode())"
    return run("docker", "exec", OBS_NODE, "python3", "-c", code, url, timeout=15).stdout.strip()


def prom_query(expression: str) -> list[dict[str, Any]]:
    url = PROM + "/api/v1/query?" + urllib.parse.urlencode({"query": expression})
    return json.loads(service_http(url))["data"]["result"]


def loki_query(expression: str, start_ns: int) -> list[dict[str, Any]]:
    params = {
        "query": expression,
        "limit": "20",
        "start": str(start_ns),
        "end": str(time.time_ns()),
    }
    url = LOKI + "/loki/api/v1/query_range?" + urllib.parse.urlencode(params)
    return json.loads(service_http(url))["data"]["result"]


def wait_for(description: str, predicate: Any, timeout: int = 45) -> Any:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)[:160]
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for {description}" + (f": {last_error}" if last_error else ""))


def send_syslog(message: str, timestamp: dt.datetime) -> None:
    stamp = timestamp.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wire = f"<134>1 {stamp} hq-access-1 obs-acceptance 480 - - {message}"
    code = (
        "import socket,sys; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
        "s.sendto(sys.argv[1].encode(), ('172.31.255.14',514)); s.close()"
    )
    run("docker", "exec", OBS_NODE, "python3", "-c", code, wire)


def ber_length(length: int) -> bytes:
    if length < 128:
        return bytes((length,))
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes((0x80 | len(encoded),)) + encoded


def ber(tag: int, value: bytes) -> bytes:
    return bytes((tag,)) + ber_length(len(value)) + value


def ber_integer(value: int) -> bytes:
    body = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big", signed=True)
    while len(body) > 1 and body[0] == 0 and body[1] < 128:
        body = body[1:]
    return ber(0x02, body)


def ber_oid(arcs: tuple[int, ...]) -> bytes:
    encoded = bytearray((40 * arcs[0] + arcs[1],))
    for arc in arcs[2:]:
        parts = [arc & 0x7F]
        arc >>= 7
        while arc:
            parts.append(0x80 | (arc & 0x7F))
            arc >>= 7
        encoded.extend(reversed(parts))
    return ber(0x06, bytes(encoded))


def assert_v2c_denied() -> None:
    variable = ber(0x30, ber_oid((1, 3, 6, 1, 2, 1, 1, 5, 0)) + ber(0x05, b""))
    bindings = ber(0x30, variable)
    pdu = ber(0xA0, ber_integer(1) + ber_integer(0) + ber_integer(0) + bindings)
    packet = ber(0x30, ber_integer(1) + ber(0x04, b"public") + pdu)
    remote_code = (
        "import socket,sys; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(1.5); "
        "s.sendto(bytes.fromhex(sys.argv[1]),('172.31.255.30',161)); "
        "\ntry: s.recvfrom(2048)\nexcept TimeoutError: print('denied')\nelse: raise SystemExit('v2c response received')"
    )
    result = run("docker", "exec", OBS_NODE, "python3", "-c", remote_code, packet.hex(), check=False)
    if result.returncode or result.stdout.strip() != "denied":
        raise RuntimeError("SNMPv2c public community received a response")


def generate_known_flows(service_image: str) -> tuple[str, int]:
    started_ns = time.time_ns()
    script = "\n".join([
        "set -e",
        "ip -4 address flush dev eth1",
        "dhclient -4 -1 -q -lf /tmp/obs-acceptance.leases eth1",
        "trap 'dhclient -4 -r -lf /tmp/obs-acceptance.leases eth1 >/dev/null 2>&1 || true; ip -4 address flush dev eth1' EXIT",
        "ip -4 -o address show dev eth1",
        "for i in $(seq 1 1000); do dig +time=1 +tries=1 +short @10.10.20.11 www.internet.test A >/dev/null; done",
    ])
    result = run("docker", "run", "--rm", "--network", f"container:{CLIENT}", "--cap-add", "NET_ADMIN",
                 service_image, "bash", "-ec", script, timeout=120)
    match = CLIENT_IP_RE.search(result.stdout)
    if not match:
        raise RuntimeError("HQ users DHCP helper did not receive an address")
    return match.group(1), started_ns


def matching_flows(client_ip: str, started_ns: int) -> list[dict[str, Any]]:
    filters = (
        f"grep -F '\"src_addr\":\"{client_ip}\"' /var/lib/netlab/flows/ipfix.json | "
        "grep -F '\"dst_addr\":\"10.10.20.11\"' | grep -F '\"dst_port\":53' | tail -n 50"
    )
    result = run("docker", "exec", ALLOY, "sh", "-lc", filters, check=False, timeout=20)
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        try:
            flow = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (flow.get("time_received_ns", 0) >= started_ns
                and flow.get("proto") == "UDP"
                and flow.get("src_addr") == client_ip
                and flow.get("dst_addr") == "10.10.20.11"
                and flow.get("dst_port") == 53
                and flow.get("in_if") and flow.get("out_if")):
            rows.append(flow)
    return rows


def interface_name(address: str, index: int) -> str:
    series = prom_query(f'ifOperStatus{{instance="{address}",ifIndex="{index}"}}')
    return series[0]["metric"].get("ifName", "") if series else ""


def assert_syslog_loki() -> str:
    marker = "OBS180-" + os.urandom(8).hex()
    now = dt.datetime.now(dt.timezone.utc)
    send_syslog(f"{marker} acceptance=udp-syslog pipeline=test", now)
    expression = '{job="syslog"} |= "' + marker + '"'
    streams = wait_for("syslog marker in Loki", lambda: loki_query(expression, time.time_ns() - 120_000_000_000), 35)
    if not any(marker in line for stream in streams for _, line in stream.get("values", [])):
        raise RuntimeError("Loki returned streams without the test marker")
    return marker


def link_event_acceptance() -> tuple[str, float]:
    expression = 'ifOperStatus{instance="172.31.255.33",ifName="eth5"}'
    before = prom_query(expression)
    if not before or before[0]["value"][1] != "1":
        raise RuntimeError("HQ access eth5 was not operational before the test")
    run("docker", "exec", ACCESS_NODE, "ip", "link", "set", "dev", "eth5", "down")
    try:
        down = wait_for("SNMP ifOperStatus=down for hq-access-1 eth5",
                        lambda: (lambda values: values[0] if values and values[0]["value"][1] == "2" else None)(prom_query(expression)),
                        45)
        sample_time = float(down["value"][0])
        event_time = dt.datetime.fromtimestamp(sample_time, tz=dt.timezone.utc)
        marker = "OBS180-LINK-" + os.urandom(6).hex()
        send_syslog(f"{marker} event=link-state node=hq-access-1 ifName=eth5 state=down", event_time)
        streams = wait_for("link event marker in Loki",
                           lambda: loki_query('{job="syslog"} |= "' + marker + '"', time.time_ns() - 120_000_000_000),
                           35)
        found = [(int(ts), line) for stream in streams for ts, line in stream.get("values", []) if marker in line]
        if not found:
            raise RuntimeError("link state log event was not found in Loki")
        log_ns, _ = found[0]
        if abs(log_ns - int(sample_time * 1_000_000_000)) > 30_000_000_000:
            raise RuntimeError("link metric and log timestamps differ by more than 30 seconds")
        return marker, sample_time
    finally:
        run("docker", "exec", ACCESS_NODE, "ip", "link", "set", "dev", "eth5", "up")
        wait_for("SNMP ifOperStatus=up after link restore",
                 lambda: bool((values := prom_query(expression)) and values[0]["value"][1] == "1"), 45)


def main() -> int:
    versions = {}
    for line in (ROOT / "versions.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            versions[key] = value
    image = os.environ.get("NETLAB_SERVICE_IMAGE", versions.get("NETLAB_SERVICE_IMAGE", "netlab/service:0.1.0"))

    for path in ("/-/ready",):
        if "Ready" not in service_http(PROM + path):
            raise RuntimeError("Prometheus is not ready")
    if "ready" not in service_http(LOKI + "/ready"):
        raise RuntimeError("Loki is not ready")

    expected_targets = set(snmp_nodes().values())
    snmp_targets = prom_query('up{job="snmp"}')
    healthy = {row["metric"]["instance"] for row in snmp_targets if row["value"][1] == "1"}
    if healthy != expected_targets or len(snmp_targets) != len(expected_targets):
        missing = ", ".join(sorted(expected_targets - healthy)) or "none"
        raise RuntimeError(f"SNMP scrape targets are not all healthy ({len(healthy)}/"
                           f"{len(expected_targets)} healthy; missing or down: {missing})")
    goflow_targets = prom_query('up{job="goflow2"}')
    if len(goflow_targets) != 1 or goflow_targets[0]["value"][1] != "1":
        raise RuntimeError("GoFlow2 metrics target is not healthy")
    interface_metrics = prom_query("ifOperStatus")
    if not interface_metrics or not any("ifName" in row["metric"] for row in interface_metrics):
        raise RuntimeError("SNMP interface metrics or ifName lookup labels are missing")
    exporter = service_http(EXPORTER + "/snmp?auth=netlab&module=if_mib&target=172.31.255.30")
    if "ifOperStatus{" not in exporter or "ifName=" not in exporter:
        raise RuntimeError("SNMPv3 exporter did not return interface metrics with ifName")
    assert_v2c_denied()
    print(f"SNMPv3 authPriv metrics: {len(expected_targets)} targets and interface labels PASS; "
          "SNMPv2c denied PASS")

    client_ip, started_ns = generate_known_flows(image)
    flows = wait_for("sampled IPFIX flow from HQ client to site DNS",
                     lambda: matching_flows(client_ip, started_ns), 45)
    inventory = json.loads((ROOT / "inventory" / "inventory.yaml").read_text(encoding="utf-8"))
    address_to_node = {node["oob"].split("/")[0]: node["id"] for node in inventory["nodes"] if node.get("oob")}
    flow = next((row for row in flows if row.get("sampler_address") in address_to_node
                 and interface_name(row["sampler_address"], row["in_if"])
                 and interface_name(row["sampler_address"], row["out_if"])), None)
    if flow is None:
        raise RuntimeError("IPFIX flow had no matching SNMP interface names")
    sampler = address_to_node[flow["sampler_address"]]
    in_name = interface_name(flow["sampler_address"], flow["in_if"])
    out_name = interface_name(flow["sampler_address"], flow["out_if"])
    print(f"IPFIX known flow: {client_ip} -> 10.10.20.11:53 at {sampler} "
          f"{in_name} (ifIndex {flow['in_if']}) -> {out_name} (ifIndex {flow['out_if']}): PASS")

    syslog_marker = assert_syslog_loki()
    print(f"RFC5424 UDP syslog -> Loki marker {syslog_marker}: PASS")
    link_marker, sample_time = link_event_acceptance()
    print(f"Link metric/log correlation hq-access-1 eth5 at {sample_time:.3f} UTC "
          f"(Loki marker {link_marker}); link restored: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError) as exc:
        print(f"OBS-180 live acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
