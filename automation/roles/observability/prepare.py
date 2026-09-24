#!/usr/bin/env python3
"""Render ignored runtime credentials and Compose SNMP agent sidecars."""
from __future__ import annotations

import argparse
import json
import secrets
import stat
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "artifacts" / "observability"
AGENT_NODES = {
    "isp1-core-1": "172.31.255.20", "hq-edge-1": "172.31.255.30",
    "hq-dist-1": "172.31.255.31", "hq-dist-2": "172.31.255.32",
    "hq-access-1": "172.31.255.33", "br1-edge-1": "172.31.255.50",
    "br1-dist-1": "172.31.255.51", "br1-dist-2": "172.31.255.52",
    "br1-access-1": "172.31.255.53",
}


def mode_600(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def render(check: bool = False) -> list[Path]:
    if not check:
        RUNTIME.mkdir(parents=True, exist_ok=True)
    secret_file = RUNTIME / "credentials.json"
    if secret_file.exists():
        credentials = json.loads(secret_file.read_text(encoding="utf-8"))
    else:
        credentials = {
            "snmp_username": "netlab",
            "snmp_auth_password": secrets.token_urlsafe(32),
            "snmp_priv_password": secrets.token_urlsafe(32),
            "grafana_admin_password": secrets.token_urlsafe(32),
        }
    generated: dict[Path, str] = {}
    auth, privacy = credentials["snmp_auth_password"], credentials["snmp_priv_password"]
    interface_module = yaml.safe_load(
        (ROOT / "observability" / "snmp-if_mib.yml").read_text(encoding="utf-8")
    )["modules"]["if_mib"]
    exporter = {"auths": {"netlab": {
        "version": 3, "security_level": "authPriv", "username": credentials["snmp_username"],
        "password": auth, "auth_protocol": "SHA", "priv_protocol": "AES", "priv_password": privacy,
    }}, "modules": {"if_mib": interface_module}}
    generated[RUNTIME / "snmp.yml"] = yaml.safe_dump(exporter, sort_keys=False)
    generated[RUNTIME / "runtime.env"] = f"GRAFANA_ADMIN_PASSWORD={credentials['grafana_admin_password']}\n"
    services: dict[str, dict[str, object]] = {}
    for node, address in AGENT_NODES.items():
        (RUNTIME / "agents" / node / "var-lib-snmp").mkdir(parents=True, exist_ok=True)
        config = RUNTIME / "agents" / f"{node}.conf"
        users_config = RUNTIME / "agents" / f"{node}.users.conf"
        generated[users_config] = f'createUser netlab SHA "{auth}" AES "{privacy}"\n'
        generated[config] = (
            "rouser netlab priv .1.3.6.1.2.1\n"
            "sysLocation NetLab OOB\n"
            f"sysName {node}\n"
            "sysServices 72\n"
        )
        service: dict[str, object] = {
            "image": "netlab/snmp-agent:0.1.0",
            "platform": "linux/amd64",
            "network_mode": f"container:clab-netlab-phase-1-{node}",
            "command": [node, address],
            "volumes": [
                f"../artifacts/observability/agents/{node}.conf:/run/netlab-snmp/{node}.conf:ro",
                f"../artifacts/observability/agents/{node}.users.conf:/run/netlab-snmp/{node}.users.conf:ro",
                f"../artifacts/observability/agents/{node}/var-lib-snmp:/var/lib/snmp"],
            "restart": "unless-stopped",
        }
        if node == "hq-edge-1":
            service["build"] = {"context": ".", "dockerfile": "agent.Dockerfile", "args": {
                "UBUNTU_SNAPSHOT": "20260905T000000Z", "SNMPD_VERSION": "5.9.4+dfsg-1.1ubuntu3.2"}}
        services[f"snmp-agent-{node}"] = service
    generated[RUNTIME / "compose.agents.yaml"] = yaml.safe_dump({"services": services}, sort_keys=False)
    changed: list[Path] = []
    for path, content in generated.items():
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                changed.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            mode_600(path, content)
    if not secret_file.exists() and not check:
        mode_600(secret_file, json.dumps(credentials, indent=2) + "\n")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = render(check=args.check)
    if changed:
        print("runtime files missing or stale: " + ", ".join(str(path.relative_to(ROOT)) for path in changed))
        return 1
    print("observability runtime files are ready (ignored, mode 0600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
