#!/usr/bin/env python3
"""Live AUTO-510 acceptance against a freshly reset NetBox (make netbox-reset netbox-up).

1. An empty NetBox database is seeded from Git intent.
2. A second reconciliation is idempotent (no changes).
3. A NetBox-only edit is reported as drift by ``sync.py --check`` and repaired
   by the next reconciliation.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SYNC = ROOT / "automation" / "netbox" / "sync.py"
sys.path.insert(0, str(ROOT / "automation" / "netbox"))
import projection  # noqa: E402
import sync  # noqa: E402


def run_sync(*args: str) -> tuple[int, dict, str]:
    proc = subprocess.run(
        [sys.executable, str(SYNC), *args], capture_output=True, text=True, check=False
    )
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    return proc.returncode, summary, proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "evidence" / "specs" / "automation" / "netbox-latest.json",
    )
    args = parser.parse_args()
    nb = sync.NetBox(sync.DEFAULT_URL, sync._token())
    version = nb.request("GET", "status/").get("netbox-version")
    want = projection.desired(projection.load_inventory())
    expected_objects = sum(len(objects) for objects in want.values())

    before = {model: len(nb.all(model)) for model in ("devices", "interfaces", "ip_addresses")}
    assert before == {"devices": 0, "interfaces": 0, "ip_addresses": 0}, (
        f"NetBox is not empty; run make netbox-reset netbox-up first: {before}"
    )

    code, seed, _ = run_sync()
    assert code == 0 and seed["by_action"]["create"] == expected_objects, seed
    code, check_after_seed, _ = run_sync("--check")
    assert code == 0 and check_after_seed["changes"] == 0, check_after_seed

    code, second, _ = run_sync()
    assert code == 0 and second["changes"] == 0, f"second sync was not idempotent: {second}"

    # NetBox-only edits: change a managed field and add an unmanaged VLAN.
    device = next(d for d in nb.all("devices") if d["name"] == "hq-fw-1")
    nb.request("PATCH", f"dcim/devices/{device['id']}/", {"description": "edited in NetBox"})
    hq = next(s for s in nb.all("sites") if s["slug"] == "hq")
    nb.request("POST", "ipam/vlans/", {"site": hq["id"], "vid": 777, "name": "ROGUE", "status": "active"})
    code, drift, drift_out = run_sync("--check")
    assert code == sync.DRIFT_EXIT, f"--check did not fail on a NetBox-only edit: {code}"
    assert drift["by_action"] == {"create": 0, "update": 1, "delete": 1}, drift
    assert "hq-fw-1" in drift_out and "hq/777" in drift_out, drift_out

    code, repair, _ = run_sync()
    assert code == 0 and repair["changes"] == 2, repair
    code, final, _ = run_sync("--check")
    assert code == 0 and final["changes"] == 0, final

    result = {
        "source": "tests/integration/netbox/acceptance.py",
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "netbox_version": version,
        "projected_objects": {model: len(objects) for model, objects in want.items()},
        "seed_from_empty": seed,
        "check_after_seed": check_after_seed,
        "second_sync": second,
        "netbox_only_edit_check": {
            "exit_code": sync.DRIFT_EXIT,
            "summary": drift,
            "detail": [line for line in drift_out.splitlines() if line.startswith(("update", "delete"))],
        },
        "repair": repair,
        "final_check": final,
        "result": "PASS",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
