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
RESTORE_STAGE=""

BACKUP_STATE_DIR="${SCRIPT_DIR}/.matrix-easy-deploy/backup"
RESTORE_ROOT="${BACKUP_STATE_DIR}/restore"
BORG_CONFIG_PATH="${BACKUP_STATE_DIR}/borgmatic.yaml"

CADDY_VOLUME_EXPORTS=(
    "caddy_data"
    "caddy_caddy_config"
)

DATABASE_RESTORE_DIR="database"
SYNAPSE_DUMP_FILE="${DATABASE_RESTORE_DIR}/synapse.dump"

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
    --archive NAME  Archive name to restore, or a unique archive ID prefix.
    --list          List available archive names in the configured repository.
  --yes           Skip destructive confirmation prompt.
  --keep-stopped  Do not restart services after restore.
  -h, --help      Show this help message.
EOF
}

cleanup_and_restart() {
    local rc=$?

    [[ -n "${RESTORE_STAGE}" ]] && rm -rf "${RESTORE_STAGE}" 2>/dev/null || true

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

postgres_password() {
    if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
        printf '%s\n' "${POSTGRES_PASSWORD}"
        return 0
    fi

    read_state_secret "POSTGRES_PASSWORD"
}

start_postgres_restore_prerequisite() {
    local inspect_status
    local attempt=0
    local max_attempts=30

    ensure_docker_network "caddy_net"

    local docker_compose
    IFS=' ' read -ra docker_compose <<< "$(docker_compose_cmd)"

    info "Starting PostgreSQL prerequisite for restore..."
    (cd "${SCRIPT_DIR}/modules/core" && "${docker_compose[@]}" up -d postgres)

    info "Waiting for PostgreSQL to become healthy..."
    while true; do
        inspect_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' matrix_postgres 2>/dev/null || true)"
        if [[ "${inspect_status}" == "healthy" || "${inspect_status}" == "running" ]]; then
            success "PostgreSQL restore prerequisite is ready."
            return 0
        fi

        attempt=$((attempt + 1))
        if [[ ${attempt} -ge ${max_attempts} ]]; then
            die "Timed out waiting for matrix_postgres to become ready for restore."
        fi

        sleep 2
    done
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
    info "Listing backup archive names from ${BACKUP_REPOSITORY_PATH}..."
    borg list "${BACKUP_REPOSITORY_PATH}" | awk '{print $1}'
}

resolve_archive_name() {
    local requested="$1"
    local entries
    local matches

    entries="$(borg list "${BACKUP_REPOSITORY_PATH}")"

    matches="$(printf '%s\n' "${entries}" | awk -v requested="${requested}" '
        {
            archive=$1
            archive_id=$NF
            gsub(/^\[/, "", archive_id)
            gsub(/\]$/, "", archive_id)
            if (archive == requested || index(archive_id, requested) == 1) {
                print archive
            }
        }
    ')"

    if [[ -z "${matches}" ]]; then
        die "Archive '${requested}' does not exist. Use 'bash restore.sh --list' and pass the full archive name or a unique ID prefix."
    fi

    if [[ "$(printf '%s\n' "${matches}" | wc -l)" -gt 1 ]]; then
        error "Archive reference '${requested}' is ambiguous. Matching archive names:"
        printf '%s\n' "${matches}" >&2
        exit 1
    fi

    printf '%s\n' "${matches}"
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

restore_synapse_database_if_present() {
    local payload_root="$1"
    local archive_path="${payload_root}/${SYNAPSE_DUMP_FILE}"
    local postgres_password_value

    [[ -f "${archive_path}" ]] || return 0

    postgres_password_value="$(postgres_password)"
    [[ -n "${postgres_password_value}" ]] || die "POSTGRES_PASSWORD not available in .env or .matrix-easy-deploy/secrets.yaml"

    if ! docker ps --format '{{.Names}}' | grep -q '^matrix_postgres$'; then
        die "matrix_postgres is not running. Start the core stack prerequisites before restoring."
    fi

    info "Resetting Synapse PostgreSQL database..."
    docker exec -e PGPASSWORD="${postgres_password_value}" matrix_postgres \
        psql -U synapse -d postgres -c "DROP DATABASE IF EXISTS synapse WITH (FORCE);"
    docker exec -e PGPASSWORD="${postgres_password_value}" matrix_postgres \
        psql -U synapse -d postgres -c "CREATE DATABASE synapse OWNER synapse ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C' TEMPLATE template0;"

    info "Restoring Synapse PostgreSQL database..."
    docker exec -i -e PGPASSWORD="${postgres_password_value}" matrix_postgres \
        pg_restore -U synapse -d synapse --no-owner --no-privileges < "${archive_path}"
}

run_restore() {
    mkdir -p "${RESTORE_ROOT}"
    RESTORE_STAGE="$(mktemp -d "${RESTORE_ROOT}/restore.XXXXXX")"

    info "Extracting archive '${ARCHIVE_NAME}'..."
    (
        cd "$RESTORE_STAGE"
        borg extract "${BACKUP_REPOSITORY_PATH}::${ARCHIVE_NAME}" payload
    )

    local payload_root="${RESTORE_STAGE}/payload"
    [[ -d "$payload_root" ]] || die "Archive '${ARCHIVE_NAME}' does not contain expected payload/ directory"

    # Restore deploy/state inputs first.
    restore_path_if_present "$payload_root" "deploy.yaml"
    restore_path_if_present "$payload_root" ".matrix-easy-deploy/secrets.yaml"
    restore_path_if_present "$payload_root" ".matrix-easy-deploy/modules.yaml"

    info "Regenerating runtime artifacts from restored deploy/state..."
    bash "${SCRIPT_DIR}/apply.sh"

    for volume_name in "${CADDY_VOLUME_EXPORTS[@]}"; do
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

    start_postgres_restore_prerequisite
    restore_synapse_database_if_present "$payload_root"

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

    load_runtime_env
    load_backup_settings
    write_borgmatic_config

    if [[ "${LIST_ONLY}" == "true" ]]; then
        list_archives
        exit 0
    fi

    [[ -n "${ARCHIVE_NAME}" ]] || die "Provide --archive <archive-name> or use --list"

    ARCHIVE_NAME="$(resolve_archive_name "${ARCHIVE_NAME}")"

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
