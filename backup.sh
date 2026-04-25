#!/usr/bin/env bash
# backup.sh — create/list local borgmatic backups for matrix-easy-deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"

LIST_ONLY="false"

BACKUP_STATE_DIR="${SCRIPT_DIR}/.matrix-easy-deploy/backup"
BACKUP_STAGING_ROOT="${BACKUP_STATE_DIR}/staging"
BACKUP_STAGING_CURRENT="${BACKUP_STAGING_ROOT}/current"
BACKUP_PAYLOAD_DIR="${BACKUP_STAGING_CURRENT}/payload"
BORG_CONFIG_PATH="${BACKUP_STATE_DIR}/borgmatic.yaml"

CADDY_VOLUME_EXPORTS=(
    "caddy_data"
    "caddy_caddy_config"
)

DATABASE_DUMP_DIR="${BACKUP_PAYLOAD_DIR}/database"
SYNAPSE_DUMP_FILE="${DATABASE_DUMP_DIR}/synapse.dump"

PERSISTENT_PATHS=(
    "deploy.yaml"
    ".matrix-easy-deploy/secrets.yaml"
    ".matrix-easy-deploy/modules.yaml"
    "modules/core/synapse_data"
    "modules/hookshot/hookshot"
    "modules/whatsapp-bridge/whatsapp"
    "modules/slack-bridge/slack"
    "modules/draupnir/draupnir"
)

print_help() {
    cat <<EOF
Usage:
  bash backup.sh [--list]

Options:
  --list          List available archives in the configured repository.
  -h, --help      Show this help message.
EOF
}

cleanup_staging() {
    rm -rf "${BACKUP_STAGING_CURRENT}" 2>/dev/null || true
}

require_command() {
    local cmd="$1"
    command -v "$cmd" &>/dev/null || die "Required command not found: ${cmd}"
}

load_runtime_env() {
    if [[ -f "${SCRIPT_DIR}/.env" ]]; then
        set -o allexport
        # shellcheck disable=SC1090
        source "${SCRIPT_DIR}/.env"
        set +o allexport
    fi
}

read_state_secret() {
    local key="$1"
    local secrets_path="${SCRIPT_DIR}/.matrix-easy-deploy/secrets.yaml"

    [[ -f "${secrets_path}" ]] || return 1

    python3 - "$secrets_path" "$key" <<'PY'
from pathlib import Path
import sys

import yaml

data = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
value = data.get(sys.argv[2], "")
if value:
    print(value)
PY
}

load_backup_settings() {
    local exports
    exports="$(python3 "${SCRIPT_DIR}/scripts/backup_config.py" --deploy-yaml "${SCRIPT_DIR}/deploy.yaml" --emit-shell)"
    eval "$exports"

    if [[ "${BACKUP_ENABLED}" != "true" ]]; then
        die "Backups are disabled. Set backup.enabled=true in deploy.yaml."
    fi
}

write_borgmatic_config() {
    mkdir -p "${BACKUP_STATE_DIR}" "${BACKUP_STAGING_CURRENT}" "${BACKUP_REPOSITORY_PATH}"

    cat > "${BORG_CONFIG_PATH}" <<EOF
source_directories:
  - payload
repositories:
  - path: ${BACKUP_REPOSITORY_PATH}
keep_daily: ${BACKUP_KEEP_DAILY}
keep_weekly: ${BACKUP_KEEP_WEEKLY}
keep_monthly: ${BACKUP_KEEP_MONTHLY}
keep_yearly: ${BACKUP_KEEP_YEARLY}
working_directory: ${BACKUP_STAGING_CURRENT}
EOF
}

stage_copy_path() {
    local rel_path="$1"
    local src="${SCRIPT_DIR}/${rel_path}"
    local dest="${BACKUP_PAYLOAD_DIR}/${rel_path}"

    [[ -e "$src" ]] || return 0

    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
}

