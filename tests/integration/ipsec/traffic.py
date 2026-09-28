#!/usr/bin/env python3
"""Generate ICMP echo traffic from an edge network namespace using ping sockets."""

from __future__ import annotations

import argparse
import itertools
import json
import socket
import struct
import sys
import time

# Linux UAPI values from <linux/in.h>; Python 3.12 omits these constants.
IP_MTU_DISCOVER = 10
IP_PMTUDISC_DO = 2


def _socket(interface: str, timeout: float) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b"\0")
    sock.setsockopt(socket.IPPROTO_IP, IP_MTU_DISCOVER, IP_PMTUDISC_DO)
    sock.settimeout(timeout)
    # XFRM interface MTU enforces the packet-size boundary.
    return sock


def build_echo_request(sequence: int, payload: bytes) -> bytes:
    """Build the ICMP header required by Linux ping datagram sockets."""
    return struct.pack("!BBHHH", 8, 0, 0, 0, sequence) + payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    probe = subparsers.add_parser("probe")
    probe.add_argument("target")
    probe.add_argument("--interface", default="xfrm0")
    probe.add_argument("--count", type=int, default=3)
    probe.add_argument("--size", type=int, default=32)
    probe.add_argument("--timeout", type=float, default=0.5)
    probe.add_argument("--interval", type=float, default=0.2)
    probe.add_argument("--expect", choices=("up", "down"), default="up")
    stream = subparsers.add_parser("stream")
    stream.add_argument("target")
    stream.add_argument("--interface", default="xfrm0")
    stream.add_argument("--count", type=int, default=80)
    stream.add_argument("--size", type=int, default=32)
    stream.add_argument("--timeout", type=float, default=0.5)
    stream.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()
    if args.count < 1 or args.size < 0 or args.size > 64000 or args.timeout <= 0 or args.interval < 0:
        parser.error("invalid count, size, timeout, or interval")

    received: list[float] = []
    sock = _socket(args.interface, args.timeout)
    try:
        for sequence in range(args.count):
            sent = time.monotonic()
            payload = f"netlab-ipsec:{sequence}".encode().ljust(args.size, b"x")
            sock.sendto(build_echo_request(sequence + 1, payload), (args.target, 0))
            try:
                sock.recvfrom(65535)
                received.append(time.time())
            except TimeoutError:
                pass
            remaining = args.interval - (time.monotonic() - sent)
            if remaining > 0 and sequence + 1 < args.count:
                time.sleep(remaining)
    except OSError as exc:
        print(f"ICMP probe error: {exc}", file=sys.stderr)
        return 2
    finally:
        sock.close()

    gaps = [later - earlier for earlier, later in itertools.pairwise(received)]
    result = {
        "sent": args.count,
        "received": len(received),
        "loss_percent": round(100 * (args.count - len(received)) / args.count, 2),
        "max_gap_seconds": round(max(gaps, default=0.0), 3),
        "reply_timestamps": received,
    }
    print(json.dumps(result))
    if args.mode == "probe":
        passed = bool(received) if args.expect == "up" else not received
        return 0 if passed else 1
    return 0 if received else 1


if __name__ == "__main__":
    raise SystemExit(main())
