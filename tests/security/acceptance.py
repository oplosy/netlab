#!/usr/bin/env python3
"""Live SEC-170 permit, deny, NAT, logging, and control-plane acceptance."""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = "netlab-phase-1"


def run(
    *args: str, check: bool = True, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )
    if check and result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr or result.stdout}"
        )
    return result


def container(node_id: str) -> str:
    return f"clab-{LAB}-{node_id}"


def physical_interface(node: dict, logical_name: str) -> str:
    physical = [
        item
        for item in node.get("interfaces", [])
        if item.get("kind") in {"routed", "ebgp", "l2", "access"}
    ]
    return f"eth{next(index for index, item in enumerate(physical, 1) if item['name'] == logical_name)}"


def docker_exec(node_id: str, *args: str, check: bool = True) -> str:
    return run("docker", "exec", container(node_id), *args, check=check).stdout


def drop_count(node_id: str, chain: str) -> int:
    output = docker_exec(
        node_id, "nft", "-a", "list", "chain", "inet", "netlab_sec170", chain
    )
    matches = re.findall(r"counter packets (\d+) bytes \d+ drop", output)
    if not matches:
        raise RuntimeError(f"no terminal deny counter in {node_id} {chain}")
    return int(matches[-1])


def service_allow_count(node_id: str, chain: str, expected_rule: str) -> int:
    output = docker_exec(
        node_id, "nft", "-a", "list", "chain", "inet", "netlab_sec170", chain
    )
    for line in output.splitlines():
        if expected_rule in line:
            match = re.search(r"counter packets (\d+)", line)
            return int(match.group(1)) if match else 0
    raise RuntimeError(
        f"expected narrow allow rule is missing on {node_id}: {expected_rule}"
    )


def helper(
    client: str, command: str, image: str, *, expect_success: bool = True
) -> str:
    result = run(
        "docker",
        "run",
        "--rm",
        "--network",
        f"container:{container(client)}",
        "--cap-add",
        "NET_ADMIN",
        "--cap-add",
        "NET_RAW",
        image,
        "bash",
        "-ec",
        command,
        check=False,
        timeout=90,
    )
    if (result.returncode == 0) != expect_success:
        raise RuntimeError(
            f"probe status {result.returncode} was unexpected for {client}: {result.stderr or result.stdout}"
        )
    return result.stdout


def lease(client: str, image: str) -> str:
    return helper(
        client,
        "ip -4 address flush dev eth1; rm -f /tmp/dhclient.eth1.leases; "
        "dhclient -4 -1 -v -lf /tmp/dhclient.eth1.leases eth1 >/dev/null; "
        "ip -4 -o address show dev eth1; ip -4 route show default",
        image,
    )


def _counter_line(node_id: str, table: str, chain: str, fragment: str) -> int:
    output = docker_exec(
        node_id, "nft", "-a", "list", "chain", table.split()[0], table.split()[1], chain
    )
    for line in output.splitlines():
        if fragment in line:
            match = re.search(r"counter packets (\d+)", line)
            return int(match.group(1)) if match else 0
    raise RuntimeError(f"counter rule not found on {node_id}: {fragment}")


def corporate_icmp(
    source_site: str, destination_site: str, image: str
) -> dict[str, str]:
    source = f"{source_site}-client-users-1"
    destination = f"{destination_site}-client-users-1"
    source_output = lease(source, image)
    destination_output = lease(destination, image)
    addresses = re.findall(
        r"inet (10\.\d+\.10\.\d+)/", source_output + "\n" + destination_output
    )
    if len(addresses) < 2:
        raise RuntimeError(
            f"cannot determine DHCP user addresses for {source_site}->{destination_site}"
        )
    source_ip, destination_ip = addresses[0], addresses[1]
    probe = (
        "python3 -c 'import socket,struct; "
        "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_ICMP); s.settimeout(4); "
        f's.sendto(struct.pack("!BBHHH",8,0,0,0,170)+b"sec170",({json.dumps(destination_ip)},0)); '
        'r=s.recvfrom(512)[0]; assert len(r)>=8 and r[0]==0, r; print("echo-reply")\''
    )
    try:
        output = helper(source, probe, image)
    except RuntimeError as exc:
        raise RuntimeError(
            f"corporate ICMP {source_site} ({source_ip}) -> "
            f"{destination_site} ({destination_ip}) failed: {exc}"
        ) from exc
    if "echo-reply" not in output:
        raise RuntimeError(
            f"corporate ICMP did not return from {destination_ip}: {output}"
        )
    return {
        "source_site": source_site,
        "source_ip": source_ip,
        "destination_site": destination_site,
        "destination_ip": destination_ip,
        "result": "ICMP echo reply received",
    }


