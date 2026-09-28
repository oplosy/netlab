"""EDGE-430 offline checks for the correlation parsers and verdict rules."""

from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "edge430_correlate", ROOT / "tests" / "e2e" / "phase-4" / "correlate.py"
)
assert SPEC and SPEC.loader
correlate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(correlate)

CLIENT = "10.10.30.100"
RUN = "edge430-20260928t120000z"


def _udp_frame(src: str, dst: str, sport: int, dport: int, payload: bytes) -> bytes:
    ip = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 20 + 8 + len(payload), 1, 0, 64, 17, 0,
        bytes(int(o) for o in src.split(".")), bytes(int(o) for o in dst.split(".")),
    )
    udp = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0)
    return b"\x02" * 6 + b"\x04" * 6 + b"\x08\x00" + ip + udp + payload


def _pcap(*frames: bytes) -> bytes:
    data = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    for index, frame in enumerate(frames):
        data += struct.pack("<IIII", 1_790_000_000 + index, 0, len(frame), len(frame)) + frame
    return data


def test_dns_payload_carries_the_run_id_as_a_query_label() -> None:
    allowed, blocked, raw = (correlate.case_payload(c, RUN) for c in correlate.CASES)
    assert bytes([len(RUN)]) + RUN.encode() in allowed and b"ids-test" in allowed
    assert b"ips-block" in blocked and RUN.encode() in blocked
    assert RUN.encode() in raw


def test_pcap_parser_extracts_udp_five_tuple_and_payload() -> None:
    payload = correlate.case_payload(correlate.CASES[0], RUN)
    arp = b"\xff" * 12 + b"\x08\x06" + b"\0" * 28
    packets = correlate.parse_pcap(_pcap(arp, _udp_frame(CLIENT, "203.0.113.10", 40431, 53, payload)))
    assert len(packets) == 1
    packet = packets[0]
    assert (packet["src"], packet["dst"], packet["proto"], packet["sport"], packet["dport"]) == (
        CLIENT, "203.0.113.10", "UDP", 40431, 53)
    assert RUN.encode() in packet["payload"]


TRACE = f"""\
trace id aa11 inet netlab_edge430_trace pre packet: iif "eth3" ip saddr {CLIENT} ip daddr 203.0.113.10 ip protocol udp udp sport 40431 udp dport 53
trace id aa11 inet netlab_edge430_trace pre rule ip saddr {CLIENT} meta nftrace set 1 (verdict continue)
trace id aa11 inet netlab_sec170 forward packet: iif "eth3" oif "eth1" ip saddr {CLIENT} ip daddr 203.0.113.10 udp sport 40431 udp dport 53
trace id aa11 inet netlab_sec170 forward rule iifname {{ "eth3", "eth4" }} oifname {{ "eth1", "eth2" }} ip saddr 10.10.30.0/24 ip daddr 203.0.113.10 udp dport 53 counter packets 4 bytes 300 queue to 0 (verdict queue)
trace id bb22 inet netlab_sec170 forward packet: iif "eth3" oif "eth1" ip saddr {CLIENT} ip daddr 203.0.113.10 udp sport 40433 udp dport 9
trace id bb22 inet netlab_sec170 forward rule limit rate 10/second burst 20 packets log prefix "EDGE410|hq-fw-1|forward|deny " level warn (verdict continue)
trace id bb22 inet netlab_sec170 forward rule counter packets 9 bytes 500 drop (verdict drop)
"""


def test_trace_parser_finds_queue_and_drop_verdicts_per_source_port() -> None:
    traces = correlate.parse_trace(TRACE)
    queued = correlate.firewall_decision(traces, CLIENT, 40431, "netlab_sec170")
    dropped = correlate.firewall_decision(traces, CLIENT, 40433, "netlab_sec170")
    assert queued and queued["verdict"] == "queue" and queued["trace_id"] == "aa11"
    assert dropped and dropped["verdict"] == "drop"
    assert correlate.firewall_decision(traces, CLIENT, 40432, "netlab_sec170") is None
    assert correlate.firewall_decision(traces, "10.10.30.101", 40431, "netlab_sec170") is None


