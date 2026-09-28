#!/usr/bin/env python3
"""AUTO-510: reconcile NetBox with Git intent, or report drift with --check.

NetBox is a rebuildable projection (ADR 0010, ADR 0019). ``sync`` makes NetBox
match ``inventory/inventory.yaml``; a second run makes no change. ``--check``
writes nothing and exits 3 when NetBox differs from intent (for example after a
NetBox-only edit).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import projection  # noqa: E402

ROOT = projection.ROOT
ENV_FILE = ROOT / "artifacts" / "netbox" / "netbox.env"
DEFAULT_URL = "http://127.0.0.1:18080"
ENDPOINTS = {
    "tags": "extras/tags",
    "sites": "dcim/sites",
    "manufacturers": "dcim/manufacturers",
    "device_types": "dcim/device-types",
    "roles": "dcim/device-roles",
    "devices": "dcim/devices",
    "interfaces": "dcim/interfaces",
    "ip_addresses": "ipam/ip-addresses",
    "vlans": "ipam/vlans",
    "prefixes": "ipam/prefixes",
}
DRIFT_EXIT = 3


def _token() -> str:
    token = os.environ.get("NETBOX_TOKEN")
    if token:
        return token
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SUPERUSER_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"no NETBOX_TOKEN and no SUPERUSER_API_TOKEN in {ENV_FILE}")


class NetBox:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.headers = {
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, body: Any = None) -> Any:
        url = path if path.startswith("http") else f"{self.url}/api/{path}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{method} {url}: {exc.code} {exc.read().decode()[:500]}") from exc
        return json.loads(payload) if payload else None

    def all(self, model: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        # No "brief" parameter at all: NetBox treats any value, even "false",
        # as brief mode and omits fields such as a role's color.
        next_url: str | None = f"{ENDPOINTS[model]}/?limit=1000"
        while next_url:
            page = self.request("GET", next_url)
            results.extend(page["results"])
            next_url = page.get("next")
        return results


def _slug(obj: dict[str, Any] | None) -> str | None:
    return obj.get("slug") if obj else None


def observed(nb: NetBox) -> tuple[dict[str, dict[Any, dict[str, Any]]], dict[str, dict[Any, int]]]:
    """Current NetBox state keyed and shaped like projection.desired(), plus ids."""
    have: dict[str, dict[Any, dict[str, Any]]] = {m: {} for m in projection.MODELS}
    ids: dict[str, dict[Any, int]] = {m: {} for m in projection.MODELS}

    def put(model: str, key: Any, fields: dict[str, Any], ident: int) -> None:
        have[model][key] = fields
        ids[model][key] = ident

    for obj in nb.all("sites"):
        put("sites", obj["slug"], {"name": obj["name"], "description": obj["description"]}, obj["id"])
    for obj in nb.all("roles"):
        put("roles", obj["slug"], {"name": obj["name"], "color": obj["color"]}, obj["id"])
    for obj in nb.all("devices"):
        put(
            "devices",
            obj["name"],
            {
                "role": _slug(obj.get("role")),
                "site": _slug(obj.get("site")),
                "device_type": _slug(obj.get("device_type")),
                "description": obj["description"],
            },
            obj["id"],
        )
    for obj in nb.all("interfaces"):
        put(
            "interfaces",
            (obj["device"]["name"], obj["name"]),
            {"type": obj["type"]["value"], "description": obj["description"]},
            obj["id"],
        )
    for obj in nb.all("ip_addresses"):
        assigned = obj.get("assigned_object") or {}
        device = (assigned.get("device") or {}).get("name")
        put(
            "ip_addresses",
            (device, assigned.get("name"), obj["address"]),
            {"description": obj["description"]},
            obj["id"],
        )
    for obj in nb.all("vlans"):
        put(
            "vlans",
            (_slug(obj.get("site")), obj["vid"]),
            {"name": obj["name"], "description": obj["description"]},
            obj["id"],
        )
    for obj in nb.all("prefixes"):
        put("prefixes", obj["prefix"], {"description": obj["description"]}, obj["id"])
    return have, ids


def _ensure_support(nb: NetBox) -> dict[str, int]:
    """Tag, manufacturer, and device type that every managed object refers to."""
    support: dict[str, int] = {}
    specs = [
        ("tags", {"name": projection.TAG, "slug": projection.TAG, "color": "607d8b"}),
        ("manufacturers", projection.MANUFACTURER),
    ]
    for model, body in specs:
        found = next((o for o in nb.all(model) if o["slug"] == body["slug"]), None)
        support[model] = (found or nb.request("POST", f"{ENDPOINTS[model]}/", body))["id"]
    found = next(
        (o for o in nb.all("device_types") if o["slug"] == projection.DEVICE_TYPE["slug"]), None
    )
    body = {**projection.DEVICE_TYPE, "manufacturer": support["manufacturers"]}
    support["device_types"] = (found or nb.request("POST", "dcim/device-types/", body))["id"]
    return support


def _payload(
    model: str, key: Any, fields: dict[str, Any], ids: dict[str, dict[Any, int]], support: dict[str, int]
) -> dict[str, Any]:
    tags = [{"slug": projection.TAG}]
    if model == "sites":
        return {"slug": key, "status": "active", "tags": tags, **fields}
    if model == "roles":
        return {"slug": key, **fields}
    if model == "devices":
        return {
            "name": key,
            "status": "active",
            "role": ids["roles"][fields["role"]],
            "site": ids["sites"][fields["site"]],
            "device_type": support["device_types"],
            "description": fields["description"],
            "tags": tags,
        }
    if model == "interfaces":
        device, name = key
        return {"device": ids["devices"][device], "name": name, "tags": tags, **fields}
    if model == "ip_addresses":
        device, iface, address = key
        return {
            "address": address,
            "status": "active",
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": ids["interfaces"][(device, iface)],
            "tags": tags,
            **fields,
        }
    if model == "vlans":
        site, vid = key
        return {"site": ids["sites"][site], "vid": vid, "status": "active", "tags": tags, **fields}
    if model == "prefixes":
        return {"prefix": key, "status": "active", "tags": tags, **fields}
    raise ValueError(model)


def reconcile(nb: NetBox, want: dict[str, dict[Any, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Apply the diff model by model; returns the changes that were made."""
    support = _ensure_support(nb)
    applied: list[dict[str, Any]] = []
    have, ids = observed(nb)
    for change in projection.diff(want, have):
        model, key, action = change["model"], change["key"], change["action"]
        path = ENDPOINTS[model]
        if action == "delete":
            nb.request("DELETE", f"{path}/{ids[model][key]}/")
        elif action == "update":
            body = _payload(model, key, want[model][key], ids, support)
            nb.request("PATCH", f"{path}/{ids[model][key]}/", body)
        else:
            body = _payload(model, key, change["fields"], ids, support)
            ids[model][key] = nb.request("POST", f"{path}/", body)["id"]
        applied.append(change)
    return applied


def _render(change: dict[str, Any]) -> str:
    key = change["key"] if isinstance(change["key"], str) else "/".join(map(str, change["key"]))
    return f"{change['action']:<6} {change['model']:<12} {key} {json.dumps(change['fields'], sort_keys=True)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("NETBOX_URL", DEFAULT_URL))
    parser.add_argument("--inventory", type=Path, default=projection.INVENTORY)
    parser.add_argument("--check", action="store_true", help="report drift; change nothing")
    parser.add_argument("--json", type=Path, help="also write the change list as JSON")
    args = parser.parse_args()
    nb = NetBox(args.url, _token())
    want = projection.desired(projection.load_inventory(args.inventory))
    if args.check:
        have, _ = observed(nb)
        changes = projection.diff(want, have)
    else:
        changes = reconcile(nb, want)
    for change in changes:
        print(_render(change))
    summary = {
        "mode": "check" if args.check else "sync",
        "changes": len(changes),
        "by_action": {a: sum(c["action"] == a for c in changes) for a in ("create", "update", "delete")},
    }
    print(json.dumps(summary))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({**summary, "detail": [_render(c) for c in changes]}, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.check and changes:
        print("NetBox drifted from Git intent", file=sys.stderr)
        return DRIFT_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
