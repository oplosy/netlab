#!/usr/bin/env bash
# Start DHCP relay only on VLANs whose VRRP VIP is local.
set -Eeuo pipefail

hooks=/run/netlab/dhcp-relay-hooks.json
state=/run/netlab/dhcp-relay-state
mapfile -t desired < <(python3 - "${hooks}" <<'PY'
import ipaddress
import json
import subprocess
import sys

hooks = json.load(open(sys.argv[1], encoding="utf-8"))
enabled = [hook for hook in hooks if hook["enabled"]]
servers = {hook["server"] for hook in enabled}
if len(servers) > 1:
    raise SystemExit("multiple DHCP relay targets on one gateway")
if not servers:
    raise SystemExit(0)
print(next(iter(servers)))
for hook in sorted(enabled, key=lambda item: item["vlan_id"]):
    vip = ipaddress.ip_interface(hook["virtual_ip"]).ip
    addresses = subprocess.run(
        ["ip", "-o", "-4", "address", "show", "dev", hook["interface"]],
        capture_output=True, text=True, check=True,
    ).stdout
    if f"inet {vip}/" in addresses:
        print(hook["interface"])
PY
)

server=${desired[0]:-}
interfaces=("${desired[@]:1}")
state=/run/netlab/dhcp-relay-state
old=
pid=
if [[ -s "${state}" ]]; then read -r old pid _ < "${state}"; fi
wanted=$(printf '%s\n' "${server}" "${interfaces[@]}" | sha256sum | cut -d ' ' -f1)
if [[ "${old}" == "${wanted}" && -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
  exit 0
fi
if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null &&
   tr '\000' ' ' < "/proc/${pid}/cmdline" | grep -q '/usr/sbin/dhcrelay'; then
  kill "${pid}"
  for _ in {1..20}; do kill -0 "${pid}" 2>/dev/null || break; sleep 0.1; done
fi
rm -f "${state}"
if [[ -z "${server}" || ${#interfaces[@]} -eq 0 ]]; then exit 0; fi
args=(-4 -d)
for interface in "${interfaces[@]}"; do args+=(-i "${interface}"); done
/usr/sbin/dhcrelay "${args[@]}" "${server}" >/var/log/netlab/dhcrelay.log 2>&1 &
pid=$!
sleep 0.2
kill -0 "${pid}" 2>/dev/null || { cat /var/log/netlab/dhcrelay.log >&2; exit 1; }
printf '%s %s %s\n' "${wanted}" "${pid}" "${interfaces[*]}" > "${state}"