def test_eve_and_ipfix_matching_is_scoped_to_client_flow_and_run_window() -> None:
    allowed, _, fw_blocked = correlate.CASES
    eve = [
        json.dumps({"event_type": "alert", "src_ip": CLIENT, "src_port": 40431,
                    "alert": {"signature_id": 9420001, "action": "allowed"}}),
        json.dumps({"event_type": "alert", "src_ip": CLIENT, "src_port": 50000,
                    "alert": {"signature_id": 9420001, "action": "allowed"}}),
        json.dumps({"event_type": "flow", "src_ip": CLIENT, "proto": "ICMP",
                    "dest_ip": fw_blocked["dst"]}),
        "not json",
    ]
    assert len(correlate.eve_events(eve, CLIENT, allowed)) == 1
    assert len(correlate.eve_events(eve, CLIENT, fw_blocked)) == 1
    flows = [
        json.dumps({"src_addr": CLIENT, "src_port": 40431, "dst_addr": allowed["dst"],
                    "time_received_ns": 200}),
        json.dumps({"src_addr": CLIENT, "src_port": 40431, "dst_addr": allowed["dst"],
                    "time_received_ns": 50}),
        json.dumps({"src_addr": CLIENT, "proto": "ICMP", "dst_addr": fw_blocked["dst"],
                    "time_received_ns": 300}),
    ]
    assert len(correlate.ipfix_records(flows, CLIENT, allowed, since_ns=100)) == 1
    assert len(correlate.ipfix_records(flows, CLIENT, fw_blocked, since_ns=100)) == 1


def test_pcap_parser_uses_the_icmp_identifier_as_the_flow_key() -> None:
    icmp = struct.pack("!BBHHH", 8, 0, 0, 40433, 1) + f"NETLAB-EDGE430 {RUN}".encode()
    ip = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 20 + len(icmp), 1, 0, 64, 1, 0,
        bytes([10, 10, 10, 100]), bytes([10, 20, 20, 2]),
    )
    frame = b"\x02" * 6 + b"\x04" * 6 + b"\x08\x00" + ip + icmp
    (packet,) = correlate.parse_pcap(_pcap(frame))
    assert (packet["proto"], packet["sport"], packet["dport"], packet["dst"]) == (
        "ICMP", 40433, 8, "10.20.20.2")
    assert RUN.encode() in packet["payload"]


def test_trace_matches_icmp_identifier() -> None:
    trace = (
        "trace id cc33 inet netlab_sec170 forward packet: iif \"eth3\" oif \"eth1\" "
        "ip saddr 10.10.10.100 ip daddr 10.20.20.2 icmp type echo-request icmp code 0 "
        "icmp id 40433 icmp sequence 1\n"
        "trace id cc33 inet netlab_sec170 forward rule counter packets 3 bytes 90 drop (verdict drop)\n"
    )
    decision = correlate.firewall_decision(
        correlate.parse_trace(trace), "10.10.10.100", 40433, "netlab_sec170"
    )
    assert decision and decision["verdict"] == "drop"


def _record(verdict: str, alerts: list[tuple[int, str]], reply: bool, run_id: str = RUN) -> dict:
    return {
        "firewall": {"verdict": verdict},
        "ids": [{"event_type": "alert", "alert": {"signature_id": s, "action": a}} for s, a in alerts],
        "packets": [{"payload": f"x {run_id} y".encode()}],
        "ipfix": [{"src_port": 1}],
        "reply": reply,
    }


def test_evaluate_accepts_complete_correlated_cases() -> None:
    allowed, ips, fw = correlate.CASES
    assert correlate.evaluate(allowed, _record("queue", [(9420001, "allowed")], True), RUN) == []
    assert correlate.evaluate(ips, _record("queue", [(9420002, "blocked")], False), RUN) == []
    assert correlate.evaluate(fw, _record("drop", [], False), RUN) == []


def test_evaluate_rejects_missing_or_wrong_evidence() -> None:
    allowed, ips, fw = correlate.CASES
    assert correlate.evaluate(allowed, _record("queue", [], True), RUN)  # no IDS alert
    assert correlate.evaluate(ips, _record("queue", [(9420002, "allowed")], True), RUN)
    assert correlate.evaluate(fw, _record("drop", [(9420001, "allowed")], False), RUN)
    assert correlate.evaluate(fw, _record("queue", [], False), RUN)  # not denied
    no_ipfix = _record("queue", [(9420001, "allowed")], True)
    no_ipfix["ipfix"] = []
    assert correlate.evaluate(allowed, no_ipfix, RUN)
    other_run = _record("queue", [(9420001, "allowed")], True, run_id="edge430-other")
    assert correlate.evaluate(allowed, other_run, RUN)
