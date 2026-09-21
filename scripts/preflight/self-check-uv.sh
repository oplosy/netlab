#!/usr/bin/env bash
# Static, read-only assertion for the uv policy branch in preflight.
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PREFLIGHT_SCRIPT=${SCRIPT_DIR}/check.sh
VERSION_FILE=${SCRIPT_DIR}/../../versions.env

grep -Fq 'UV_VERSION=' "${VERSION_FILE}" || {
  printf 'uv preflight self-check: UV_VERSION policy is missing\n' >&2
  exit 1
}
grep -Fq 'command -v uv' "${PREFLIGHT_SCRIPT}" || {
  printf 'uv preflight self-check: command detection is missing\n' >&2
  exit 1
}
grep -Fq 'uv --version' "${PREFLIGHT_SCRIPT}" || {
  printf 'uv preflight self-check: version probe is missing\n' >&2
  exit 1
}
grep -Fq "check_exact_version 'uv'" "${PREFLIGHT_SCRIPT}" || {
  printf 'uv preflight self-check: exact version assertion is missing\n' >&2
  exit 1
}
printf 'uv preflight self-check: passed (read-only)\n'
