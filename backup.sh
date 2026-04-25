#!/usr/bin/env bash
# backup.sh — create/list local borgmatic backups for matrix-easy-deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"

KEEP_STOPPED="false"
LIST_ONLY="false"
STACK_STOPPED="false"

BACKUP_STATE_DIR="${SCRIPT_DIR}/.matrix-easy-deploy/backup"
BACKUP_STAGING_ROOT="${BACKUP_STATE_DIR}/staging"
BACKUP_STAGING_CURRENT="${BACKUP_STAGING_ROOT}/current"
BACKUP_PAYLOAD_DIR="${BACKUP_STAGING_CURRENT}/payload"
BORG_CONFIG_PATH="${BACKUP_STATE_DIR}/borgmatic.yaml"

VOLUME_EXPORTS=(
    "core_postgres_data"
    "core_redis_data"
    "caddy_data"
    "caddy_caddy_config"
)

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
  bash backup.sh [--list] [--keep-stopped]

Options:
  --list          List available archives in the configured repository.
  --keep-stopped  Do not restart services after creating a backup.
  -h, --help      Show this help message.
EOF
}

cleanup_and_restart() {
    local rc=$?

    rm -rf "${BACKUP_STAGING_CURRENT}" 2>/dev/null || true

    if [[ "${STACK_STOPPED}" == "true" && "${KEEP_STOPPED}" != "true" ]]; then
        info "Restarting services after backup flow..."
        if ! bash "${SCRIPT_DIR}/start.sh"; then
            warn "Automatic restart failed; please run 'bash start.sh' manually."
            rc=1
        fi
    fi

    exit "$rc"
}

require_command() {
    local cmd="$1"
    command -v "$cmd" &>/dev/null || die "Required command not found: ${cmd}"
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
retention:
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
  "volumes": ["core_postgres_data", "core_redis_data", "caddy_data", "caddy_caddy_config"]
}
EOF
}

list_archives() {
    info "Listing backup archives from ${BACKUP_REPOSITORY_PATH}..."
    borgmatic --config "${BORG_CONFIG_PATH}" list
}

create_backup() {
    rm -rf "${BACKUP_STAGING_CURRENT}"
    mkdir -p "${BACKUP_PAYLOAD_DIR}" "${BACKUP_PAYLOAD_DIR}/docker-volumes"

    for rel_path in "${PERSISTENT_PATHS[@]}"; do
        stage_copy_path "$rel_path"
    done

    for volume_name in "${VOLUME_EXPORTS[@]}"; do
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
            --keep-stopped)
                KEEP_STOPPED="true"
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

    load_backup_settings
    write_borgmatic_config

    if [[ "${LIST_ONLY}" == "true" ]]; then
        list_archives
        exit 0
    fi

    trap cleanup_and_restart EXIT

    info "Stopping services before backup..."
    bash "${SCRIPT_DIR}/stop.sh"
    STACK_STOPPED="true"

    create_backup
}

main "$@"
