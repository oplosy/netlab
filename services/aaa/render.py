#!/usr/bin/env python3
"""Render OOB-only FreeRADIUS files without persisting credentials in Git."""
from __future__ import annotations
import argparse
import ipaddress
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def render_clients(data: dict[str, Any], shared_secret: str) -> str:
    if len(shared_secret) < 32 or any(character.isspace() for character in shared_secret):
        raise ValueError("RADIUS shared secret must contain at least 32 non-space characters")
    return "\n".join([
        "# Generated from inventory; secret is runtime-only.",
        "client netlab-oob {",
        "  ipaddr = 172.31.255.0/24",
        f"  secret = {shared_secret}",
        "  nas_type = other",
        "}",
        "",
    ])


def render_authorize(username: str, password: str) -> str:
    if not username.isidentifier() or not password or any(character.isspace() for character in password):
        raise ValueError("invalid runtime test credentials")
    return f"{username} Cleartext-Password := \"{password}\"\n    Reply-Message := \"netlab central AAA accepted\"\n"


def render_node_radius(server_address: str, shared_secret: str, source_address: str) -> str:
    if len(shared_secret) < 32 or any(character.isspace() for character in shared_secret):
        raise ValueError("RADIUS shared secret must contain at least 32 non-space characters")
    server = str(ipaddress.ip_interface(server_address).ip)
    source = str(ipaddress.ip_interface(source_address).ip)
    return f"{server}:1812 {shared_secret} 3 {source}\n"


def render_sshd_config(oob_address: str) -> str:
    address = str(ipaddress.ip_interface(oob_address).ip)
    return "\n".join([
        "# Generated from inventory; SSH is bound to the OOB address only.",
        f"ListenAddress {address}", "PermitRootLogin no", "PasswordAuthentication yes",
        "KbdInteractiveAuthentication no", "UsePAM yes", "AllowUsers netlab_admin netlab_breakglass",
        "ForceCommand /usr/bin/vtysh", "PermitTTY yes", "AllowTcpForwarding no",
        "AllowAgentForwarding no", "X11Forwarding no", "PermitTunnel no", "PermitUserRC no", "",
    ])


def render_sshd_pam() -> str:
    return "\n".join([
        "# Generated: central RADIUS first; only netlab_breakglass may fall back locally.",
        "auth [success=done default=ignore] pam_radius_auth.so try_first_pass retry=1",
        "auth [success=ok default=die] pam_succeed_if.so user = netlab_breakglass",
        "auth sufficient pam_unix.so try_first_pass",
        "auth requisite pam_deny.so",
        "account required pam_nologin.so", "@include common-account",
        "session required pam_loginuid.so", "@include common-session",
        "session optional pam_motd.so motd=/run/motd.dynamic",
        "session optional pam_mail.so standard noenv", "session required pam_limits.so",
        "@include common-password", "",
    ])


def render_site(data: dict[str, Any], site: str, oob_address: str, shared_secret: str,
                username: str, password: str) -> dict[str, str]:
    clients = render_clients(data, shared_secret)
    authorize = render_authorize(username, password)
    return {"clients": clients, "authorize": authorize, "oob_address": str(ipaddress.ip_interface(oob_address).ip)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=ROOT / "inventory" / "inventory.yaml")
    parser.add_argument("--site", default="hq")
    args = parser.parse_args()
    print(json.dumps({"site": args.site, "credentials": "provided at runtime only"}))
