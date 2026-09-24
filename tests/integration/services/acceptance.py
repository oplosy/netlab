#!/usr/bin/env python3
"""Live SVC-160 acceptance checks. Run only when the orchestrator releases the lab."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAB = "netlab-phase-1"


def run(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr or result.stdout}")
    return result.stdout


def container(node_id: str) -> str:
    return f"clab-{LAB}-{node_id}"


def docker_exec(node_id: str, *args: str) -> str:
    return run("docker", "exec", container(node_id), *args)


def counter(node_id: str, selector: str) -> int:
    for table in ("netlab_sec170", "netlab_gateway"):
        result = subprocess.run(
            ("docker", "exec", container(node_id), "nft", "list", "chain", "inet", table, "forward"),
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            continue
        lines = result.stdout.splitlines()
        if table == "netlab_sec170":
            # SEC-170's default drop runs before the SVC gateway's more specific
            # drop rules, so measure its terminal forward-chain counter instead.
            selected = [line for line in lines if line.strip().startswith("counter packets ")
                        and line.strip().endswith(" drop")]
            if selected:
                match = re.search(r"counter packets (\d+)", selected[-1])
                if match:
                    return int(match.group(1))
        else:
            for line in lines:
                if selector in line:
                    match = re.search(r"counter packets (\d+)", line)
                    if match:
                        return int(match.group(1))
    raise RuntimeError(f"no nftables counter found for {selector} on {node_id}")


def helper(client: str, script: str, image: str) -> str:
    return run(
        "docker", "run", "--rm", "--network", f"container:{container(client)}",
        "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW", image,
        "bash", "-ec", script, timeout=90,
    )


def helper_with_stdin(client: str, command: str, image: str, value: str, *, expect_success: bool = True) -> str:
    result = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", f"container:{container(client)}",
         "--cap-add", "NET_ADMIN", image, "bash", "-ec", command],
        input=value, capture_output=True, text=True, timeout=60, check=False,
    )
    if (result.returncode == 0) != expect_success:
        raise RuntimeError(f"SSH acceptance command had unexpected status {result.returncode}: {result.stderr or result.stdout}")
    return result.stdout


def lease_and_bootstrap(site: str, client: str, image: str) -> str:
    offset = "10.10" if site == "hq" else "10.20"
    guest = "guest" in client
    script = "\n".join([
        "ip -4 address flush dev eth1",
        "rm -f /tmp/dhclient.eth1.leases",
        "dhclient -4 -1 -v -lf /tmp/dhclient.eth1.leases eth1",
        "ip -4 -o address show dev eth1",
        "grep -E 'fixed-address|option routers|option domain-name-servers|option ntp-servers' /tmp/dhclient.eth1.leases | tail -n 4",
        f"dig +time=3 +tries=1 @{offset}.20.11 {site}-edge-1.{site}.netlab.test A",
        f"dig +time=3 +tries=1 @{offset}.20.11 www.internet.test A",
        "printf 'driftfile /tmp/chrony.drift\\nserver "
        + f"{offset}.20.12 iburst\\nallow {offset}.0.0/16\\ncmdport 0\\n"
        + "' >/tmp/chrony-acceptance.conf",
        "chronyd -Q -t 8 -f /tmp/chrony-acceptance.conf",
    ])
    output = helper(client, script, image)
    vlan = "30" if guest else ("20" if "server" in client else "10")
    expected = (
        f"option routers {offset}.{vlan}.1;",
        f"option domain-name-servers {offset}.20.11;",
        f"option ntp-servers {offset}.20.12;",
    )
    if any(value not in output for value in expected) or "www.internet.test" not in output:
        raise RuntimeError(f"DNS bootstrap checks failed for {client}: {output}")
    # Guests also need an explicit policy exception; confirm the router's
    # data-plane exception rules are installed before testing their drops.
    if guest:
        gateway = f"{site}-dist-1"
        rules = docker_exec(gateway, "nft", "list", "chain", "inet", "netlab_gateway", "forward")
        if f"{offset}.20.11 udp dport 53 accept" not in rules or f"{offset}.20.12 udp dport 123 accept" not in rules:
            raise RuntimeError(f"guest DNS/NTP exceptions are absent on {gateway}")
    return output


def relay_ownership(site: str) -> None:
    relays: dict[str, set[str]] = {}
    for suffix in ("dist-1", "dist-2"):
        node = f"{site}-{suffix}"
        state = docker_exec(node, "bash", "-ec",
                            "test ! -s /run/netlab/dhcp-relay-state || cat /run/netlab/dhcp-relay-state")
        fields = state.split()
        if fields:
            if len(fields) < 2:
                raise RuntimeError(f"DHCP relay state is incomplete on {node}")
            docker_exec(node, "bash", "-ec", f"kill -0 {fields[1]}")
        relays[node] = set(fields[2:])
    for vlan in (10, 20, 30, 99):
        owners = [node for node, interfaces in relays.items() if f"vlan{vlan}" in interfaces]
        if len(owners) != 1:
            raise RuntimeError(f"VLAN {vlan} on {site} has {len(owners)} active DHCP relays: {owners}")
    print(f"{site}: one DHCP relay per active VRRP VLAN: PASS")


def denied_flow(site: str, guest: str, target: str, image: str) -> None:
    gateway = f"{site}-dist-1"
    guest_drop = f'iifname "vlan30" ip daddr {{'
    oob_drop = 'iifname "vlan30" ip daddr 172.31.255.0/24'
    before_server = counter(gateway, guest_drop)
    before_oob = counter(gateway, oob_drop)
    helper(
        guest,
        "python3 -c 'import socket; s=socket.socket(); s.settimeout(1); "
        f"s.connect_ex(({json.dumps(target)}, 22))'",
        image,
    )
    helper(guest, "python3 -c 'import socket; s=socket.socket(); s.settimeout(1); s.connect_ex((\"172.31.255.13\", 1812))'", image)
    if counter(gateway, guest_drop) <= before_server:
        raise RuntimeError(f"guest-to-server deny counter did not increase at {gateway}")
    if counter(gateway, oob_drop) <= before_oob:
        raise RuntimeError(f"guest-to-OOB deny counter did not increase at {gateway}")


def denied_oob(site: str, client: str, vlan_id: int, image: str) -> None:
    gateway = f"{site}-dist-1"
    selector = f'iifname "vlan{vlan_id}" ip daddr 172.31.255.0/24'
    before = counter(gateway, selector)
    helper(client, "python3 -c 'import socket; s=socket.socket(); s.settimeout(1); s.connect_ex((\"172.31.255.13\", 22))'", image)
    if counter(gateway, selector) <= before:
        raise RuntimeError(f"OOB deny counter did not increase for {client}")


def aaa_acceptance() -> None:
    node = container("svc-aaa-1")
    credentials = json.loads(run("docker", "exec", node, "cat", "/run/netlab/aaa-runtime.json"))
    password = credentials["admin_password"]
    target = "hq-edge-1"
    target_oob = "172.31.255.30"
    ssh = (
        "sshpass -d 0 ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=5 netlab_admin@{target_oob}"
    )
    session = helper_with_stdin("svc-aaa-1", ssh, os.environ.get("NETLAB_SERVICE_IMAGE", "netlab/service:0.1.0"),
                                password + "\nshow version\nexit\n")
    if "FRRouting" not in session:
        raise RuntimeError("RADIUS-backed SSH did not reach the forced vtysh CLI")
    helper_with_stdin("svc-aaa-1", ssh, os.environ.get("NETLAB_SERVICE_IMAGE", "netlab/service:0.1.0"),
                      "invalid-netlab-password\n", expect_success=False)

    inventory = json.loads((ROOT / "inventory" / "inventory.yaml").read_text(encoding="utf-8"))
    for network_node in (node for node in inventory["nodes"] if node.get("role") in {"edge", "dist", "isp", "router"}):
        address = network_node["oob"].split("/")[0]
        output = docker_exec(network_node["id"], "ss", "-H", "-lnt", "sport", "=", ":22")
        listeners = [line.split()[3] for line in output.splitlines() if line.split()]
        if not listeners or any(not local.startswith(address + ":") for local in listeners):
            raise RuntimeError(f"SSH listener is absent or not OOB-only on {network_node['id']}: {listeners}")

    try:
        run("docker", "exec", target, "nft", "delete", "table", "inet", "netlab_svc160_aaa_test", timeout=10)
    except RuntimeError:
        pass
    try:
        docker_exec(target, "bash", "-ec", "nft add table inet netlab_svc160_aaa_test; "
                    "nft 'add chain inet netlab_svc160_aaa_test output { type filter hook output priority -200; policy accept; }'; "
                    "nft add rule inet netlab_svc160_aaa_test output ip daddr 172.31.255.13 udp dport 1812 drop")
        fallback = helper_with_stdin(
            "svc-aaa-1",
            "sshpass -d 0 ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            f"-o ConnectTimeout=10 netlab_breakglass@{target_oob}",
            os.environ.get("NETLAB_SERVICE_IMAGE", "netlab/service:0.1.0"),
            credentials["breakglass_password"] + "\nshow version\nexit\n",
        )
        if "FRRouting" not in fallback:
            raise RuntimeError("local break-glass SSH did not reach the forced vtysh CLI")
        helper_with_stdin(
            "svc-aaa-1",
            "sshpass -d 0 ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            f"-o ConnectTimeout=10 netlab_admin@{target_oob}",
            os.environ.get("NETLAB_SERVICE_IMAGE", "netlab/service:0.1.0"),
            password + "\n", expect_success=False,
        )
    finally:
        docker_exec(target, "nft", "delete", "table", "inet", "netlab_svc160_aaa_test")
    print("central AAA-backed SSH accept/reject: PASS (secrets redacted)")
    print("OOB-only SSH, vtysh-only break-glass during RADIUS outage: PASS")


def main() -> int:
    image = os.environ.get("NETLAB_SERVICE_IMAGE", "netlab/service:0.1.0")
    # Verify this image exists before attempting any namespace probes.
    run("docker", "image", "inspect", image)
    for site in ("hq", "br1"):
        relay_ownership(site)
        for suffix in ("client-users-1", "server-1", "client-guest-1"):
            client = f"{site}-{suffix}"
            output = lease_and_bootstrap(site, client, image)
            print(f"{client}: DHCP options, internal/Internet DNS, NTP query: PASS")
            if suffix == "client-guest-1":
                # The server target is the data address from its fresh lease.
                server_output = helper(f"{site}-server-1", "dhclient -4 -1 eth1 >/dev/null; ip -4 -o address show dev eth1", image)
                match = re.search(r"inet (10\.(?:10|20)\.20\.\d+)/", server_output)
                if not match:
                    raise RuntimeError(f"could not determine {site} server VLAN address")
                denied_flow(site, client, match.group(1), image)
                print(f"{client}: guest-to-server and guest-to-OOB drops: PASS")
            vlan_id = 30 if suffix == "client-guest-1" else (20 if suffix == "server-1" else 10)
            denied_oob(site, client, vlan_id, image)
    aaa_acceptance()
    print("SVC-160 live acceptance: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError) as exc:
        print(f"SVC-160 live acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