export_volume_if_present() {
    local volume_name="$1"
    local target_dir="$2"

    if ! docker volume inspect "$volume_name" &>/dev/null; then
        return 0
    fi

    info "Exporting Docker volume '${volume_name}'..."
    docker run --rm \
        -v "${volume_name}:/source:ro" \
        -v "${target_dir}:/backup" \
        alpine:3 sh -c "cd /source && tar -cf /backup/${volume_name}.tar ."
}

dump_synapse_database() {
    local postgres_password="${POSTGRES_PASSWORD:-}"

    if [[ -z "${postgres_password}" ]]; then
        postgres_password="$(read_state_secret "POSTGRES_PASSWORD")"
    fi
    [[ -n "${postgres_password}" ]] || die "POSTGRES_PASSWORD not available in .env or .matrix-easy-deploy/secrets.yaml"

    if ! docker ps --format '{{.Names}}' | grep -q '^matrix_postgres$'; then
        die "matrix_postgres is not running. Start the core stack before creating a live backup."
    fi

    mkdir -p "${DATABASE_DUMP_DIR}"

    info "Creating logical PostgreSQL dump for Synapse..."
    docker exec -e PGPASSWORD="${postgres_password}" matrix_postgres \
        pg_dump -U synapse -d synapse -Fc --exclude-table-data=e2e_one_time_keys_json \
        > "${SYNAPSE_DUMP_FILE}"
}

write_manifest() {
    local manifest_path="${BACKUP_PAYLOAD_DIR}/manifest.json"
    local timestamp
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local version="unknown"

    if [[ -f "${SCRIPT_DIR}/VERSION" ]]; then
        version="$(tr -d ' \n\r' < "${SCRIPT_DIR}/VERSION")"
    fi

    cat > "$manifest_path" <<EOF
{
  "format": 1,
  "created_at": "${timestamp}",
  "version": "${version}",
  "repository_path": "${BACKUP_REPOSITORY_PATH}",
    "database_dumps": ["database/synapse.dump"],
    "volumes": ["caddy_data", "caddy_caddy_config"]
}
EOF
}

list_archives() {
    info "Listing backup archive names from ${BACKUP_REPOSITORY_PATH}..."
    borg list "${BACKUP_REPOSITORY_PATH}" | awk '{print $1}'
}

create_backup() {
    rm -rf "${BACKUP_STAGING_CURRENT}"
    mkdir -p "${BACKUP_PAYLOAD_DIR}" "${BACKUP_PAYLOAD_DIR}/docker-volumes"

    for rel_path in "${PERSISTENT_PATHS[@]}"; do
        stage_copy_path "$rel_path"
    done

    dump_synapse_database

    for volume_name in "${CADDY_VOLUME_EXPORTS[@]}"; do
        export_volume_if_present "$volume_name" "${BACKUP_PAYLOAD_DIR}/docker-volumes"
    done

    write_manifest

    info "Ensuring local borg repository exists..."
    borgmatic --config "${BORG_CONFIG_PATH}" repo-create --encryption none

    info "Creating backup archive..."
    borgmatic --config "${BORG_CONFIG_PATH}" create --verbosity 1 --stats

    info "Applying retention policy..."
    borgmatic --config "${BORG_CONFIG_PATH}" prune

    info "Checking repository consistency..."
    borgmatic --config "${BORG_CONFIG_PATH}" check

    success "Backup completed successfully."
    list_archives
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --list)
                LIST_ONLY="true"
                ;;
            -h|--help)
                print_help
                exit 0
                ;;
            *)
                die "Unknown argument: $1"
                ;;
        esac
        shift
    done

    require_command python3
    require_command docker
    require_command borgmatic
    require_command borg

    load_runtime_env
    load_backup_settings
    write_borgmatic_config

    if [[ "${LIST_ONLY}" == "true" ]]; then
        list_archives
        exit 0
    fi

    trap cleanup_staging EXIT

    create_backup
}

main "$@"