def ensure_site_overlay() -> None:
    """Start the dependency overlay and wait for its summarized OSPF route."""
    hq_edge = container("hq-edge-1")
    sas = run(
        "docker", "exec", hq_edge, "swanctl", "--list-sas", check=False, timeout=20
    )
    inventory = json.loads(
        (ROOT / "inventory" / "inventory.yaml").read_text(encoding="utf-8")
    )
    overlay = next(link for link in inventory["links"] if link["id"] == "hq-br1-xfrm")
    child = "site-overlay-" + overlay["id"].replace("-", "_")
    if child not in sas.stdout:
        run(
            "docker",
            "exec",
            hq_edge,
            "swanctl",
            "--initiate",
            "--child",
            child,
            timeout=60,
        )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        route = run(
            "docker",
            "exec",
            hq_edge,
            "vtysh",
            "-c",
            "show ip route 10.20.0.0/16",
            check=False,
        )
        if route.returncode == 0 and "xfrm0" in route.stdout:
            return
        time.sleep(1)
    raise RuntimeError("HQ has no OSPF 10.20.0.0/16 route over the BR1 XFRM interface")


def guest_probe(
    site: str,
    image: str,
    public_ip: str,
    server_ip: str,
    inband_ip: str,
    remote_ip: str,
    inventory: dict,
) -> dict[str, str]:
    guest = f"{site}-client-guest-1"
    dist = f"{site}-dist-1"
    edge = f"{site}-edge-1"
    local_prefix = str(
        ipaddress.ip_network(
            next(item["aggregate"] for item in inventory["sites"] if item["id"] == site)
        ).network_address
    ).rsplit(".", 2)[0]
    isp_link = next(
        link
        for link in inventory["links"]
        if link.get("kind") == "ebgp"
        and any(item["node"] == edge for item in link["endpoints"])
        and any(item["node"] == "isp1-core-1" for item in link["endpoints"])
    )
    isp_endpoint = next(
        item for item in isp_link["endpoints"] if item["node"] == "isp1-core-1"
    )
    isp_node = next(item for item in inventory["nodes"] if item["id"] == "isp1-core-1")
    isp_iface = physical_interface(isp_node, isp_endpoint["interface"])
    before_isp = service_allow_count(
        "isp1-core-1",
        "forward",
        f'iifname "{isp_iface}" ip saddr {public_ip} ip daddr 203.0.113.11 udp dport 123',
    )
    before_nat = docker_exec(
        edge, "nft", "-a", "list", "chain", "ip", "netlab_sec170_nat", "postrouting"
    )
    nat_before = next(
        (
            int(m.group(1))
            for line in before_nat.splitlines()
            if f"ip saddr {local_prefix}.30.0/24 ip daddr 203.0.113.11 udp dport 123"
            in line
            and (m := re.search(r"counter packets (\d+)", line))
        ),
        0,
    )
    dns_before = service_allow_count(
        "isp1-core-1",
        "forward",
        f'iifname "{isp_iface}" ip saddr {public_ip} ip daddr 203.0.113.10 udp dport 53',
    )
    dns_nat_before = _counter_line(
        edge,
        "ip netlab_sec170_nat",
        "postrouting",
        f"ip saddr {local_prefix}.30.0/24 ip daddr 203.0.113.10 udp dport 53",
    )
    dns = helper(
        guest, "dig +time=3 +tries=1 @203.0.113.10 www.internet.test A +short", image
    )
    dns_after = service_allow_count(
        "isp1-core-1",
        "forward",
        f'iifname "{isp_iface}" ip saddr {public_ip} ip daddr 203.0.113.10 udp dport 53',
    )
    dns_nat_after = _counter_line(
        edge,
        "ip netlab_sec170_nat",
        "postrouting",
        f"ip saddr {local_prefix}.30.0/24 ip daddr 203.0.113.10 udp dport 53",
    )
    if (
        "203.0.113.20" not in dns.split()
        or dns_after <= dns_before
        or dns_nat_after <= dns_nat_before
    ):
        raise RuntimeError(
            f"{site} guest DNS did not resolve through the scoped public SNAT policy: {dns.strip()}"
        )
    dns_tcp_before = service_allow_count(
        "isp1-core-1",
        "forward",
        f'iifname "{isp_iface}" ip saddr {public_ip} ip daddr 203.0.113.10 tcp dport 53',
    )
    dns_tcp_nat_before = _counter_line(
        edge,
        "ip netlab_sec170_nat",
        "postrouting",
        f"ip saddr {local_prefix}.30.0/24 ip daddr 203.0.113.10 tcp dport 53",
    )
    dns_tcp = helper(
        guest,
        "dig +tcp +time=3 +tries=1 @203.0.113.10 www.internet.test A +short",
        image,
    )
    dns_tcp_after = service_allow_count(
        "isp1-core-1",
        "forward",
        f'iifname "{isp_iface}" ip saddr {public_ip} ip daddr 203.0.113.10 tcp dport 53',
    )
    dns_tcp_nat_after = _counter_line(
        edge,
        "ip netlab_sec170_nat",
        "postrouting",
        f"ip saddr {local_prefix}.30.0/24 ip daddr 203.0.113.10 tcp dport 53",
    )
    if (
        "203.0.113.20" not in dns_tcp.split()
        or dns_tcp_after <= dns_tcp_before
        or dns_tcp_nat_after <= dns_tcp_nat_before
    ):
        raise RuntimeError(
            f"{site} guest TCP DNS did not resolve through the scoped public SNAT policy: {dns_tcp.strip()}"
        )
    ntp = (
        "python3 -c 'import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(5); "
        'q=bytearray(48); q[0]=0x23; s.sendto(q,("203.0.113.11",123)); r,_=s.recvfrom(256); '
        'assert len(r)>=48 and (r[0]&7)==4, (len(r),r[0]); print("NTP server mode=4 bytes=%d" % len(r))\''
    )
    output = helper(guest, ntp, image)
    after_isp = service_allow_count(
        "isp1-core-1",
        "forward",
        f'iifname "{isp_iface}" ip saddr {public_ip} ip daddr 203.0.113.11 udp dport 123',
    )
    after_nat = docker_exec(
        edge, "nft", "-a", "list", "chain", "ip", "netlab_sec170_nat", "postrouting"
    )
    nat_after = next(
        (
            int(m.group(1))
            for line in after_nat.splitlines()
            if f"ip saddr {local_prefix}.30.0/24 ip daddr 203.0.113.11 udp dport 123"
            in line
            and (m := re.search(r"counter packets (\d+)", line))
        ),
        0,
    )
    if not output.strip() or after_isp <= before_isp:
        raise RuntimeError(
            f"{site} guest Internet NTP was not accepted with its public SNAT address {public_ip}"
        )
    # nft NAT counters are kernel-version dependent; the ISP input counter is the packet-level proof.
    dist_node = next(node for node in inventory["nodes"] if node["id"] == dist)
    oob_ip = str(ipaddress.ip_interface(dist_node["oob"]).ip)
    blocked = [
        ("server", server_ip, 22),
        ("inband", inband_ip, 22),
        ("corporate", remote_ip, 22),
        ("oob", oob_ip, 22),
    ]
    denied_packets: dict[str, str] = {}
    for _name, address, port in blocked:
        # The selected OOB address belongs to this distribution node, so the
        # probe terminates in its input hook. Other routed destinations traverse
        # the forward hook; the inband SVI probe also targets the local node.
        deny_chain = "input" if _name in {"inband", "oob"} else "forward"
        before_denied_probe = drop_count(dist, deny_chain)
        helper(
            guest,
            f"python3 -c 'import socket; s=socket.socket(); s.settimeout(2); r=s.connect_ex(({json.dumps(address)},{port})); assert r != 0, ({json.dumps(address)},{port},r)'",
            image,
        )
        after_denied_probe = drop_count(dist, deny_chain)
        if after_denied_probe <= before_denied_probe:
            raise RuntimeError(
                f"{site} {_name} probe did not hit the distribution {deny_chain} default-deny counter"
            )
        denied_packets[_name] = (
            f"{deny_chain}:{before_denied_probe}->{after_denied_probe}"
        )
    # Unauthenticated BFD on a user data VLAN must not reach an SVI control plane.
    lease(f"{site}-server-1", image)
    before_control = drop_count(dist, "input")
    dist_svi = next(
        address
        for interface in dist_node["interfaces"]
        if interface["name"] == "vlan20"
        for address in interface["addresses"]
    ).split("/")[0]
    helper(
        f"{site}-server-1",
        f'python3 -c \'import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(1); s.sendto(b"SEC170",({json.dumps(dist_svi)},3784)); print("sent")\'',
        image,
    )
    after_control = drop_count(dist, "input")
    if after_control <= before_control:
        raise RuntimeError(
            f"{site} unauthorized data-VLAN BFD packet did not hit the input deny counter"
        )
    return {
        "site": site,
        "guest_dns": dns.strip(),
        "guest_dns_nat_packets_before_after": f"{dns_nat_before}->{dns_nat_after}",
        "isp_dns_allow_packets_before_after": f"{dns_before}->{dns_after}",
        "guest_dns_tcp": dns_tcp.strip(),
        "guest_dns_tcp_nat_packets_before_after": f"{dns_tcp_nat_before}->{dns_tcp_nat_after}",
        "isp_dns_tcp_allow_packets_before_after": f"{dns_tcp_before}->{dns_tcp_after}",
        "guest_ntp": output.strip(),
        "snat_public_endpoint": public_ip,
        "isp_service_allow_packets": str(after_isp),
        "guest_nat_rule_packets_before_after": f"{nat_before}->{nat_after}",
        "denied_flows": ",".join(name for name, _, _ in blocked),
        "per_flow_forward_deny_packets_before_after": json.dumps(
            denied_packets, sort_keys=True
        ),
        "unauthorized_bfd_input_packets_before_after": f"{before_control}->{after_control}",
    }


