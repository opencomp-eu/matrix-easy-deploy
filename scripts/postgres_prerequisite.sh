#!/usr/bin/env bash
# scripts/postgres_prerequisite.sh — backup plan postgres_start hook.
# Ensures matrix_postgres is up before backup dumps / restore pg_restore steps.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"
# shellcheck source=scripts/module_common.sh
source "${SCRIPT_DIR}/scripts/module_common.sh"

# Env wins; fall back to the persisted state secrets.
if [[ -z "${POSTGRES_PASSWORD:-}" && -f "${SCRIPT_DIR}/.matrix-easy-deploy/secrets.yaml" ]]; then
    POSTGRES_PASSWORD="$(python3 "${SCRIPT_DIR}/scripts/state_secrets.py" \
        --secrets-file "${SCRIPT_DIR}/.matrix-easy-deploy/secrets.yaml" \
        --get POSTGRES_PASSWORD 2>/dev/null || true)"
    [[ -n "${POSTGRES_PASSWORD}" ]] && export POSTGRES_PASSWORD
fi

ensure_postgres_prerequisite "${SCRIPT_DIR}"
