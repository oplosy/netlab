from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "evidence"))
import phase1
from phase1 import EvidenceRun, evaluate_ipsec, evaluate_ospf, evaluate_ospf_repeatability


def ospf_result(*, gap: float = 1.2, continuity: bool = True, vrrp: bool = True) -> dict:
    return {
        "bfd_test": {
            "surviving_path_packet_interruptions_seconds": {
                "hq-dist-2": gap,
                "hq-active-vip": gap,
            },
            "packet_continuity_threshold_passed": continuity,
            "vrrp_failover_threshold_passed": vrrp,
        }
    }


def test_three_ospf_measurements_pass_individually(tmp_path: Path) -> None:
    run = EvidenceRun(tmp_path)
    for number in range(1, 4):
        result_path = tmp_path / f"ospf-run-{number}.json"
        result_path.write_text(json.dumps(ospf_result()), encoding="utf-8")
        assert evaluate_ospf(run, result_path, number)

    run.threshold("ospf_three_consecutive_runs_passed", 3, "==", 3)
    assert not run.failed


def test_ospf_gap_over_three_seconds_fails_run(tmp_path: Path) -> None:
    run = EvidenceRun(tmp_path)
    result_path = tmp_path / "ospf-run-1.json"
    result_path.write_text(json.dumps(ospf_result(gap=3.01)), encoding="utf-8")

    assert not evaluate_ospf(run, result_path, 1)
    assert run.failed


def test_prior_ospf_repeatability_requires_three_passing_measurements(tmp_path: Path) -> None:
    run = EvidenceRun(tmp_path)
    results_path = tmp_path / "repeatability.json"
    passing_run = {
        "surviving_path_packet_interruptions_seconds": {"hq-dist-2": 1.2, "hq-active-vip": 1.4},
        "packet_continuity_threshold_passed": True,
        "vrrp_failover_threshold_passed": True,
    }
    results_path.write_text(
        json.dumps({"runs": [passing_run, passing_run, passing_run], "all_runs_passed": True}),
        encoding="utf-8",
    )

    assert evaluate_ospf_repeatability(run, results_path)
    assert not run.failed


def test_prior_ospf_repeatability_rejects_a_missed_measurement(tmp_path: Path) -> None:
    run = EvidenceRun(tmp_path)
    results_path = tmp_path / "repeatability.json"
    passing_run = {
        "surviving_path_packet_interruptions_seconds": {"hq-dist-2": 1.2, "hq-active-vip": 1.4},
        "packet_continuity_threshold_passed": True,
        "vrrp_failover_threshold_passed": True,
    }
    failed_run = {**passing_run, "vrrp_failover_threshold_passed": False}
    results_path.write_text(
        json.dumps({"runs": [passing_run, failed_run, passing_run], "all_runs_passed": False}),
        encoding="utf-8",
    )

    assert not evaluate_ospf_repeatability(run, results_path)
    assert run.failed


def test_ipsec_missing_capture_fails_acceptance(tmp_path: Path) -> None:
    run = EvidenceRun(tmp_path)
    (tmp_path / "test-ipsec.stdout.txt").write_text(
        json.dumps(
            {
                "site_pair": "hq-br1",
                "underlay_capture": {
                    "esp_packets": 1,
                    "ike_packets": 1,
                    "enterprise_plaintext_packets": 0,
                },
                "rekey_max_gap_seconds": 1.0,
                "underlay_capture_path": str(tmp_path / "missing.pcap"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    evaluate_ipsec(run)

    assert run.failed
    assert any(item["name"] == "ipsec_underlay_capture_exists" and not item["passed"] for item in run.thresholds)


def test_interrupted_stage_is_recorded_as_failure(tmp_path: Path, monkeypatch) -> None:
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(phase1.subprocess, "run", interrupt)
    run = EvidenceRun(tmp_path)

    result = run.run("stalled-stage", ["python", "slow.py"])

    assert result["status"] == "fail"
    assert result["return_code"] == 130
    assert run.failed
    assert (tmp_path / result["stderr_file"]).read_text(encoding="utf-8").strip() == "command interrupted by operator"
