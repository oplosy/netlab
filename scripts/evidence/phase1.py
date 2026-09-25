#!/usr/bin/env python3
"""Run Phase 1 acceptance in order and retain a timestamped result bundle."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_OUTPUTS = (
    Path("evidence/specs/ospf/latest.json"),
    Path("evidence/specs/services/latest.md"),
    Path("evidence/specs/security/latest.md"),
)
TIMEOUT_SECONDS = 900
PYTHON_COMMAND = shlex.split(os.environ.get("NETLAB_CHILD_PYTHON", "uv run --locked python"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def snapshot_outputs() -> dict[Path, bytes | None]:
    return {path: (ROOT / path).read_bytes() if (ROOT / path).is_file() else None for path in RUNTIME_OUTPUTS}


def restore_outputs(snapshot: dict[Path, bytes | None]) -> None:
    for relative, content in snapshot.items():
        path = ROOT / relative
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


class EvidenceRun:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.stages: list[dict[str, Any]] = []
        self.thresholds: list[dict[str, Any]] = []
        self.evidence_files: list[str] = []
        self.failed = False

    def run(
        self,
        name: str,
        command: list[str],
        timeout: int = TIMEOUT_SECONDS,
        expect_empty_output: bool = False,
        required: bool = True,
        extra_env: dict[str, str] | None = None,
        record_stage: bool = True,
    ) -> dict[str, Any]:
        started = time.monotonic()
        record: dict[str, Any] = {
            "name": name, "command": " ".join(command), "started_at_utc": utc_now(), "status": "fail", "required": required
        }
        try:
            env = os.environ.copy()
            # Bash apply scripts treat PYTHON as one executable path, not a shell command.
            env["PYTHON"] = PYTHON_COMMAND[-1]
            env.update(extra_env or {})
            result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout, check=False)
            stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            stderr += f"\ncommand timed out after {timeout} seconds\n"
            returncode = 124
        except KeyboardInterrupt:
            stdout, stderr, returncode = "", "command interrupted by operator\n", 130
        except OSError as exc:
            stdout, stderr, returncode = "", f"could not start command: {exc}\n", 127
        if returncode == 0 and expect_empty_output and stdout.strip():
            stderr += f"expected no project resources; command returned:\n{stdout}"
            returncode = 1

        stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        stdout_path = self.output_dir / f"{stem}.stdout.txt"
        stderr_path = self.output_dir / f"{stem}.stderr.txt"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        record.update({
            "finished_at_utc": utc_now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "return_code": returncode,
            "status": "pass" if returncode == 0 else ("fail" if required else "warning"),
            "stdout_file": stdout_path.name,
            "stderr_file": stderr_path.name,
        })
        if record_stage:
            self.stages.append(record)
        self.failed |= required and returncode != 0
        return record

    def threshold(self, name: str, actual: float | int | bool, operator: str, limit: float | int | bool) -> None:
        passed = {"<=": actual <= limit, ">=": actual >= limit, "==": actual == limit}[operator]
        self.thresholds.append({"name": name, "actual": actual, "operator": operator, "limit": limit, "passed": passed})
        self.failed |= not passed


def evaluate_l2(run: EvidenceRun) -> None:
    output = (run.output_dir / "test-l2.stdout.txt").read_text(encoding="utf-8")
    rows = re.findall(
        r"(?P<site>hq|br1) (?P<mode>lacp|rstp): .*?max_reply_gap=(?P<gap>[0-9.]+)s limit=(?P<limit>[0-9.]+)s",
        output,
    )
    run.threshold("l2_failover_measurements_present", len(rows), "==", 4)
    for site, mode, gap, limit in rows:
        run.threshold(f"l2_{site}_{mode}_max_reply_gap_seconds", float(gap), "<=", float(limit))


def evaluate_ospf(run: EvidenceRun, result_path: Path, run_number: int) -> bool:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        bfd = result["bfd_test"]
        gaps = bfd["surviving_path_packet_interruptions_seconds"]
        prefix = f"ospf_run_{run_number}_"
        run.threshold(prefix + "bfd_surviving_path_packet_gap_seconds", max(map(float, gaps.values())), "<=", 3.0)
        run.threshold(prefix + "packet_continuity_threshold", bool(bfd["packet_continuity_threshold_passed"]), "==", True)
        run.threshold(prefix + "vrrp_failover_threshold", bool(bfd["vrrp_failover_threshold_passed"]), "==", True)
        return all(item["passed"] for item in run.thresholds if item["name"].startswith(prefix))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        run.thresholds.append({"name": f"ospf_run_{run_number}_result_parse", "actual": str(exc), "operator": "==", "limit": "valid JSON evidence", "passed": False})
        run.failed = True
        return False


def evaluate_ospf_repeatability(run: EvidenceRun, result_path: Path) -> bool:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        measurements = result["runs"]
        run.threshold("ospf_prior_repeatability_measurements", len(measurements), ">=", 3)
        for number, measurement in enumerate(measurements, start=1):
            prefix = f"ospf_prior_run_{number}_"
            gaps = measurement["surviving_path_packet_interruptions_seconds"]
            run.threshold(prefix + "bfd_surviving_path_packet_gap_seconds", max(map(float, gaps.values())), "<=", 3.0)
            run.threshold(prefix + "packet_continuity_threshold", bool(measurement["packet_continuity_threshold_passed"]), "==", True)
            run.threshold(prefix + "vrrp_failover_threshold", bool(measurement["vrrp_failover_threshold_passed"]), "==", True)
        passed = bool(result["all_runs_passed"]) and all(
            item["passed"] for item in run.thresholds if item["name"].startswith("ospf_prior_")
        )
        run.threshold("ospf_prior_repeatability_evidence_passed", passed, "==", True)
        return passed
    except (OSError, ValueError, KeyError, TypeError) as exc:
        run.thresholds.append({"name": "ospf_prior_repeatability_result_parse", "actual": str(exc), "operator": "==", "limit": "valid three-run evidence", "passed": False})
        run.failed = True
        return False


def evaluate_ipsec(run: EvidenceRun) -> None:
    output = (run.output_dir / "test-ipsec.stdout.txt").read_text(encoding="utf-8")
    match = re.search(r"\{\s*\"site_pair\".*?\n\}", output, re.DOTALL)
    if not match:
        run.thresholds.append({"name": "ipsec_result_present", "actual": False, "operator": "==", "limit": True, "passed": False})
        run.failed = True
        return
    try:
        result = json.loads(match.group(0))
        capture = result["underlay_capture"]
        run.threshold("ipsec_esp_packets", int(capture["esp_packets"]), ">=", 1)
        run.threshold("ipsec_ike_packets", int(capture["ike_packets"]), ">=", 1)
        run.threshold("ipsec_cleartext_enterprise_packets", int(capture["enterprise_plaintext_packets"]), "==", 0)
        run.threshold("ipsec_rekey_max_gap_seconds", float(result["rekey_max_gap_seconds"]), "<=", 3.0)
        source = Path(result["underlay_capture_path"])
        if source.is_file():
            destination = run.output_dir / "underlay.pcap"
            shutil.copy2(source, destination)
            run.evidence_files.append(destination.name)
            run.threshold("ipsec_underlay_capture_copied", destination.is_file() and destination.stat().st_size > 0, "==", True)
        else:
            run.threshold("ipsec_underlay_capture_exists", False, "==", True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        run.thresholds.append({"name": "ipsec_result_parse", "actual": str(exc), "operator": "==", "limit": "valid JSON evidence", "passed": False})
        run.failed = True


def check_prerequisite(run: EvidenceRun) -> bool:
    try:
        value = subprocess.run(
            ["sysctl", "-n", "net.netfilter.nf_log_all_netns"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        value = subprocess.CompletedProcess([], 127, "", str(exc))
    if value.returncode == 0 and value.stdout.strip() == "1":
        return True
    actual = value.stdout.strip() or value.stderr.strip() or "unavailable"
    run.thresholds.append({
        "name": "sec170_namespace_logging_prerequisite", "actual": actual, "operator": "==", "limit": "1", "passed": False
    })
    run.failed = True
    print(
        "SEC-170 live logs require net.netfilter.nf_log_all_netns=1 in the WSL init namespace; "
        "set it temporarily for this run and restore the previous value afterward.",
        file=sys.stderr,
    )
    return False


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    default_root = ROOT / "artifacts" / "runs" / "phase-1"
    output_root = Path(os.environ.get("NETLAB_EVIDENCE_DIR", str(default_root))).resolve()
    output_dir = output_root / f"phase-1-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=False)
    run = EvidenceRun(output_dir)
    started = utc_now()
    snapshot = snapshot_outputs()
    cleanup: list[dict[str, Any]] = []
    suite_started = False
    try:
        if check_prerequisite(run):
            suite_started = True
            commands = [
                ("verify-pinned-images", ["make", "verify-images"]),
                ("image-lock-report", ["make", "image-report"]),
                ("clean-initial-state", ["make", "lab-down"]),
                ("verify-empty-start", ["make", "verify-clean"]),
                ("verify-no-observability-containers-at-start", ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=netlab-observability"]),
                ("deploy-topology", ["make", "lab-up"]),
                ("test-bgp", ["make", "test-bgp"]),
                ("apply-ipsec-overlay", ["bash", "automation/roles/ipsec/apply.sh"]),
                ("test-ipsec", ["make", "test-ipsec"]),
                ("test-ospf-plan", PYTHON_COMMAND + ["-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/integration/ospf/test_plan.py", "tests/integration/l3/test_gateway_plan.py"]),
                ("apply-ospf", ["bash", "automation/roles/ospf/apply.sh"]),
                ("reapply-ospf", ["bash", "automation/roles/ospf/apply.sh"]),
                ("apply-gateway", PYTHON_COMMAND + ["config/gateway/apply.py"]),
                ("test-l2-plan", PYTHON_COMMAND + ["-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/integration/l2/test_switching_plan.py"]),
                ("test-l2", ["python3", "tests/integration/l2/measure.py", "--site", "all"]),
                ("test-services", ["make", "test-services"]),
                ("test-security", ["make", "test-security", "SEC_RUNTIME_PYTHON=python3"]),
                ("observability-up", ["make", "observability-up", "OBS_RUNTIME_PYTHON=python3"]),
                ("test-observability", ["make", "test-observability", "OBS_RUNTIME_PYTHON=python3"]),
                # L3 failover intentionally leaves the standby active until teardown.
                ("test-l3", ["make", "test-l3"]),
            ]
            ospf_passes = 0
            for name, command in commands:
                result = run.run(
                    name,
                    command,
                    expect_empty_output=name == "verify-no-observability-containers-at-start",
                    required=name != "test-l2-plan",
                    extra_env=None,
                )
                if result["status"] == "fail":
                    break
                if name == "test-l2":
                    evaluate_l2(run)
                elif name == "test-ipsec":
                    evaluate_ipsec(run)
                if run.failed:
                    break
                if name == "apply-gateway":
                    ospf_path = output_dir / "ospf.json"
                    measure_result = run.run(
                        "measure-ospf-live",
                        PYTHON_COMMAND + ["tests/integration/ospf/measure.py", "--output", str(ospf_path)],
                        extra_env={"NETLAB_OSPF_LIVE": "1"},
                    )
                    current_passed = evaluate_ospf(run, ospf_path, 1) if ospf_path.is_file() else False
                    ospf_passes = int(current_passed and measure_result["status"] == "pass")
                    repeatability_path = ROOT / "evidence" / "specs" / "ospf" / "ospf131-repeatability.json"
                    repeatability_passed = evaluate_ospf_repeatability(run, repeatability_path)
                    if repeatability_passed:
                        retained = output_dir / "ospf131-repeatability.json"
                        shutil.copy2(repeatability_path, retained)
                        run.evidence_files.append(retained.name)
                    run.threshold("ospf_live_and_three_run_repeatability_passed", ospf_passes == 1 and repeatability_passed, "==", True)
                    if run.failed:
                        break
                if name in ("test-services", "test-security"):
                    filename = "services/latest.md" if name == "test-services" else "security/latest.md"
                    source = ROOT / "evidence" / "specs" / filename
                    if source.is_file():
                        destination = output_dir / ("services.md" if name == "test-services" else "security.md")
                        shutil.copy2(source, destination)
                        run.evidence_files.append(destination.name)
    finally:
        if suite_started:
            cleanup.append(run.run("cleanup-project-runtime", ["make", "lab-down"], record_stage=False))
            cleanup.append(run.run("verify-final-clean", ["make", "verify-clean"], record_stage=False))
            cleanup.append(run.run(
                "verify-no-observability-containers-after-cleanup",
                ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=netlab-observability"],
                expect_empty_output=True,
                record_stage=False,
            ))
        restore_outputs(snapshot)

    run.failed |= any(stage["status"] != "pass" for stage in cleanup)
    bundle = {
        "schema_version": "1.0",
        "run_id": output_dir.name,
        "started_at_utc": started,
        "finished_at_utc": utc_now(),
        "result": "FAIL" if run.failed else "PASS",
        "runtime": {"docker_settings_changed": False, "runtime": "existing project WSL2 Docker Engine"},
        "thresholds": run.thresholds,
        "stages": run.stages,
        "cleanup": cleanup,
        "evidence_files": sorted(set(run.evidence_files + [
            path.name for path in output_dir.iterdir() if path.is_file() and path.name != "result.json"
        ])),
    }
    result_path = output_dir / "result.json"
    result_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "result": bundle["result"], "run_id": bundle["run_id"], "bundle": str(result_path),
        "stages_passed": sum(item["status"] == "pass" for item in run.stages),
        "stages_failed": sum(item["status"] == "fail" for item in run.stages),
        "stages_warnings": sum(item["status"] == "warning" for item in run.stages),
        "thresholds_passed": sum(item["passed"] for item in run.thresholds),
        "thresholds_failed": sum(not item["passed"] for item in run.thresholds),
    }, indent=2))
    return 0 if bundle["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
