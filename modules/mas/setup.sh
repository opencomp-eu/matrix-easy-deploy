#!/usr/bin/env bash
# modules/mas/setup.sh — bootstrap MAS PostgreSQL database
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${PROJECT_ROOT}/scripts/lib.sh"
source "${PROJECT_ROOT}/scripts/module_common.sh"

DEPLOY_ENV="${PROJECT_ROOT}/.env"
module_load_env "$DEPLOY_ENV" "bash apply.sh"

POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
MAS_DB_PASSWORD="${MAS_DB_PASSWORD:-}"
MAS_DB_NAME="${MAS_DB_NAME:-mas}"
MAS_DB_USER="${MAS_DB_USER:-mas}"

if [[ -z "$MAS_DB_PASSWORD" ]]; then
    die "MAS_DB_PASSWORD is required in .env. Run bash apply.sh first."
fi

ensure_postgres_role_and_database \
    "$POSTGRES_PASSWORD" \
    "$MAS_DB_USER" \
    "$MAS_DB_PASSWORD" \
    "$MAS_DB_NAME"

success "MAS database '${MAS_DB_NAME}' is ready."
