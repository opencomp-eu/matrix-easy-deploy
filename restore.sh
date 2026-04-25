#!/usr/bin/env bash
# restore.sh — restore matrix-easy-deploy from a selected local borg archive
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"

ARCHIVE_NAME=""
ASSUME_YES="false"
KEEP_STOPPED="false"
LIST_ONLY="false"
STACK_STOPPED="false"

BACKUP_STATE_DIR="${SCRIPT_DIR}/.matrix-easy-deploy/backup"
RESTORE_ROOT="${BACKUP_STATE_DIR}/restore"
BORG_CONFIG_PATH="${BACKUP_STATE_DIR}/borgmatic.yaml"

VOLUME_EXPORTS=(
    "core_postgres_data"
    "core_redis_data"
    "caddy_data"
    "caddy_caddy_config"
)

PERSISTENT_RESTORE_PATHS=(
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
  bash restore.sh --archive <archive-name> [--yes] [--keep-stopped]
  bash restore.sh --list

Options:
  --archive NAME  Archive name to restore.
  --list          List available archives in the configured repository.
  --yes           Skip destructive confirmation prompt.
  --keep-stopped  Do not restart services after restore.
  -h, --help      Show this help message.
EOF
}

cleanup_and_restart() {
    local rc=$?

    if [[ "${STACK_STOPPED}" == "true" && "${KEEP_STOPPED}" != "true" ]]; then
        info "Restarting services after restore flow..."
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
        die "Backups are disabled. Set backup.enabled=true in deploy.yaml before restoring."
    fi
}

write_borgmatic_config() {
    mkdir -p "${BACKUP_STATE_DIR}" "${BACKUP_REPOSITORY_PATH}"

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
working_directory: ${BACKUP_STATE_DIR}
EOF
}

confirm_restore() {
    [[ "${ASSUME_YES}" == "true" ]] && return 0

    echo
    warn "Restore will overwrite current runtime state with archive '${ARCHIVE_NAME}'."
    local confirm
    ask_yn confirm "Continue with restore?" "n"
    [[ "$confirm" == "y" ]] || return 1

    local final_confirm
    ask_yn final_confirm "Final confirmation: restore now?" "n"
    [[ "$final_confirm" == "y" ]]
}

list_archives() {
    info "Listing backup archives from ${BACKUP_REPOSITORY_PATH}..."
    borgmatic --config "${BORG_CONFIG_PATH}" list
}

restore_path_if_present() {
    local payload_root="$1"
    local rel_path="$2"
    local src="${payload_root}/${rel_path}"
    local dest="${SCRIPT_DIR}/${rel_path}"

    [[ -e "$src" ]] || return 0

    rm -rf "$dest"
    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
}

import_volume_if_present() {
    local payload_root="$1"
    local volume_name="$2"
    local archive_path="${payload_root}/docker-volumes/${volume_name}.tar"

    [[ -f "$archive_path" ]] || return 0

    ensure_docker_volume "$volume_name"
    info "Importing Docker volume '${volume_name}'..."
    docker run --rm \
        -v "${volume_name}:/target" \
        -v "${payload_root}/docker-volumes:/backup:ro" \
        alpine:3 sh -c "rm -rf /target/* /target/.[!.]* /target/..?* 2>/dev/null || true; cd /target && tar -xf /backup/${volume_name}.tar"
}

run_restore() {
    local restore_stage
    mkdir -p "${RESTORE_ROOT}"
    restore_stage="$(mktemp -d "${RESTORE_ROOT}/restore.XXXXXX")"

    info "Extracting archive '${ARCHIVE_NAME}'..."
    (
        cd "$restore_stage"
        borg extract "${BACKUP_REPOSITORY_PATH}::${ARCHIVE_NAME}" payload
    )

    local payload_root="${restore_stage}/payload"
    [[ -d "$payload_root" ]] || die "Archive '${ARCHIVE_NAME}' does not contain expected payload/ directory"

    # Restore deploy/state inputs first.
    restore_path_if_present "$payload_root" "deploy.yaml"
    restore_path_if_present "$payload_root" ".matrix-easy-deploy/secrets.yaml"
    restore_path_if_present "$payload_root" ".matrix-easy-deploy/modules.yaml"

    info "Regenerating runtime artifacts from restored deploy/state..."
    bash "${SCRIPT_DIR}/apply.sh"

    for volume_name in "${VOLUME_EXPORTS[@]}"; do
        import_volume_if_present "$payload_root" "$volume_name"
    done

    for rel_path in "${PERSISTENT_RESTORE_PATHS[@]}"; do
        case "$rel_path" in
            deploy.yaml|.matrix-easy-deploy/secrets.yaml|.matrix-easy-deploy/modules.yaml)
                continue
                ;;
        esac
        restore_path_if_present "$payload_root" "$rel_path"
    done

    info "Final reconciliation after payload restore..."
    bash "${SCRIPT_DIR}/apply.sh"

    success "Restore completed successfully."
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --archive)
                ARCHIVE_NAME="${2:-}"
                [[ -n "$ARCHIVE_NAME" ]] || die "--archive requires a value"
                shift
                ;;
            --list)
                LIST_ONLY="true"
                ;;
            --yes)
                ASSUME_YES="true"
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

    [[ -n "${ARCHIVE_NAME}" ]] || die "Provide --archive <archive-name> or use --list"

    confirm_restore || {
        info "Restore cancelled."
        exit 0
    }

    trap cleanup_and_restart EXIT

    info "Stopping services before restore..."
    bash "${SCRIPT_DIR}/stop.sh"
    STACK_STOPPED="true"

    run_restore
}

main "$@"
