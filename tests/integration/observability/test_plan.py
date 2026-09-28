from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "automation" / "roles" / "observability"))

import prepare  # noqa: E402
from targets import snmp_nodes  # noqa: E402


def read_inventory() -> dict:
    return json.loads((ROOT / "inventory/inventory.yaml").read_text(encoding="utf-8"))


def test_stack_images_are_version_locked_and_share_oob_namespace() -> None:
    stack = yaml.safe_load((ROOT / "observability/compose.yaml").read_text(encoding="utf-8"))
    lock = (ROOT / "versions.env").read_text(encoding="utf-8")
    for name, service in stack["services"].items():
        variable = service["image"].split(":?", 1)[0].removeprefix("${")
        locked = next(line.split("=", 1)[1] for line in lock.splitlines() if line.startswith(f"{variable}="))
        assert "@sha256:" in locked, name
        assert service["platform"] == "${IMAGE_PLATFORM:-linux/amd64}"
        assert service["network_mode"] == "container:clab-netlab-phase-1-svc-observability-1"
        assert "ports" not in service


def test_snmp_nodes_are_every_infrastructure_oob_node() -> None:
    inventory = read_inventory()
    expected = {
        node["id"]: node["oob"].split("/")[0]
        for node in inventory["nodes"]
        if node.get("role") in {"edge", "dist", "access", "isp", "firewall"}
    }
    assert snmp_nodes(inventory) == expected
    assert snmp_nodes()["hq-fw-1"] == "172.31.255.35"
    assert len(set(expected.values())) == len(expected)


def test_prometheus_snmp_targets_match_snmp_nodes() -> None:
    config = yaml.safe_load((ROOT / "observability/prometheus.yml").read_text(encoding="utf-8"))
    targets = config["scrape_configs"][0]["static_configs"][0]["targets"]
    assert config["scrape_configs"][0]["job_name"] == "snmp"
    assert sorted(targets) == sorted(snmp_nodes().values())
    assert config["scrape_configs"][0]["params"] == {"auth": ["netlab"], "module": ["if_mib"]}
    assert config["scrape_configs"][1]["static_configs"][0]["targets"] == ["127.0.0.1:6060"]


def test_prepare_renders_one_agent_sidecar_per_snmp_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prepare, "RUNTIME", tmp_path)
    prepare.render()
    services = yaml.safe_load((tmp_path / "compose.agents.yaml").read_text(encoding="utf-8"))["services"]
    expected = snmp_nodes()
    assert set(services) == {f"snmp-agent-{node}" for node in expected}
    for node, address in expected.items():
        service = services[f"snmp-agent-{node}"]
        assert service["command"] == [node, address]
        assert service["network_mode"] == f"container:clab-netlab-phase-1-{node}"
        assert (tmp_path / "agents" / f"{node}.conf").is_file()
    assert sum("build" in service for service in services.values()) == 1


def test_live_acceptance_derives_expected_snmp_targets() -> None:
    source = (ROOT / "tests/integration/observability/acceptance.py").read_text(encoding="utf-8")
    assert "from targets import snmp_nodes" in source
    assert "len(snmp_targets) != 9" not in source


def test_service_intent_exposes_only_required_observability_ingress() -> None:
    intent = next(row for row in read_inventory()["service_intents"] if row["id"] == "observability-oob")
    assert {(row["protocol"], row["port"]) for row in intent["transports"]} == {
        ("tcp", 3000), ("tcp", 514), ("udp", 514), ("tcp", 9090), ("udp", 2055),
    }


def test_logs_and_flows_are_forwarded_to_local_loki() -> None:
    config = (ROOT / "observability/alloy.alloy").read_text(encoding="utf-8")
    assert "/var/log/netlab/syslog.log" in config
    assert "/var/lib/netlab/flows/ipfix.json" in config
    assert "http://172.31.255.14:3100/loki/api/v1/push" in config
    assert config.count('sync_period = "5s"') == 2
    loki = yaml.safe_load((ROOT / "observability/loki.yaml").read_text(encoding="utf-8"))
    assert loki["auth_enabled"] is False
    assert loki["server"]["http_listen_address"] == "172.31.255.14"


def test_snmp_exporter_has_interface_metrics_and_name_lookups() -> None:
    config = yaml.safe_load((ROOT / "observability/snmp-if_mib.yml").read_text(encoding="utf-8"))
    module = config["modules"]["if_mib"]
    metrics = {metric["name"]: metric for metric in module["metrics"]}
    assert "1.3.6.1.2.1.2" in module["walk"]
    assert "ifOperStatus" in metrics
    assert any(lookup["labelname"] == "ifName" for lookup in metrics["ifOperStatus"]["lookups"])


def test_dashboard_has_interface_state_and_logs() -> None:
    dashboard = json.loads((ROOT / "observability/grafana/dashboards/path-health.json").read_text(encoding="utf-8"))
    expressions = [target["expr"] for panel in dashboard["panels"] for target in panel["targets"]]
    assert "ifOperStatus" in expressions
    assert any("job=~" in expr and "ipfix" in expr for expr in expressions)
