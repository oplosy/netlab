#!/usr/bin/env bash
# IMG-020 source: client role health assertion.
set -Eeuo pipefail
ip link show lo >/dev/null
getent hosts localhost >/dev/null
printf 'healthy client\n'
