#!/usr/bin/env python3
"""AUTO-510: wait until the NetBox API answers an authenticated status call.

The first start runs database migrations and can take several minutes.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("NETBOX_URL", sync.DEFAULT_URL))
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    nb = sync.NetBox(args.url, sync._token())
    started = time.monotonic()
    last = ""
    while time.monotonic() - started < args.timeout:
        try:
            status = nb.request("GET", "status/")
            print(f"NetBox {status.get('netbox-version')} ready after "
                  f"{time.monotonic() - started:.0f}s")
            return 0
        except (OSError, RuntimeError) as exc:
            last = str(exc)
        time.sleep(5)
    print(f"NetBox not ready after {args.timeout:.0f}s: {last}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
