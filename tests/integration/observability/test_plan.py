from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


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


def test_snmp_targets_cover_every_infrastructure_oob_node() -> None:
    inventory = read_inventory()
    expected = {
        node["oob"].split("/")[0]
        for node in inventory["nodes"]
        if node.get("role") in {"edge", "dist", "access", "isp"}
    }
    config = yaml.safe_load((ROOT / "observability/prometheus.yml").read_text(encoding="utf-8"))
    targets = set(config["scrape_configs"][0]["static_configs"][0]["targets"])
    assert targets == expected
    assert config["scrape_configs"][0]["params"] == {"auth": ["netlab"], "module": ["if_mib"]}
    assert config["scrape_configs"][1]["static_configs"][0]["targets"] == ["127.0.0.1:6060"]


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
