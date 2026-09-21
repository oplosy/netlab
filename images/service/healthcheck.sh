#!/usr/bin/env bash
# IMG-020 source: service role health assertion.
set -Eeuo pipefail
SERVICE_PORT=${SERVICE_PORT:-8080}
curl --fail --silent --show-error "http://127.0.0.1:${SERVICE_PORT}/health" >/dev/null
printf 'healthy service port=%s\n' "${SERVICE_PORT}"
