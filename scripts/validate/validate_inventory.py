#!/usr/bin/env python3
"""Validate the Git-authoritative inventory and its cross-record invariants.

The inventory file is JSON syntax stored with a ``.yaml`` suffix. JSON is a
strict YAML 1.2 subset, so it can be consumed by both the Python validator and
normal YAML tooling without requiring a parser at this foundation stage.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from pathlib import Path
from typing import Any


class InventoryValidationError(ValueError):
    """Raised when one or more inventory invariants are violated."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _unique(items: list[dict[str, Any]], field: str, label: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item.get(field)
        if value in result:
            errors.append(f"duplicate {label} '{value}'")
        else:
            result[value] = item
    return result


def _parse_ip(value: str, label: str, errors: list[str]) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_interface(value).ip
    except ValueError:
        errors.append(f"{label} is not an IP address or interface: {value!r}")
        return None


def _parse_network(value: str, label: str, errors: list[str]) -> ipaddress._BaseNetwork | None:
    try:
        return ipaddress.ip_network(value, strict=True)
    except ValueError:
        errors.append(f"{label} is not a canonical network: {value!r}")
        return None


def _check_schema(data: dict[str, Any], schema_path: Path, errors: list[str]) -> None:
    """Run JSON Schema when installed, while retaining a stdlib-only fallback."""
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        for error in sorted(validator.iter_errors(data), key=lambda item: list(item.path)):
            path = ".".join(str(part) for part in error.path) or "<root>"
            errors.append(f"schema {path}: {error.message}")
    except (OSError, json.JSONDecodeError, jsonschema.SchemaError) as exc:
        errors.append(f"unable to validate JSON Schema: {exc}")


