#!/usr/bin/env python3
"""Run EDGE-430 SecureEdge correlation and write a compact tracked report.

Prerequisites: the project lab with phase-3 configuration applied and Suricata
bound on hq-fw-1. The observability stack is started if it is not running.
Full per-case records, the capture, and the nft trace stay under the ignored
artifacts/runs/phase-4 directory; the tracked outputs hold no packet payloads.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "evidence" / "reports" / "phase-4.md"
SUMMARY = ROOT / "evidence" / "specs" / "secure-edge" / "edge430-latest.json"


def observability_running() -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "netlab-observability-alloy-1"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() == "true"


def summarize(result: dict) -> dict:
    cases = []
    for case in result.get("cases", []):
        alerts = [e for e in case.get("ids", []) if e.get("event_type") == "alert"]
        cases.append(
            {
                "case": case["case"],
                "result": case["result"],
                "failures": case["failures"],
                "five_tuple": case["five_tuple"],
                "reply": case["reply"],
                "firewall": case["firewall"] and {
                    "trace_id": case["firewall"]["trace_id"],
                    "verdict": case["firewall"]["verdict"],
                },
                "ids_alerts": [
                    {"sid": a["alert"]["signature_id"], "action": a["alert"]["action"],
                     "flow_id": a.get("flow_id")}
                    for a in alerts
                ],
                "ids_event_types": sorted({e.get("event_type") for e in case.get("ids", [])}),
                "packets_with_run_id": sum(p["payload_has_run_id"] for p in case["packets"]),
                "ipfix": [
                    {"sampler": f.get("sampler_address"), "in_if": f.get("in_if"),
                     "out_if": f.get("out_if"), "packets": f.get("packets")}
                    for f in case["ipfix"]
                ],
            }
        )
    return {k: v for k, v in result.items() if k != "cases"} | {"cases": cases}


def report(summary: dict, bundle: Path) -> str:
    rows = []
    for case in summary["cases"]:
        fw = case["firewall"] or {}
        ids = ", ".join(f"{a['sid']} {a['action']}" for a in case["ids_alerts"]) or "none"
        ipfix = ", ".join(sorted({str(f["sampler"]) for f in case["ipfix"]})) or "none"
        flow = case["five_tuple"]
        key = (
            f"ICMP id {flow['sport']} → {flow['dst']}" if flow["proto"] == "ICMP"
            else f"UDP {flow['sport']} → {flow['dst']}:{flow['dport']}"
        )
        rows.append(
            f"| {case['case']} | {flow['src']} | {key} "
            f"| {fw.get('verdict', 'missing')} (trace {fw.get('trace_id', '-')}) | {ids} "
            f"| {case['packets_with_run_id']} | {ipfix} | {'yes' if case['reply'] else 'no'} "
            f"| **{case['result']}** |"
        )
    return "\n".join(
        [
            "# Phase 4 SecureEdge correlation evidence",
            "",
            f"- Result: **{summary.get('result', 'FAIL')}**",
            f"- Run ID: `{summary.get('run_id')}`",
            f"- Bundle: `{bundle.relative_to(ROOT).as_posix()}`",
            f"- Started (UTC): `{summary.get('started_utc')}`",
            "- Probe clients (DHCP): "
            + ", ".join(f"`{node}` {ip}" for node, ip in summary.get("clients", {}).items()),
            "- IPFIX sampling on hq-access-1: 1 during the run, restored to "
            f"`{summary.get('ipfix_sampling_restored')}`",
            "- Docker settings changed: **no**",
            "",
            "## Correlated cases",
            "",
            "Every record is joined by the run ID and the probe flow key (UDP source",
            "port or ICMP identifier). The run ID is also carried in each probe payload",
            "(DNS query label or ICMP payload).",
            "",
            "| Case | Client | Flow | Firewall decision | IDS alert | Packets with run ID "
            "| IPFIX sampler | Reply | Result |",
            "|---|---|---|---|---|---:|---|---|---|",
            *rows,
            "",
            "`fw-blocked` is HQ users' ICMP to the Branch 1 server subnet: the",
            "distribution SEC-170 policy permits it and hq-fw-1 denies it before the",
            "IPS queue, so it has no IDS event by design.",
            "",
        ]
    )


def main() -> int:
    if not observability_running():
        up = subprocess.run(["make", "observability-up"], cwd=ROOT, check=False)
        if up.returncode:
            print("observability-up failed", file=sys.stderr)
            return up.returncode
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "artifacts" / "runs" / "phase-4" / f"edge430-{stamp}.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "e2e" / "phase-4" / "correlate.py"),
         "--output", str(out)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    try:
        result = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return proc.returncode or 1
    summary = summarize(result)
    SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(report(summary, out.parent / result["run_id"]), encoding="utf-8")
    return 0 if summary.get("result") == "PASS" and proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
