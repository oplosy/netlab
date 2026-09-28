#!/usr/bin/env python3
"""Apply inventory-rendered site services and the OOB-only AAA service."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from services.aaa.render import (
    render_authorize,
    render_clients,
    render_node_radius,
    render_sshd_config,
    render_sshd_pam,
)
from services.dhcp.render import render as render_dhcp
from services.dns.render import render_internet_zone
from services.dns.render import render_site as render_dns
from services.ntp.render import render_internet
from services.ntp.render import render_site as render_ntp

LAB = "netlab-phase-1"
AAA_NODE = "svc-aaa-1"


def _physical(data: dict[str, Any]) -> dict[tuple[str, str], str]:
    kinds = {"routed", "ebgp", "l2", "access"}
    result: dict[tuple[str, str], str] = {}
    for node in data["nodes"]:
        number = 1
        for interface in node.get("interfaces", []):
            if interface.get("kind") in kinds:
                result[node["id"], interface["name"]] = f"eth{number}"
                number += 1
    return result


def _run(
    docker: str, container: str, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [docker, "exec", container, *args], capture_output=True, text=True, check=False
    )
    if check and result.returncode:
        raise RuntimeError(
            f"{container}: {' '.join(args)}: {(result.stderr or result.stdout).strip()}"
        )
    return result


def _run_input(docker: str, container: str, value: str, *args: str) -> None:
    result = subprocess.run(
        [docker, "exec", "-i", container, *args],
        input=value,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"{container}: {' '.join(args)}: {(result.stderr or result.stdout).strip()}"
        )


def _copy(
    docker: str, container: str, destination: str, value: str, mode: str = "0644"
) -> bool:
    old = _run(docker, container, "cat", destination, check=False)
    if old.returncode == 0 and old.stdout == value:
        permissions = _run(
            docker, container, "stat", "-c", "%a", destination, check=False
        )
        if permissions.returncode == 0 and permissions.stdout.strip() != mode:
            _run(docker, container, "chmod", mode, destination)
            return True
        return False
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", delete=False
    ) as stream:
        stream.write(value)
        source = Path(stream.name)
    try:
        subprocess.run(
            [docker, "cp", str(source), f"{container}:{destination}"], check=True
        )
        _run(docker, container, "chmod", mode, destination)
    finally:
        source.unlink(missing_ok=True)
    return True


def _container(node_id: str) -> str:
    return f"clab-{LAB}-{node_id}"


def _site_files(data: dict[str, Any], site: str) -> dict[str, tuple[str, str]]:
    dhcp = json.dumps(render_dhcp(data, site), indent=2) + "\n"
    dns = render_dns(data, site)
    return {
        f"svc-{site}-dhcp-1": {"/etc/kea/kea-dhcp4.conf": dhcp},
        f"svc-{site}-dns-1": {
            "/etc/bind/named.conf.options": dns["options"],
            "/etc/bind/named.conf.local": dns["local"],
            f"/etc/bind/netlab-{site}.zone": dns["zone"],
        },
        f"svc-{site}-ntp-1": {"/etc/chrony/chrony.conf": render_ntp(data, site)},
    }


def _internet_dns_files(data: dict[str, Any]) -> dict[str, str]:
    public_endpoints = sorted(
        {
            str(ipaddress.ip_interface(site["public_endpoint"]).ip)
            for site in data["sites"]
        },
        key=ipaddress.ip_address,
    )
    allowed_sources = " ".join(f"{address}/32;" for address in public_endpoints)
    return {
        "/etc/bind/named.conf.options": "\n".join(
            [
                "options {",
                '  directory "/var/cache/bind";',
                "  listen-on { 127.0.0.1; 203.0.113.10; };",
                "  listen-on-v6 { none; };",
                "  recursion no;",
                f"  allow-query {{ 203.0.113.0/25; {allowed_sources} 10.0.0.0/8; }};",
                "  allow-transfer { none; };",
                "  dnssec-validation no;",
                "};",
                "",
            ]
        ),
        "/etc/bind/named.conf.local": 'zone "internet.test" { type master; file "/etc/bind/internet.test.zone"; allow-transfer { none; }; };\n',
        "/etc/bind/internet.test.zone": render_internet_zone(),
    }


def _runtime_credentials(docker: str, aaa_container: str) -> dict[str, str]:
    existing = _run(
        docker, aaa_container, "cat", "/run/netlab/aaa-runtime.json", check=False
    )
    if existing.returncode == 0:
        value = json.loads(existing.stdout)
        if {"shared_secret", "admin_password", "breakglass_password"} <= value.keys():
            return value
    credentials = {
        "shared_secret": secrets.token_urlsafe(36),
        "admin_username": "netlab_admin",
        "admin_password": secrets.token_urlsafe(24),
        "breakglass_password": secrets.token_urlsafe(28),
    }
    _copy(
        docker,
        aaa_container,
        "/run/netlab/aaa-runtime.json",
        json.dumps(credentials) + "\n",
        "0600",
    )
    return credentials


def _service_nodes(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in data["nodes"] if node.get("role") == "service"}


def _link_endpoints(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        link["id"]: {endpoint["node"]: endpoint for endpoint in link["endpoints"]}
        for link in data["links"]
        if link.get("kind") == "routed"
    }


def build_plan(data: dict[str, Any]) -> dict[str, Any]:
    physical = _physical(data)
    nodes = _service_nodes(data)
    work: list[dict[str, Any]] = []
    for site in (item["id"] for item in data["sites"]):
        for node_id, files in _site_files(data, site).items():
            node = nodes[node_id]
            interface_name = node["interfaces"][0]["name"]
            address = str(ipaddress.ip_interface(node["service_address"]))
            site_network = ipaddress.ip_network(
                next(item["aggregate"] for item in data["sites"] if item["id"] == site)
            )
            work.append(
                {
                    "node": node_id,
                    "container": _container(node_id),
                    "interface": physical[node_id, interface_name],
                    "address": address,
                    "gateway": str(
                        next(
                            ipaddress.ip_address(vlan["gateway"])
                            for vlan in data["vlans"]
                            if vlan["site"] == site and vlan["vlan_id"] == 20
                        )
                    ),
                    "internet_prefix": "203.0.113.0/25",
                    "site_prefix": str(site_network),
                    "files": files,
                }
            )
    links = _link_endpoints(data)
    isp_work = []
    for site, service, link_id, route in (
        ("isp", "dns", "isp1-service-dns", "203.0.113.10/32"),
        ("isp", "ntp", "isp1-service-ntp", "203.0.113.11/32"),
    ):
        endpoint_nodes = links[link_id]
        isp_endpoint = endpoint_nodes["isp1-core-1"]
        service_id = next(
            node_id for node_id in endpoint_nodes if node_id != "isp1-core-1"
        )
        service_endpoint = endpoint_nodes[service_id]
        isp_work.append(
            {
                "service": service,
                "container": _container(service_id),
                "interface": physical[service_id, service_endpoint["interface"]],
                "address": service_endpoint["address"],
                "service_address": route,
                "gateway": str(ipaddress.ip_interface(isp_endpoint["address"]).ip),
                "isp_container": _container("isp1-core-1"),
                "isp_interface": physical["isp1-core-1", isp_endpoint["interface"]],
                "isp_address": isp_endpoint["address"],
                "route_via": str(
                    ipaddress.ip_interface(service_endpoint["address"]).ip
                ),
                "dns_files": _internet_dns_files(data) if service == "dns" else {},
                "ntp_file": render_internet(data) if service == "ntp" else None,
            }
        )
    aaa_node = nodes[AAA_NODE]
    network_nodes = [
        {"id": node["id"], "oob": node["oob"]}
        for node in data["nodes"]
        if node.get("role") in {"edge", "dist", "firewall", "isp", "router"}
    ]
    service_egress_nat = []
    for site in data["sites"]:
        edge = next(
            node
            for node in data["nodes"]
            if node.get("site") == site["id"] and node.get("role") == "edge"
        )
        rules: list[dict[str, Any]] = []
        for service, destination, protocol, ports in (
            ("dns", "203.0.113.10", "udp", (53,)),
            ("dns", "203.0.113.10", "tcp", (53,)),
            ("ntp", "203.0.113.11", "udp", (123,)),
        ):
            service_node = nodes[f"svc-{site['id']}-{service}-1"]
            rules.append(
                {
                    "source": str(
                        ipaddress.ip_interface(service_node["service_address"]).ip
                    ),
                    "destination": destination,
                    "protocol": protocol,
                    "port": ports[0],
                    "public_address": str(
                        ipaddress.ip_interface(site["public_endpoint"]).ip
                    ),
                }
            )
        service_egress_nat.append({"container": _container(edge["id"]), "rules": rules})
    return {
        "sites": work,
        "internet": isp_work,
        "network_nodes": network_nodes,
        "aaa": {
            "container": _container(AAA_NODE),
            "oob_address": str(ipaddress.ip_interface(aaa_node["oob"]).ip),
        },
        "service_egress_nat": service_egress_nat,
    }


def _restart_or_reload(docker: str, node: dict[str, Any], service: str) -> None:
    container = node["container"]
    processes = {
        "dns": "named",
        "ntp": "chronyd",
        "dhcp": "kea-dhcp4",
        "aaa": "freeradius",
    }
    process = processes[service]
    was_running = (
        _run(docker, container, "pgrep", "-x", process, check=False).returncode == 0
    )
    _run(docker, container, "touch", "/run/netlab/services-configured")
    if not was_running:
        _run(
            docker,
            container,
            "bash",
            "-ec",
            f"for _ in {{1..100}}; do pgrep -x {process} >/dev/null && exit 0; sleep 0.1; done; "
            f"echo '{service} daemon failed to start after configuration marker' >&2; exit 1",
        )
        return
    old_pid = _run(docker, container, "pgrep", "-xo", process).stdout.strip()
    _run(docker, container, "kill", "-TERM", old_pid)
    _run(
        docker,
        container,
        "bash",
        "-ec",
        f"for _ in {{1..100}}; do pid=$(pgrep -xo {process} || true); "
        f'[[ -n "$pid" && "$pid" != {old_pid} ]] && exit 0; sleep 0.1; done; '
        f"echo '{service} daemon did not restart after configuration update' >&2; exit 1",
    )


def apply(plan: dict[str, Any], docker: str = "docker") -> None:
    for item in plan["sites"]:
        container = item["container"]
        _run(docker, container, "ip", "link", "set", "dev", item["interface"], "up")
        _run(
            docker,
            container,
            "ip",
            "address",
            "replace",
            item["address"],
            "dev",
            item["interface"],
        )
        _run(
            docker,
            container,
            "ip",
            "route",
            "replace",
            item["internet_prefix"],
            "via",
            item["gateway"],
            "dev",
            item["interface"],
        )
        _run(
            docker,
            container,
            "ip",
            "route",
            "replace",
            item["site_prefix"],
            "via",
            item["gateway"],
            "dev",
            item["interface"],
        )
        changed = False
        for destination, content in item["files"].items():
            changed = _copy(docker, container, destination, content) or changed
        if item["node"].endswith("-dns-1"):
            _run(docker, container, "named-checkconf", "/etc/bind/named.conf")
            site = item["node"].split("-")[1]
            _run(
                docker,
                container,
                "named-checkzone",
                f"{site}.netlab.test",
                f"/etc/bind/netlab-{site}.zone",
            )
        elif item["node"].endswith("-dhcp-1"):
            _run(docker, container, "kea-dhcp4", "-t", "/etc/kea/kea-dhcp4.conf")
        elif item["node"].endswith("-ntp-1"):
            _run(docker, container, "chronyd", "-p", "-f", "/etc/chrony/chrony.conf")
        if (
            changed
            or _run(
                docker,
                container,
                "test",
                "-f",
                "/run/netlab/services-configured",
                check=False,
            ).returncode
        ):
            _restart_or_reload(docker, item, item["node"].split("-")[2])

    for item in plan["internet"]:
        _run(
            docker,
            item["isp_container"],
            "ip",
            "link",
            "set",
            "dev",
            item["isp_interface"],
            "up",
        )
        _run(
            docker,
            item["isp_container"],
            "ip",
            "address",
            "replace",
            item["isp_address"],
            "dev",
            item["isp_interface"],
        )
        _run(
            docker,
            item["isp_container"],
            "ip",
            "route",
            "replace",
            item["service_address"],
            "via",
            item["route_via"],
            "dev",
            item["isp_interface"],
        )
        service = item["container"]
        _run(docker, service, "ip", "link", "set", "dev", item["interface"], "up")
        _run(
            docker,
            service,
            "ip",
            "address",
            "replace",
            item["address"],
            "dev",
            item["interface"],
        )
        _run(
            docker,
            service,
            "ip",
            "address",
            "replace",
            item["service_address"],
            "dev",
            "lo",
        )
        _run(
            docker,
            service,
            "ip",
            "route",
            "replace",
            "default",
            "via",
            item["gateway"],
            "dev",
            item["interface"],
        )
        changed = False
        if item["service"] == "dns":
            for destination, content in item["dns_files"].items():
                changed = _copy(docker, service, destination, content) or changed
            _run(docker, service, "named-checkconf", "/etc/bind/named.conf")
            _run(
                docker,
                service,
                "named-checkzone",
                "internet.test",
                "/etc/bind/internet.test.zone",
            )
        else:
            changed = _copy(
                docker, service, "/etc/chrony/chrony.conf", item["ntp_file"]
            )
            _run(docker, service, "chronyd", "-p", "-f", "/etc/chrony/chrony.conf")
        if (
            changed
            or _run(
                docker,
                service,
                "test",
                "-f",
                "/run/netlab/services-configured",
                check=False,
            ).returncode
        ):
            _restart_or_reload(docker, {**item, "container": service}, item["service"])
        if item["service"] == "ntp":
            # The simulated ISP NTP endpoint must serve its local stratum after
            # every start. Site servers then poll it promptly and converge
            # before the service apply reports success.
            _run(docker, service, "chronyc", "local", "stratum", "8")

    # Install the return-path SNAT before waiting for site chrony to synchronize.
    _configure_service_egress_nat(plan["service_egress_nat"], docker)

    for site in ("hq", "br1"):
        ntp_container = _container(f"svc-{site}-ntp-1")
        _run(docker, ntp_container, "chronyc", "burst", "4/4")
        _run(docker, ntp_container, "chronyc", "waitsync", "30", "0", "0", "1")

    aaa = plan["aaa"]
    credentials = _runtime_credentials(docker, aaa["container"])
    files = {
        "/etc/freeradius/3.0/clients.conf": render_clients(
            {}, credentials["shared_secret"]
        ),
        "/etc/freeradius/3.0/mods-config/files/authorize": render_authorize(
            credentials["admin_username"], credentials["admin_password"]
        ),
        "/etc/freeradius/3.0/sites-enabled/netlab": "\n".join(
            [
                "server netlab {",
                "  listen {",
                "    type = auth",
                "    ipaddr = 172.31.255.13",
                "    port = 1812",
                "  }",
                "  listen {",
                "    type = acct",
                "    ipaddr = 172.31.255.13",
                "    port = 1813",
                "  }",
                "  authorize {",
                "    filter_username",
                "    preprocess",
                "    files",
                "    pap",
                "  }",
                "  authenticate {",
                "    Auth-Type PAP {",
                "      pap",
                "    }",
                "    Auth-Type CHAP {",
                "      chap",
                "    }",
                "    Auth-Type MS-CHAP {",
                "      mschap",
                "    }",
                "    Auth-Type EAP {",
                "      eap",
                "    }",
                "  }",
                "}",
                "",
            ]
        ),
    }
    _run(
        docker,
        aaa["container"],
        "rm",
        "-f",
        "/etc/freeradius/3.0/sites-enabled/default",
        "/etc/freeradius/3.0/sites-enabled/inner-tunnel",
    )
    changed = False
    for destination, content in files.items():
        changed = (
            _copy(docker, aaa["container"], destination, content, "0640") or changed
        )
        ownership = _run(docker, aaa["container"], "stat", "-c", "%U:%G", destination)
        if ownership.stdout.strip() != "root:freerad":
            _run(docker, aaa["container"], "chown", "root:freerad", destination)
            changed = True
    _run(docker, aaa["container"], "freeradius", "-XC")
    aaa_item = {"container": aaa["container"]}
    if (
        changed
        or _run(
            docker,
            aaa["container"],
            "test",
            "-f",
            "/run/netlab/services-configured",
            check=False,
        ).returncode
    ):
        _restart_or_reload(docker, aaa_item, "aaa")

    _configure_network_nodes(plan, credentials, docker)


def _configure_service_egress_nat(items: list[dict[str, Any]], docker: str) -> None:
    """SNAT only site DNS/NTP upstream requests to the BGP-advertised endpoint."""
    for item in items:
        lines = [
            "table ip netlab_service_egress_nat {",
            " chain postrouting { type nat hook postrouting priority srcnat; policy accept;",
        ]
        for rule in item["rules"]:
            lines.append(
                f"  ip saddr {rule['source']} ip daddr {rule['destination']} "
                f"{rule['protocol']} dport {rule['port']} counter snat to {rule['public_address']}"
            )
        lines.extend([" }", "}", ""])
        content = "\n".join(lines)
        digest = hashlib.sha256(content.encode()).hexdigest()
        marker = "/run/netlab/service-egress-nat.sha256"
        current = _run(docker, item["container"], "cat", marker, check=False)
        exists = _run(
            docker,
            item["container"],
            "nft",
            "list",
            "table",
            "ip",
            "netlab_service_egress_nat",
            check=False,
        )
        if (
            current.returncode == 0
            and current.stdout.strip() == digest
            and exists.returncode == 0
        ):
            continue
        _run(
            docker,
            item["container"],
            "nft",
            "delete",
            "table",
            "ip",
            "netlab_service_egress_nat",
            check=False,
        )
        _run_input(docker, item["container"], content, "nft", "-f", "-")
        _run_input(docker, item["container"], digest + "\n", "tee", marker)


def _configure_network_nodes(
    plan: dict[str, Any], credentials: dict[str, str], docker: str
) -> None:
    aaa_address = plan["aaa"]["oob_address"]
    for node in plan["network_nodes"]:
        container = _container(node["id"])
        oob_address = str(ipaddress.ip_interface(node["oob"]).ip)
        radius_config = render_node_radius(
            aaa_address, credentials["shared_secret"], node["oob"]
        )
        ssh_config = render_sshd_config(node["oob"])
        pam_config = render_sshd_pam()
        desired = {
            "/etc/pam_radius_auth.conf": (radius_config, "0600"),
            "/etc/ssh/sshd_config.d/10-netlab-oob.conf": (ssh_config, "0644"),
            "/etc/pam.d/sshd": (pam_config, "0644"),
        }
        changed = False
        for path, (content, mode) in desired.items():
            changed = _copy(docker, container, path, content, mode) or changed
        account_state = hashlib.sha256(
            "\0".join(
                (
                    credentials["admin_password"],
                    credentials["breakglass_password"],
                    credentials["shared_secret"],
                )
            ).encode()
        ).hexdigest()
        state = _run(
            docker, container, "cat", "/run/netlab/aaa-configured", check=False
        )
        accounts_changed = (
            state.returncode != 0 or state.stdout.strip() != account_state
        )
        _run(
            docker,
            container,
            "bash",
            "-ec",
            "getent passwd netlab_admin >/dev/null || useradd --create-home --shell /bin/bash --groups frrvty netlab_admin; "
            "getent passwd netlab_breakglass >/dev/null || useradd --create-home --shell /bin/bash --groups frrvty netlab_breakglass; "
            "usermod --append --groups frrvty netlab_admin; usermod --append --groups frrvty netlab_breakglass",
        )
        if accounts_changed:
            _run_input(
                docker,
                container,
                f"netlab_admin:{credentials['admin_password']}\nnetlab_breakglass:{credentials['breakglass_password']}\n",
                "chpasswd",
            )
        _run(docker, container, "mkdir", "-p", "/run/sshd")
        _run(docker, container, "ssh-keygen", "-A")
        _run(docker, container, "sshd", "-t")
        if changed or accounts_changed:
            _run(
                docker,
                container,
                "bash",
                "-ec",
                "if pgrep -x sshd >/dev/null; then pkill -HUP -x sshd; else /usr/sbin/sshd; fi",
            )
            _run_input(
                docker,
                container,
                account_state + "\n",
                "tee",
                "/run/netlab/aaa-configured",
            )
        elif _run(docker, container, "pgrep", "-x", "sshd", check=False).returncode:
            _run(docker, container, "/usr/sbin/sshd")
        _run(docker, container, "test", "-f", "/etc/pam_radius_auth.conf")
        if oob_address == aaa_address:
            raise ValueError(f"network node {node['id']} reuses central AAA address")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory", type=Path, default=ROOT / "inventory" / "inventory.yaml"
    )
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.inventory.read_text(encoding="utf-8"))
    from scripts.validate.validate_inventory import validate_inventory

    errors = validate_inventory(data, ROOT / "schemas/inventory.schema.json")
    if errors:
        print("invalid inventory:\n" + "\n".join(errors), file=sys.stderr)
        return 1
    plan = build_plan(data)
    if args.plan:
        public = {key: value for key, value in plan.items() if key != "aaa"}
        print(json.dumps(public, indent=2))
        return 0
    try:
        apply(plan)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"service apply failed: {exc}", file=sys.stderr)
        return 1
    print("site services converged for HQ and BR1; central AAA configured on OOB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
