#!/usr/bin/env bash
# IMG-020 source: client role startup.
set -Eeuo pipefail
if [[ $# -gt 0 ]]; then
  exec "$@"
fi
exec tail -f /dev/null
