from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from automation.roles.services.apply import build_plan
from config.gateway.apply import build_plan as build_gateway_plan
from services.aaa.render import render_authorize, render_clients, render_node_radius, render_sshd_config, render_sshd_pam
from services.dhcp.render import render as render_dhcp
from services.dns.render import render_internet_zone, render_site
from services.ntp.render import render_internet, render_site as render_ntp

INVENTORY = json.loads((ROOT / "inventory/inventory.yaml").read_text(encoding="utf-8"))


class ServicePlanTests(unittest.TestCase):
    def test_kea_has_site_vlan_pools_and_bootstrap_options(self) -> None:
        for site, prefix in (("hq", "10.10"), ("br1", "10.20")):
            config = render_dhcp(INVENTORY, site)["Dhcp4"]
            self.assertEqual(config["interfaces-config"]["interfaces"], ["eth1"])
            self.assertEqual(len(config["subnet4"]), 4)
            for subnet in config["subnet4"]:
                options = {item["name"]: item["data"] for item in subnet["option-data"]}
                third = subnet["subnet"].split(".")[2]
                self.assertEqual(subnet["pools"], [{"pool": f"{prefix}.{third}.100-{prefix}.{third}.199"}])
                self.assertEqual(options["routers"], f"{prefix}.{third}.1")
                self.assertEqual(options["domain-name-servers"], f"{prefix}.20.11")
                self.assertEqual(options["ntp-servers"], f"{prefix}.20.12")

    def test_bind_zones_keep_data_plane_only_and_forward_simulated_names(self) -> None:
        for site in ("hq", "br1"):
            config = render_site(INVENTORY, site)
            aggregate = next(s["aggregate"] for s in INVENTORY["sites"] if s["id"] == site)
            self.assertIn("forwarders { 203.0.113.10; }", config["options"])
            self.assertIn(f"allow-recursion {{ localhost; {aggregate}; }}", config["options"])
            self.assertIn("gateway-vlan10 IN A", config["zone"])
            self.assertIn(f"svc-{site}-dns-1 IN A", config["zone"])
            self.assertNotIn("172.31.255.", config["zone"])
        self.assertIn("www IN A 203.0.113.20", render_internet_zone())

    def test_chrony_is_site_scoped_and_uses_simulated_isp(self) -> None:
        for site, aggregate in (("hq", "10.10.0.0/16"), ("br1", "10.20.0.0/16")):
            config = render_ntp(INVENTORY, site)
            self.assertIn("server 203.0.113.11 iburst prefer", config)
            self.assertIn(f"allow {aggregate}", config)
            self.assertNotIn("172.31.255.", config)
        internet_ntp = render_internet(INVENTORY)
        self.assertIn("bindaddress 203.0.113.11", internet_ntp)
        self.assertIn("allow 203.0.113.129/32", internet_ntp)
        self.assertIn("allow 203.0.113.130/32", internet_ntp)
        self.assertNotIn("allow 10.0.0.0/8", internet_ntp)

    def test_gateway_relays_each_vlan_to_local_kea_and_keeps_guest_exceptions(self) -> None:
        plan = build_gateway_plan(INVENTORY)
        self.assertEqual(len(plan["nodes"]), 4)
        self.assertEqual(len(plan["dhcp_relay_hooks"]), 16)
        for hook in plan["dhcp_relay_hooks"]:
            expected = "10.10.20.10" if hook["site"] == "hq" else "10.20.20.10"
            self.assertEqual(hook["server"], expected)
            self.assertTrue(hook["enabled"])
        for item in plan["nodes"]:
            policy = item["nftables_config"]
            offset = 10 if item["site"] == "hq" else 20
            self.assertIn(f"ip daddr 10.{offset}.20.11 udp dport 53 accept", policy)
            self.assertIn(f"ip daddr 10.{offset}.20.11 tcp dport 53 accept", policy)
            self.assertIn(f"ip daddr 10.{offset}.20.12 udp dport 123 accept", policy)
            for vlan in (10, 20, 30, 99):
                self.assertIn(f'iifname "vlan{vlan}" ip daddr 172.31.255.0/24 counter drop', policy)
            self.assertLess(policy.index("udp dport 53 accept"), policy.index('iifname "vlan30" ip daddr {'))

    def test_site_and_isp_service_routes_are_inventory_derived(self) -> None:
        plan = build_plan(INVENTORY)
        self.assertEqual(len(plan["sites"]), 6)
        self.assertEqual(len(plan["internet"]), 2)
        self.assertEqual(len(plan["network_nodes"]), 10)
        self.assertEqual(plan["network_nodes"][0]["oob"], "172.31.255.30/24")
        self.assertEqual(plan["aaa"]["oob_address"], "172.31.255.13")
        self.assertEqual(len(plan["service_egress_nat"]), 2)
        for item, public in zip(plan["service_egress_nat"], ("203.0.113.129", "203.0.113.130"), strict=True):
            self.assertEqual(len(item["rules"]), 3)
            self.assertEqual({rule["public_address"] for rule in item["rules"]}, {public})
            self.assertEqual(
                {(rule["source"], rule["destination"], rule["protocol"], rule["port"]) for rule in item["rules"]},
                {
                    ("10.10.20.11" if public.endswith("129") else "10.20.20.11", "203.0.113.10", "udp", 53),
                    ("10.10.20.11" if public.endswith("129") else "10.20.20.11", "203.0.113.10", "tcp", 53),
                    ("10.10.20.12" if public.endswith("129") else "10.20.20.12", "203.0.113.11", "udp", 123),
                },
            )
        for service in plan["internet"]:
            expected_interface = "eth3" if service["service"] == "dns" else "eth4"
            self.assertEqual(service["isp_interface"], expected_interface)
            expected_ip = "203.0.113.10/32" if service["service"] == "dns" else "203.0.113.11/32"
            self.assertEqual(service["service_address"], expected_ip)
        internet_dns_options = next(service["dns_files"]["/etc/bind/named.conf.options"]
                                    for service in plan["internet"] if service["service"] == "dns")
        self.assertIn("allow-query { 203.0.113.0/25; 203.0.113.129/32; 203.0.113.130/32; 10.0.0.0/8; }",
                      internet_dns_options)
        self.assertNotIn("203.0.113.128/25", internet_dns_options)

    def test_radius_rendering_uses_oob_scope_and_runtime_secrets(self) -> None:
        clients = render_clients(INVENTORY, "a" * 40)
        self.assertIn("172.31.255.0/24", clients)
        self.assertNotIn("10.10.", clients)
        self.assertIn("Cleartext-Password := ", render_authorize("netlab_test", "runtime-only-pass"))
        with self.assertRaises(ValueError):
            render_clients(INVENTORY, "short")

    def test_node_ssh_is_oob_only_pam_backed_and_breakglass_is_vtysh_only(self) -> None:
        secret = "runtime-shared-secret-that-is-long-enough"
        radius = render_node_radius("172.31.255.13/24", secret, "172.31.255.31/24")
        self.assertIn("172.31.255.13:1812", radius)
        self.assertIn("3 172.31.255.31", radius)
        sshd = render_sshd_config("172.31.255.31/24")
        self.assertIn("ListenAddress 172.31.255.31", sshd)
        self.assertIn("PermitRootLogin no", sshd)
        self.assertIn("ForceCommand /usr/bin/vtysh", sshd)
        self.assertIn("AllowTcpForwarding no", sshd)
        self.assertNotIn("0.0.0.0", sshd)
        pam = render_sshd_pam()
        self.assertLess(pam.index("pam_radius_auth.so"), pam.index("pam_unix.so"))
        self.assertIn("user = netlab_breakglass", pam)
        self.assertIn("default=die", pam)

    def test_service_packages_are_pinned(self) -> None:
        versions = {}
        for line in (ROOT / "versions.env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                versions[key] = value
        service_dockerfile = (ROOT / "images/service/Dockerfile").read_text(encoding="utf-8")
        for key in ("KEA_VERSION", "BIND9_VERSION", "BIND9_DNSUTILS_VERSION", "BIND9_UTILS_VERSION", "CHRONY_VERSION",
                    "FREERADIUS_VERSION", "DHCP_CLIENT_VERSION", "OPENSSH_CLIENT_VERSION",
                    "SSHPASS_VERSION"):
            self.assertIn(f"ARG {key}={versions[key]}", service_dockerfile)
        network_dockerfile = (ROOT / "images/network-node/Dockerfile").read_text(encoding="utf-8")
        for key in ("ISC_DHCP_RELAY_VERSION", "OPENSSH_SERVER_VERSION",
                    "LIBPAM_RADIUS_AUTH_VERSION"):
            self.assertIn(f"ARG {key}={versions[key]}", network_dockerfile)


if __name__ == "__main__":
    unittest.main()
