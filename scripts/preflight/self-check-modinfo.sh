#!/usr/bin/env bash
# Deterministic, read-only assertion for the unloaded-module modinfo path.
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PREFLIGHT_SCRIPT=${SCRIPT_DIR}/check.sh
MODULE=${PREFLIGHT_SELF_CHECK_MODULE:-veth}

grep -Fq 'modinfo "${module}"' "${PREFLIGHT_SCRIPT}" || {
  printf 'modinfo fallback self-check: implementation path not present\n' >&2
  exit 1
}
command -v modinfo >/dev/null 2>&1 || {
  printf 'modinfo fallback self-check: modinfo command is unavailable\n' >&2
  exit 1
}
modinfo "${MODULE}" >/dev/null 2>&1 || {
  printf 'modinfo fallback self-check: module metadata unavailable for %s\n' "${MODULE}" >&2
  exit 1
}
printf 'modinfo fallback self-check: passed for %s (read-only)\n' "${MODULE}"
