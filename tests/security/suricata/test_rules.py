"""EDGE-420 static checks for the curated Suricata rules and wiring (ADR 0018)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RULES = ROOT / "secure-edge" / "suricata" / "rules" / "netlab-local.rules"
SPEC = importlib.util.spec_from_file_location(
    "security_apply", ROOT / "config" / "security" / "apply.py"
)
assert SPEC and SPEC.loader
security = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(security)


def _rules() -> list[str]:
    return [
        line.strip()
        for line in RULES.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_rules_use_only_the_reserved_sid_range_and_unique_sids() -> None:
    sids = [int(re.search(r"\bsid:(\d+);", rule).group(1)) for rule in _rules()]
    assert sids and len(sids) == len(set(sids))
    assert all(9420000 <= sid <= 9420999 for sid in sids), sids


def test_rules_cover_detection_and_prevention() -> None:
    actions = {rule.split()[0] for rule in _rules()}
    assert actions == {"alert", "drop"}
    by_sid = {
        int(re.search(r"\bsid:(\d+);", rule).group(1)): rule for rule in _rules()
    }
    assert by_sid[9420001].startswith("alert dns") and "ids-test.netlab" in by_sid[9420001]
    assert by_sid[9420002].startswith("drop dns") and "ips-block.netlab" in by_sid[9420002]


def test_engine_loads_only_the_local_rule_file_on_the_policy_queue() -> None:
    args = security.SURICATA_ARGS
    assert args[args.index("-S") + 1] == security.SURICATA_RULES_DEST
    assert "-s" not in args  # -S is exclusive; -s would add default rules
    assert args[args.index("-q") + 1] == str(security.IPS_QUEUE)
    assert security.IPS_QUEUE_VERDICT == f"counter queue num {security.IPS_QUEUE}"
    assert security.SURICATA_RULES == RULES


def test_only_the_firewall_queues_to_the_ips() -> None:
    plan = security.render_plan(security.load_inventory())
    roles = {node["id"]: node.get("role") for node in security.load_inventory()["nodes"]}
    for node_id, tables in plan.items():
        queued = "queue num" in "".join(tables.values())
        assert queued == (roles[node_id] == "firewall"), node_id
