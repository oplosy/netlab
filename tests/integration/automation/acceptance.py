#!/usr/bin/env python3
"""Live AUTO-530 acceptance for the safe change workflow and drift reports.

1. The change playbook runs precheck, backup, diff, apply, and postcheck, and
   leaves a record for each stage.
2. A second apply reports no material change.
3. Device drift (a manual nftables change on hq-fw-1) and intent drift
   (Git intent changed but not applied) each fail the drift report; removing
   the cause returns it to clean.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PLAYBOOK = ROOT / "automation" / "playbooks" / "change.yml"
HOSTS = ROOT / "automation" / "inventory" / "hosts.yml"
SNAPSHOT = ROOT / "automation" / "compliance" / "snapshot.py"
GOLDEN = ROOT / "artifacts" / "state" / "golden.json"
CHANGES = ROOT / "artifacts" / "changes"
DRIFT_TABLE = "netlab_auto530_drift_probe"


def run(*args: str, env: dict[str, str] | None = None, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False,
        env={**os.environ, **(env or {})},
    )


def change() -> Path:
    existing = set(CHANGES.glob("*")) if CHANGES.exists() else set()
    playbook = str(Path(sys.executable).with_name("ansible-playbook"))
    proc = run(playbook, "-i", str(HOSTS), str(PLAYBOOK))
    if proc.returncode:
        raise RuntimeError(f"change playbook failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-2000:]}")
    (record,) = set(CHANGES.glob("*")) - existing
    for name in ("before.json", "after.json", "material-change.json"):
        assert (record / name).is_file(), f"{record.name} has no {name}"
    return record


def drift(env: dict[str, str] | None = None) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "drift.json"
        proc = run(sys.executable, str(SNAPSHOT), "drift", "--golden", str(GOLDEN),
                   "--report", str(report), env=env)
        return proc.returncode, json.loads(report.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "evidence" / "specs" / "automation" / "change-latest.json",
    )
    args = parser.parse_args()

    first = change()
    second = change()
    second_change = json.loads((second / "material-change.json").read_text(encoding="utf-8"))
    assert second_change["material_change"] is False, (
        f"second apply changed state: {[(d['node'], d['section']) for d in second_change['differences']]}"
    )

    code, clean = drift()
    assert code == 0 and clean["result"] == "CLEAN", clean

    container = "clab-netlab-phase-1-hq-fw-1"
    subprocess.run(["docker", "exec", container, "nft", "add", "table", "inet", DRIFT_TABLE], check=True)
    try:
        code, device = drift()
    finally:
        subprocess.run(["docker", "exec", container, "nft", "delete", "table", "inet", DRIFT_TABLE],
                       check=False)
    assert code == 3 and device["result"] == "DRIFT", device
    assert [(d["node"], d["section"]) for d in device["device_drift"]] == [("hq-fw-1", "nftables")], device

    data = json.loads((ROOT / "inventory" / "inventory.yaml").read_text(encoding="utf-8"))
    link = next(item for item in data["links"] if item.get("kind") == "xfrm")
    link["ospf_cost"] = int(link.get("ospf_cost", 10)) + 5
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp)
    try:
        code, intent = drift({"NETLAB_INVENTORY": tmp.name})
    finally:
        os.unlink(tmp.name)
    assert code == 3 and intent["intent_drift"]["detected"] and not intent["device_drift"], intent

    code, restored = drift()
    assert code == 0 and restored["result"] == "CLEAN", restored

    first_change = json.loads((first / "material-change.json").read_text(encoding="utf-8"))
    result = {
        "source": "tests/integration/automation/acceptance.py",
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stages": ["precheck", "backup", "diff", "apply", "postcheck"],
        "first_change": {"record": first.name, "material_change": first_change["material_change"],
                         "sections": [f"{d['node']}/{d['section']}" for d in first_change["differences"]]},
        "second_change": {"record": second.name, "material_change": second_change["material_change"]},
        "drift_clean_after_change": clean["result"],
        "device_drift": {"injected": f"nft table {DRIFT_TABLE} on hq-fw-1", "exit_code": 3,
                         "sections": [f"{d['node']}/{d['section']}" for d in device["device_drift"]]},
        "intent_drift": {"injected": f"ospf_cost +5 on {link['id']} (uncommitted copy)", "exit_code": 3,
                         "golden_render_digest": intent["intent_drift"]["golden_render_digest"],
                         "current_render_digest": intent["intent_drift"]["current_render_digest"]},
        "drift_clean_after_restore": restored["result"],
        "result": "PASS",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
