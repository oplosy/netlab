#!/usr/bin/env bash
# IMG-020 source: service role startup.
set -Eeuo pipefail
SERVICE_PORT=${SERVICE_PORT:-8080}
SERVICE_ROOT=/var/lib/netlab/service
mkdir -p "${SERVICE_ROOT}"
printf 'netlab service healthy\n' > "${SERVICE_ROOT}/health"
if [[ $# -gt 0 ]]; then
  exec "$@"
fi
exec python3 -m http.server "${SERVICE_PORT}" --bind 0.0.0.0 --directory "${SERVICE_ROOT}"
