#!/usr/bin/env python3
"""Small AF_PACKET capture helper for the isolated ISP container."""

from __future__ import annotations

import argparse
import json
import select
import socket
import struct
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("duration", type=float)
    parser.add_argument("interfaces", nargs="+", choices=("eth1", "eth2", "eth3", "eth4", "eth5", "eth6"))
    args = parser.parse_args()
    if not 1 <= args.duration <= 60 or len(set(args.interfaces)) != len(args.interfaces):
        parser.error("duration must be 1..60 seconds and interfaces must be unique")

    sockets: list[socket.socket] = []
    try:
        for interface in args.interfaces:
            capture_socket = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
            capture_socket.bind((interface, 0))
            sockets.append(capture_socket)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        packets = 0
        with args.output.open("wb") as output:
            output.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline:
                readable, _, _ = select.select(sockets, [], [], min(0.25, deadline - time.monotonic()))
                for capture_socket in readable:
                    frame = capture_socket.recv(65535)
                    timestamp = time.time()
                    seconds = int(timestamp)
                    micros = int((timestamp - seconds) * 1_000_000)
                    output.write(struct.pack("<IIII", seconds, micros, len(frame), len(frame)))
                    output.write(frame)
                    packets += 1
            output.flush()
        print(json.dumps({"output": str(args.output), "interfaces": args.interfaces, "packets": packets}))
    except OSError as exc:
        print(f"underlay capture failed: {exc}")
        return 1
    finally:
        for capture_socket in sockets:
            capture_socket.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
