#!/usr/bin/env python3
"""Measure ICMP reply gaps from a routed lab interface."""

from __future__ import annotations

import argparse
import itertools
import json
import socket
import struct
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    parser.add_argument("--interface", default="xfrm0")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=0.25)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, args.interface.encode() + b"\0")
    sock.settimeout(args.timeout)
    started = time.time()
    received: list[float] = []
    try:
        for sequence in range(1, args.count + 1):
            sent = time.monotonic()
            request = struct.pack("!BBHHH", 8, 0, 0, 0, sequence) + b"netlab-ospf"
            sock.sendto(request, (args.target, 0))
            try:
                sock.recvfrom(65535)
                received.append(time.time())
            except TimeoutError:
                pass
            delay = args.interval - (time.monotonic() - sent)
            if delay > 0 and sequence < args.count:
                time.sleep(delay)
    finally:
        sock.close()

    finished = time.time()
    gaps = [later - earlier for earlier, later in itertools.pairwise(received)]
    if received:
        gaps.extend((received[0] - started, finished - received[-1]))
    print(json.dumps({
        "target": args.target,
        "interface": args.interface,
        "sent": args.count,
        "received": len(received),
        "loss_percent": round(100 * (args.count - len(received)) / args.count, 2),
        "interval_seconds": args.interval,
        "max_reply_gap_seconds": round(max(gaps, default=0.0), 3),
        "started_timestamp": started,
        "finished_timestamp": finished,
        "reply_timestamps": received,
    }))
    return 0 if received else 1


if __name__ == "__main__":
    raise SystemExit(main())
