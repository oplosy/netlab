# VPN-150 live acceptance evidence

Date: 2026-09-24

## Runtime

- Runtime: Docker Engine 29.6.2 inside Ubuntu 26.04 on WSL2.
- Kernel XFRM interface and user configuration support were present.
- The lab was deployed with `make lab-up PYTHON=python3` and remained running during acceptance.
- No Docker Desktop, Docker Engine, WSL, or kernel settings were changed.

## Commands and results

- `python3 tests/integration/ipsec/measure.py` — passed.
- `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -q -p no:cacheprovider tests/integration/ipsec/test_plan.py` — 7 passed.
- The live measurement verified that peer traffic is denied before CHILD_SA establishment, passes after establishment, and is denied again after SA termination.
- 1372-byte ICMP payloads passed across the XFRM interface; a 1373-byte DF packet was rejected at the configured 1400-byte interface MTU.
- TCP MSS clamp: 1360 bytes.
- CHILD_SA rekey traffic: 80 probes, maximum observed gap 0.1 seconds; rekey command completed in 0.237 seconds.
- SA termination and re-establishment recovery passed.
- Underlay capture: 364 ESP packets, 8 IKE packets, 0 cleartext enterprise packets.
- Capture file: `/tmp/netlab-ipsec-underlay.pcap` in the Ubuntu WSL environment; it was inspected and not added to the repository.

## Scope and limitations

This is live verification of the HQ–BR1 pair in the Phase 1 lab. It does not claim verification of future sites or a Windows-native Docker runtime. The normal `make test-ipsec` target is not yet present in the integration branch; the executable live acceptance script was run directly.
