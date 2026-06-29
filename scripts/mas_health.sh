#!/usr/bin/env bash
# scripts/mas_health.sh — probe MAS HTTP /health via the homeserver container network
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

HS_CONTAINER="${HOMESERVER_CONTAINER:-matrix_synapse}"

if ! docker inspect "$HS_CONTAINER" &>/dev/null; then
    die "Homeserver container '${HS_CONTAINER}' is not running."
fi

if ! docker inspect matrix_mas &>/dev/null; then
    die "MAS container 'matrix_mas' is not running."
fi

response="$(docker exec "$HS_CONTAINER" python3 -c \
    'import urllib.request; print(urllib.request.urlopen("http://matrix_mas:8080/health", timeout=5).read().decode())')"
success "MAS health: ${response}"
