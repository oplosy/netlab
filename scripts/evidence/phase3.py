#!/usr/bin/env python3
"""Build Branch 2 and capture static plus live Phase 3 acceptance evidence."""

from __future__ import annotations
import os, subprocess, sys, time
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = os.environ.get("PYTHON", sys.executable)
STAGES = [
    ("inventory", [PYTHON, "scripts/validate/validate_inventory.py"], 120),
    ("topology", [PYTHON, "scripts/lifecycle/render_topology.py", "--check"], 120),
    (
        "static_suite",
        [PYTHON, "-m", "pytest", "--import-mode=importlib", "-q", "tests"],
        600,
    ),
    ("deploy_project_lab", ["make", "lab-up"], 900),
    ("apply_gateways", [PYTHON, "config/gateway/apply.py"], 300),
    ("apply_ipsec", [PYTHON, "config/ipsec/apply.py"], 300),
    ("apply_ospf_bfd", ["bash", "automation/roles/ospf/apply.sh"], 300),
    ("apply_security", [PYTHON, "config/security/apply.py"], 300),
    ("apply_services", [PYTHON, "automation/roles/services/apply.py"], 300),
    ("reapply_services", [PYTHON, "automation/roles/services/apply.py"], 300),
    ("reapply_gateways", [PYTHON, "config/gateway/apply.py"], 300),
    ("reapply_ipsec", [PYTHON, "config/ipsec/apply.py"], 300),
    ("reapply_ospf_bfd", ["bash", "automation/roles/ospf/apply.sh"], 300),
    ("reapply_security", [PYTHON, "config/security/apply.py"], 300),
    ("branch2_live_acceptance", [PYTHON, "tests/e2e/phase-3_acceptance.py"], 1800),
]


def reused_apply_stages() -> tuple[dict[str, tuple[int, float]], str]:
    source = os.environ.get("SITE330_REUSE_APPLIED_RUN")
    if not source:
        return {}, ""
    run_dir = (ROOT / source).resolve()
    artifact_root = (ROOT / "artifacts" / "runs" / "phase-3").resolve()
    if artifact_root not in run_dir.parents:
        raise RuntimeError(
            "SITE330_REUSE_APPLIED_RUN must point inside phase-3 artifacts"
        )
    report = (ROOT / "evidence" / "reports" / "phase-3.md").read_text(encoding="utf-8")
    if f"`{run_dir.relative_to(ROOT).as_posix()}`" not in report:
        raise RuntimeError("phase-3 report does not identify the requested reuse run")
    reusable = {name for name, _command, _timeout in STAGES[3:-1]}
    rows: dict[str, tuple[int, float]] = {}
    for name, status, duration in re.findall(
        r"\| ([a-z0-9_]+) \| (PASS|FAIL) \| ([0-9.]+)s \|", report
    ):
        if name in reusable and status == "PASS":
            if not (run_dir / f"{name}.stdout.txt").is_file():
                raise RuntimeError(f"reused stage artifact is missing: {name}")
            rows[name] = (0, float(duration))
    if rows.keys() != reusable:
        raise RuntimeError(
            "the reuse run does not contain passing deploy/apply/idempotence stages"
        )
    return rows, run_dir.relative_to(ROOT).as_posix()


def main() -> int:
    inherited, inherited_from = reused_apply_stages()
    started = datetime.now(timezone.utc)
    output = (
        ROOT
        / "artifacts"
        / "runs"
        / "phase-3"
        / started.strftime("site-330-%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    stages = STAGES if not inherited else STAGES[:3] + STAGES[-1:]
    current_rows = []
    for name, command, timeout in stages:
        begin = time.monotonic()
        try:
            env = os.environ.copy()
            env["PYTHON"] = PYTHON
            proc = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = (
                exc.stdout.decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = (
                exc.stderr.decode(errors="replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            stderr += f"\nTimed out after {timeout}s\n"
            code = 124
        (output / f"{name}.stdout.txt").write_text(stdout, encoding="utf-8")
        (output / f"{name}.stderr.txt").write_text(stderr, encoding="utf-8")
        row = (name, code, round(time.monotonic() - begin, 3))
        current_rows.append(row)
        print(f"{name}: {'PASS' if code == 0 else 'FAIL'} ({row[2]}s)")
        if code:
            print(stdout, end="")
            print(stderr, file=sys.stderr, end="")
            break
    rows_by_name = {name: (code, duration) for name, code, duration in current_rows}
    rows_by_name.update(inherited)
    rows = [
        (name, *rows_by_name[name])
        for name, _command, _timeout in STAGES
        if name in rows_by_name
    ]
    passed = len(rows) == len(STAGES) and all(code == 0 for _, code, _ in rows)
    report = [
        "# Phase 3 Branch 2 Acceptance",
        "",
        f"- Result: **{'PASS' if passed else 'FAIL'}**",
        f"- Run: `{output.relative_to(ROOT).as_posix()}`",
        f"- Started (UTC): `{started.isoformat(timespec='seconds')}`",
        "- Docker/WSL daemon settings changed: **no**",
        "- Runtime action: project-scoped Containerlab deploy/reconfigure and configuration apply",
        *(
            [f"- Reused passing deploy/apply evidence from: `{inherited_from}`"]
            if inherited_from
            else []
        ),
        "",
        "## Stages",
        "",
        "| Stage | Result | Duration |",
        "|---|---|---:|",
        *[
            f"| {name} | {'PASS' if code == 0 else 'FAIL'} | {duration}s |"
            for name, code, duration in rows
        ],
        "",
        "## Acceptance",
        "",
        "- One Branch 2 summary route on HQ and a reciprocal HQ summary on Branch 2.",
        "- Guest-to-corporate/OOB and non-OOB SSH denied with counters; guest DNS/NTP and OOB controls exercised.",
        "- Branch 1 DHCP, DNS, NTP, corporate reachability, and guest-deny checks run in the same suite.",
        "",
    ]
    (ROOT / "evidence" / "reports" / "phase-3.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