def validate_inventory(data: dict[str, Any], schema_path: Path | None = None) -> list[str]:
    """Return all validation errors; an empty list means the inventory is valid."""
    errors: list[str] = []
    required = {"schema_version", "metadata", "asns", "sites", "nodes", "vlans", "prefixes", "links", "bundles", "service_intents"}
    missing = required - set(data) if isinstance(data, dict) else required
    if not isinstance(data, dict):
        return ["inventory root must be an object"]
    if missing:
        errors.append(f"missing top-level fields: {', '.join(sorted(missing))}")
        return errors
    if schema_path is not None:
        _check_schema(data, schema_path, errors)

    asns = data["asns"]
    sites = data["sites"]
    nodes = data["nodes"]
    vlans = data["vlans"]
    prefixes = data["prefixes"]
    links = data["links"]
    bundles = data["bundles"]
    intents = data["service_intents"]
    if not all(isinstance(value, list) for value in (asns, sites, nodes, vlans, prefixes, links, bundles, intents)):
        errors.append("asns, sites, nodes, vlans, prefixes, links, bundles, and service_intents must be arrays")
        return errors

    asn_map: dict[int, dict[str, Any]] = {}
    for item in asns:
        value = item.get("id")
        if value in asn_map:
            errors.append(f"duplicate ASN {value}")
        else:
            asn_map[value] = item
        if not isinstance(value, int) or not 64512 <= value <= 65534:
            errors.append(f"ASN {value!r} is outside the private ASN range 64512-65534")
    expected_asns = {
        65000: ("ISP-1", "isp", False),
        65001: ("ISP-2", "isp", True),
        65100: ("Headquarters", "enterprise", False),
        65101: ("Branch 1", "enterprise", False),
        65102: ("Branch 2", "enterprise", True),
    }
    if set(asn_map) != set(expected_asns):
        errors.append(f"ASN allocation must be exactly {sorted(expected_asns)}, got {sorted(asn_map)}")
    for asn, expected in expected_asns.items():
        record = asn_map.get(asn)
        if record is not None and (record.get("name"), record.get("domain"), record.get("reserved")) != expected:
            errors.append(f"ASN {asn} does not match the accepted addressing plan")
    site_map = _unique(sites, "id", "site", errors)
    node_map = _unique(nodes, "id", "node", errors)
    vlan_map = _unique(vlans, "id", "VLAN", errors)
    prefix_map = _unique(prefixes, "id", "prefix", errors)
    _unique(links, "id", "link", errors)
    bundle_map = _unique(bundles, "id", "bundle", errors)
    _unique(intents, "id", "service intent", errors)

    expected_sites = {
        "hq": {"aggregate": "10.10.0.0/16", "area": 10, "asn": 65100},
        "br1": {"aggregate": "10.20.0.0/16", "area": 20, "asn": 65101},
    }
    for site_id, expected in expected_sites.items():
        site = site_map.get(site_id)
        if site is None:
            errors.append(f"required Phase 1 site '{site_id}' is missing")
            continue
        for field, expected_value in (("aggregate", expected["aggregate"]), ("ospf_area", expected["area"]), ("asn", expected["asn"])):
            if site.get(field) != expected_value:
                errors.append(f"site {site_id} {field} must be {expected_value!r}, got {site.get(field)!r}")
        if site.get("asn") not in asn_map or asn_map[site.get("asn")].get("reserved"):
            errors.append(f"site {site_id} references an unknown or reserved ASN {site.get('asn')}")
    if set(site_map) - set(expected_sites):
        errors.append("Phase 0 inventory may only activate sites hq and br1")

    # Prefixes are hierarchical allocations. Overlap is permitted only when
    # the child explicitly names the containing allocation as its parent.
    networks: dict[str, ipaddress._BaseNetwork] = {}
    for prefix_id, prefix in prefix_map.items():
        network = _parse_network(prefix.get("cidr", ""), f"prefix {prefix_id}", errors)
        if network is not None:
            networks[prefix_id] = network
        parent = prefix.get("parent")
        if parent is not None and parent not in prefix_map:
            errors.append(f"prefix {prefix_id} references unknown parent '{parent}'")
    def is_declared_child(child_id: str, ancestor_id: str) -> bool:
        seen: set[str] = set()
        current = child_id
        while current not in seen:
            seen.add(current)
            parent = prefix_map.get(current, {}).get("parent")
            if parent is None:
                return False
            if parent == ancestor_id:
                return True
            current = parent
        return False

    for index, (left_id, left_network) in enumerate(networks.items()):
        for right_id, right_network in list(networks.items())[index + 1 :]:
            if not left_network.overlaps(right_network):
                continue
            allowed = (right_network.supernet_of(left_network) and is_declared_child(left_id, right_id)) or (left_network.supernet_of(right_network) and is_declared_child(right_id, left_id))
            if not allowed:
                errors.append(f"overlapping prefixes '{left_id}' ({left_network}) and '{right_id}' ({right_network})")

    addresses: dict[ipaddress._BaseAddress, str] = {}
    interface_addresses: dict[tuple[str, str], set[str]] = {}
    def record_address(value: str, source: str) -> None:
        address = _parse_ip(value, source, errors)
        if address is None:
            return
        previous = addresses.get(address)
        if previous is not None:
            errors.append(f"duplicate IP address {address} at {source}; already used by {previous}")
        else:
            addresses[address] = source

    for node_id, node in node_map.items():
        site = node.get("site")
        if site is not None and site not in site_map:
            errors.append(f"node {node_id} references unknown site '{site}'")
        node_asn = node.get("asn")
        if node_asn is not None and node_asn not in asn_map:
            errors.append(f"node {node_id} references unknown ASN {node_asn}")
        oob_network = _parse_network("172.31.255.0/24", "OOB management network", errors)
        oob_address = _parse_ip(node.get("oob", ""), f"node {node_id} OOB", errors)
        if oob_address is not None and oob_network is not None and oob_address not in oob_network:
            errors.append(f"node {node_id} OOB address is outside 172.31.255.0/24")
        if oob_address is not None:
            previous = addresses.get(oob_address)
            if previous is not None:
                errors.append(f"duplicate IP address {oob_address} at node {node_id} OOB; already used by {previous}")
            else:
                addresses[oob_address] = f"node {node_id} OOB"
        if node.get("loopback"):
            record_address(node["loopback"], f"node {node_id} loopback")
        interface_names: set[str] = set()
        for interface in node.get("interfaces", []):
            name = interface.get("name")
            if name in interface_names:
                errors.append(f"node {node_id} has duplicate interface '{name}'")
            interface_names.add(name)
            interface_addresses[(node_id, name)] = set(interface.get("addresses", []))
            if interface.get("kind") != "svi":
                for address in interface.get("addresses", []):
                    record_address(address, f"node {node_id} interface {name}")
        if node.get("service_address"):
            record_address(node["service_address"], f"node {node_id} service address")

    for vlan_id, vlan in vlan_map.items():
        site = vlan.get("site")
        if site not in site_map:
            errors.append(f"VLAN {vlan_id} references unknown site '{site}'")
        prefix = _parse_network(vlan.get("prefix", ""), f"VLAN {vlan_id} prefix", errors)
        expected_vlans = {10: "USERS", 20: "SERVERS", 30: "GUEST", 99: "INBAND-MGMT"}
        if vlan.get("vlan_id") not in expected_vlans or vlan.get("name") != expected_vlans.get(vlan.get("vlan_id")):
            errors.append(f"VLAN {vlan_id} does not use an accepted VLAN ID/name pair")
        if prefix is not None:
            for address_field in ("gateway",):
                address = _parse_ip(vlan.get(address_field, ""), f"VLAN {vlan_id} {address_field}", errors)
                if address is not None and address not in prefix:
                    errors.append(f"VLAN {vlan_id} {address_field} is outside {prefix}")
            for address_value in vlan.get("distribution_addresses", []):
                address = _parse_ip(address_value, f"VLAN {vlan_id} distribution address", errors)
                if address is not None and address not in prefix:
                    errors.append(f"VLAN {vlan_id} distribution address is outside {prefix}")
        for address_field in ("gateway",) + ("distribution_addresses",):
            values = vlan.get(address_field, []) if address_field == "distribution_addresses" else [vlan.get(address_field, "")]
            for value in values:
                record_address(value, f"VLAN {vlan_id} {address_field}")

    # Link peer and address references must resolve to declared node interfaces.
    used_link_endpoints: dict[tuple[str, str], str] = {}
    for link in links:
        link_id = link.get("id")
        endpoints = link.get("endpoints", [])
        if len(endpoints) != 2:
            errors.append(f"link {link_id} must have exactly two endpoints")
            continue
        endpoint_keys: set[tuple[str, str]] = set()
        for endpoint in endpoints:
            node_id = endpoint.get("node")
            interface_name = endpoint.get("interface")
            node = node_map.get(node_id)
            if node is None:
                errors.append(f"link {link_id} references unknown peer node '{node_id}'")
                continue
            interface_names = {item.get("name") for item in node.get("interfaces", [])}
            if interface_name not in interface_names:
                errors.append(f"link {link_id} references unknown interface '{node_id}:{interface_name}'")
            key = (node_id, interface_name)
            if key in endpoint_keys:
                errors.append(f"link {link_id} repeats endpoint {node_id}:{interface_name}")
            endpoint_keys.add(key)
            previous_link = used_link_endpoints.get(key)
            if previous_link is not None:
                errors.append(f"interface {node_id}:{interface_name} is reused by links {previous_link} and {link_id}")
            else:
                used_link_endpoints[key] = link_id
            if endpoint.get("address"):
                if endpoint["address"] not in interface_addresses.get((node_id, interface_name), set()):
                    errors.append(f"link {link_id} endpoint {node_id}:{interface_name} address does not match the node interface declaration")
        if link.get("kind") in {"routed", "ebgp", "xfrm"}:
            prefix_id = link.get("prefix")
            if prefix_id not in prefix_map:
                errors.append(f"link {link_id} references unknown prefix '{prefix_id}'")
            elif prefix_id in networks:
                for endpoint in endpoints:
                    address = _parse_ip(endpoint.get("address", ""), f"link {link_id} endpoint", errors)
                    if address is not None and address not in networks[prefix_id]:
                        errors.append(f"link {link_id} endpoint {address} is outside prefix {prefix_id}")

    links_by_bundle: dict[str, list[dict[str, Any]]] = {bundle_id: [] for bundle_id in bundle_map}
    for link in links:
        if link.get("kind") != "l2":
            continue
        bundle_id = link.get("bundle")
        if bundle_id not in bundle_map:
            errors.append(f"L2 link {link.get('id')} references unknown bundle '{bundle_id}'")
            continue
        links_by_bundle[bundle_id].append(link)
    expected_trunk_vlans = {10, 20, 30, 99}
    for bundle_id, bundle in bundle_map.items():
        member_links = links_by_bundle[bundle_id]
        if len(member_links) != 2:
            errors.append(f"bundle {bundle_id} must have exactly two physical L2 member links, got {len(member_links)}")
        if bundle.get("mode") != "lacp":
            errors.append(f"bundle {bundle_id} must use LACP")
        if set(bundle.get("trunk_vlans", [])) != expected_trunk_vlans:
            errors.append(f"bundle {bundle_id} trunk VLANs must be {sorted(expected_trunk_vlans)}")
        peer_nodes = set(bundle.get("peer_nodes", []))
        if len(peer_nodes) != 2:
            errors.append(f"bundle {bundle_id} must name exactly two peer nodes")
        for link in member_links:
            endpoint_nodes = {endpoint.get("node") for endpoint in link.get("endpoints", [])}
            if endpoint_nodes != peer_nodes:
                errors.append(f"bundle {bundle_id} link {link.get('id')} does not connect its declared peer nodes")
            for endpoint in link.get("endpoints", []):
                node = node_map.get(endpoint.get("node"))
                interface = next((item for item in (node or {}).get("interfaces", []) if item.get("name") == endpoint.get("interface")), None)
                if interface is not None and interface.get("kind") != "l2":
                    errors.append(f"bundle {bundle_id} endpoint {endpoint.get('node')}:{endpoint.get('interface')} must be an L2 interface")

    # Distribution peers are an L2/LACP trunk, never a routed /31. This
    # catches a regression even if someone leaves a stale routed link behind.
    for site_id in ("hq", "br1"):
        expected_peer_nodes = {f"{site_id}-dist-1", f"{site_id}-dist-2"}
        peer_bundles = [bundle for bundle in bundle_map.values() if bundle.get("site") == site_id and bundle.get("kind") == "distribution-peer"]
        if len(peer_bundles) != 1:
            errors.append(f"site {site_id} must declare exactly one distribution-peer bundle")
            continue
        peer_bundle = peer_bundles[0]
        if set(peer_bundle.get("peer_nodes", [])) != expected_peer_nodes:
            errors.append(f"distribution peer bundle {peer_bundle.get('id')} must connect {sorted(expected_peer_nodes)}")
        pair_links = [link for link in links if {endpoint.get("node") for endpoint in link.get("endpoints", [])} == expected_peer_nodes]
        if any(link.get("kind") != "l2" for link in pair_links):
            errors.append(f"distribution peers for {site_id} must not use routed links")
        if len(pair_links) != 2:
            errors.append(f"distribution peers for {site_id} must have exactly two physical links")

    for intent in intents:
        node_id = intent.get("node")
        node = node_map.get(node_id)
        if node is None:
            errors.append(f"service intent {intent.get('id')} references unknown node '{node_id}'")
        elif node.get("role") != "service" or node.get("service") != intent.get("service"):
            errors.append(f"service intent {intent.get('id')} does not match service node '{node_id}'")
        for site in intent.get("source_sites", []):
            if site not in site_map:
                errors.append(f"service intent {intent.get('id')} references unknown site '{site}'")
        if intent.get("service") == "aaa":
            for transport in intent.get("transports", []):
                if transport.get("port") in {1812, 1813} and transport.get("protocol") != "udp":
                    errors.append(f"service intent {intent.get('id')} RADIUS port {transport.get('port')} must use UDP")

    # The site aggregates and VLAN records are duplicated intentionally across
    # the hierarchy, but their canonical prefix IDs must still resolve.
    for site_id, site in site_map.items():
        aggregate_id = f"{site_id}-aggregate"
        if aggregate_id not in prefix_map or prefix_map[aggregate_id].get("cidr") != site.get("aggregate"):
            errors.append(f"site {site_id} aggregate does not match prefix {aggregate_id}")
        if site.get("public_endpoint"):
            endpoint_id = f"{site_id}-public-endpoint"
            if endpoint_id not in prefix_map or prefix_map[endpoint_id].get("cidr") != site.get("public_endpoint"):
                errors.append(f"site {site_id} public endpoint does not match prefix {endpoint_id}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--inventory", type=Path, default=root / "inventory" / "inventory.yaml")
    parser.add_argument("--schema", type=Path, default=root / "schemas" / "inventory.schema.json")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.inventory.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"inventory load failed: {exc}", file=sys.stderr)
        return 2
    errors = validate_inventory(data, args.schema)
    if errors:
        print("inventory validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"inventory valid: {args.inventory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
