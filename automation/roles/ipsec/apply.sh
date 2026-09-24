#!/usr/bin/env bash
# Apply the Phase 1 inventory-derived IKEv2 XFRM overlay.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
PYTHON=${PYTHON:-python3}
DOCKER=${DOCKER:-docker}

"${PYTHON}" "${ROOT}/config/ipsec/apply.py" --docker "${DOCKER}"
