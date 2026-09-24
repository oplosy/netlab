#!/bin/sh
# Keepalived health check: only an installed OSPF route to the remote site counts.
set -eu

[ "$#" -eq 1 ] || exit 2
ip -4 route show exact "$1" | grep -q ' proto ospf '
