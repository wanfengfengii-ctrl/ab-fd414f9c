#!/bin/sh
# Entry point for the `verify` acceptance service.
#
# Waits until the workbench API answers its health check, then runs the
# full acceptance suite against it over the container network.
set -eu

BASE="${VERIFY_BASE_URL:-http://app:8000}"
HEALTH_URL="${BASE}/health"

echo "verify: waiting for application at ${HEALTH_URL}"
i=0
while :; do
    if python -c "
import sys, urllib.request
try:
    r = urllib.request.urlopen('${HEALTH_URL}', timeout=2)
    sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
" ; then
        echo "verify: application healthy"
        break
    fi
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        echo "verify: timed out waiting for ${HEALTH_URL}" >&2
        exit 1
    fi
    sleep 1
done

exec python /app/acceptance/acceptance.py