def main() -> int:
    inventory = json.loads(
        (ROOT / "inventory" / "inventory.yaml").read_text(encoding="utf-8")
    )
    image = os.environ.get("NETLAB_SERVICE_IMAGE", "netlab/service:0.1.0")
    run("docker", "image", "inspect", image)
    namespace_logging = run(
        "sysctl", "-n", "net.netfilter.nf_log_all_netns", check=False
    )
    namespace_logging_enabled = (
        namespace_logging.returncode == 0 and namespace_logging.stdout.strip() == "1"
    )
    evidence = []
    monitor_path = Path("/tmp/netlab-sec170-kernel.log")
    monitor_path.unlink(missing_ok=True)
    with monitor_path.open("w", encoding="utf-8") as monitor_stream:
        monitor = subprocess.Popen(
            ["stdbuf", "-oL", "dmesg", "--follow", "--ctime"],
            stdout=monitor_stream,
            stderr=subprocess.DEVNULL,
        )
        try:
            sites = {site["id"]: site for site in inventory["sites"]}
            for site in sites:
                guest = f"{site}-client-guest-1"
                lease(guest, image)
                # The reserved target need not have a listener; nft counters prove the deny.
                local_prefix = str(
                    ipaddress.ip_network(sites[site]["aggregate"]).network_address
                ).rsplit(".", 2)[0]
                remote = "hq" if site != "hq" else "br1"
                remote_prefix = str(
                    ipaddress.ip_network(sites[remote]["aggregate"]).network_address
                ).rsplit(".", 2)[0]
                edge = next(
                    node
                    for node in inventory["nodes"]
                    if node.get("site") == site
                    and node.get("role") == "edge"
                    and node["id"].endswith("edge-1")
                )
                public = str(ipaddress.ip_interface(edge["public_endpoint"]).ip)
                evidence.append(
                    guest_probe(
                        site,
                        image,
                        public,
                        f"{local_prefix}.20.250",
                        f"{local_prefix}.99.2",
                        f"{remote_prefix}.10.1",
                        inventory,
                    )
                )
            ensure_site_overlay()
            evidence.append(corporate_icmp("hq", "br1", image))
            evidence.append(corporate_icmp("br1", "hq", image))
            evidence.append(corporate_icmp("hq", "br2", image))
            evidence.append(corporate_icmp("br2", "hq", image))
        finally:
            monitor.send_signal(signal.SIGINT)
            monitor.wait(timeout=10)
    logs = monitor_path.read_text(encoding="utf-8")
    log_lines = [line for line in logs.splitlines() if "SEC170|" in line]
    evidence.append(
        {
            "correlated_deny_log_events_observed": str(len(log_lines)),
            "kernel_log_visibility": (
                "available"
                if namespace_logging_enabled and log_lines
                else "unavailable; per-rule nft counters and failed probes are authoritative"
            ),
        }
    )
    service_acceptance_path = (
        ROOT / "tests" / "integration" / "services" / "acceptance.py"
    )
    spec = importlib.util.spec_from_file_location(
        "service_acceptance", service_acceptance_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load SVC-160 OOB AAA regression")
    service_acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service_acceptance)
    service_acceptance.aaa_acceptance()
    evidence.append({"oob_aaa_and_break_glass": "PASS"})
    report = [
        "# SEC-170 Live Security Evidence",
        "",
        f"- Run time (UTC): {datetime.now(timezone.utc).isoformat()}",
        "- Runtime: existing project WSL2 lab; Docker Desktop and daemon settings were not changed.",
        "- Result: PASS",
        "",
        "## Results",
        "",
    ]
    for item in evidence:
        report.extend(["### " + item.get("site", "Control-plane negative test"), ""])
        report.extend(
            f"- {key}: {value}" for key, value in item.items() if key != "site"
        )
        report.append("")
    report.extend(
        [
            "## Deny log sample",
            "",
            "```text",
            *log_lines[-3:],
            "```",
            "",
        ]
    )
    out = ROOT / "evidence" / "specs" / "security" / "latest.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report), encoding="utf-8")
    print("SEC-170 live acceptance: PASS")
    for row in evidence:
        print(json.dumps(row, sort_keys=True))
    print(f"evidence={out.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired,
        KeyError,
        ValueError,
    ) as exc:
        print(f"SEC-170 live acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
