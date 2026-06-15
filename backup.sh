#!/usr/bin/env bash
# backup.sh — create/list local borgmatic backups for matrix-easy-deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"
# shellcheck source=scripts/backup_payload.sh
source "${SCRIPT_DIR}/scripts/backup_payload.sh"
# shellcheck source=scripts/backup_crypto.sh
source "${SCRIPT_DIR}/scripts/backup_crypto.sh"

LIST_ONLY="false"
EXPORT_PATH=""
EXPORT_ONLY="false"
EXPORT_FROM_ARCHIVE=""
ENCRYPT_EXPORT="false"

BACKUP_STATE_DIR="${SCRIPT_DIR}/.matrix-easy-deploy/backup"
BACKUP_STAGING_ROOT="${BACKUP_STATE_DIR}/staging"
BACKUP_STAGING_CURRENT="${BACKUP_STAGING_ROOT}/current"
BORG_CONFIG_PATH="${BACKUP_STATE_DIR}/borgmatic.yaml"

print_help() {
    cat <<EOF
Usage:
  bash backup.sh [--list]
  bash backup.sh [--export PATH] [--export-only] [--encrypt]
  bash backup.sh --export-from-archive ARCHIVE --export PATH [--encrypt]

Options:
  --list                  List available archives in the configured repository.
  --export PATH           Write a portable .tar.gz archive after staging (or from Borg).
  --export-only PATH      Stage payload and export without updating the Borg repository.
  --export-from-archive   Re-export an existing Borg archive to a portable file.
  --encrypt               Encrypt portable export with age (passphrase).
  -h, --help              Show this help message.
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

load_backup_settings() {
    local exports
    local require_enabled="${1:-true}"

    exports="$(python3 "${SCRIPT_DIR}/scripts/backup_config.py" --deploy-yaml "${SCRIPT_DIR}/deploy.yaml" --emit-shell)"
    eval "$exports"

    if [[ "${require_enabled}" == "true" && "${BACKUP_ENABLED}" != "true" ]]; then
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
archive_name_format: 'MED_Backup_{now:%Y-%m-%dT%H:%M:%S}'
keep_daily: ${BACKUP_KEEP_DAILY}
keep_weekly: ${BACKUP_KEEP_WEEKLY}
keep_monthly: ${BACKUP_KEEP_MONTHLY}
keep_yearly: ${BACKUP_KEEP_YEARLY}
working_directory: ${BACKUP_STAGING_CURRENT}
EOF
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
        die "Archive '${requested}' does not exist. Use 'bash backup.sh --list' and pass the full archive name or a unique ID prefix."
    fi

    if [[ "$(printf '%s\n' "${matches}" | wc -l)" -gt 1 ]]; then
        error "Archive reference '${requested}' is ambiguous. Matching archive names:"
        printf '%s\n' "${matches}" >&2
        exit 1
    fi

    printf '%s\n' "${matches}"
}

export_portable_archive() {
    local export_path="$1"
    local staging_current="$2"

    local export_dir
    export_dir="$(dirname "${export_path}")"
    mkdir -p "${export_dir}"

    info "Writing portable archive to ${export_path}..."
    if [[ "${ENCRYPT_EXPORT}" == "true" ]]; then
        (
            cd "${staging_current}"
            tar -cf - payload
        ) | med_backup_encrypt_stream "${export_path}"
    else
        tar -C "${staging_current}" -cf - payload | gzip -c > "${export_path}"
    fi

    success "Portable archive written to ${export_path}"
}

export_from_borg_archive() {
    local archive_name="$1"
    local export_path="$2"

    info "Exporting Borg archive '${archive_name}' to portable format..."
    if [[ "${ENCRYPT_EXPORT}" == "true" ]]; then
        borg export-tar "${BACKUP_REPOSITORY_PATH}::${archive_name}" - | med_backup_encrypt_stream "${export_path}"
    else
        borg export-tar "${BACKUP_REPOSITORY_PATH}::${archive_name}" - | gzip -c > "${export_path}"
    fi

    success "Portable archive written to ${export_path}"
}

create_backup() {
    backup_payload_stage "${BACKUP_STAGING_CURRENT}" "${BACKUP_REPOSITORY_PATH}" "${ENCRYPT_EXPORT}"

    if [[ "${EXPORT_ONLY}" == "true" ]]; then
        [[ -n "${EXPORT_PATH}" ]] || die "--export-only requires --export PATH"
        export_portable_archive "${EXPORT_PATH}" "${BACKUP_STAGING_CURRENT}"
        return 0
    fi

    info "Ensuring local borg repository exists..."
    borgmatic --config "${BORG_CONFIG_PATH}" repo-create --encryption none

    info "Creating backup archive..."
    borgmatic --config "${BORG_CONFIG_PATH}" create --verbosity 1 --stats

    info "Applying retention policy..."
    borgmatic --config "${BORG_CONFIG_PATH}" prune

    info "Checking repository consistency..."
    borgmatic --config "${BORG_CONFIG_PATH}" check

    if [[ -n "${EXPORT_PATH}" ]]; then
        export_portable_archive "${EXPORT_PATH}" "${BACKUP_STAGING_CURRENT}"
    fi

    success "Backup completed successfully."
    list_archives
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --list)
                LIST_ONLY="true"
                ;;
            --export)
                EXPORT_PATH="${2:-}"
                [[ -n "$EXPORT_PATH" ]] || die "--export requires a path"
                shift
                ;;
            --export-only)
                EXPORT_ONLY="true"
                if [[ -n "${2:-}" && "${2}" != --* ]]; then
                    EXPORT_PATH="$2"
                    shift
                fi
                ;;
            --export-from-archive)
                EXPORT_FROM_ARCHIVE="${2:-}"
                [[ -n "$EXPORT_FROM_ARCHIVE" ]] || die "--export-from-archive requires an archive name"
                shift
                ;;
            --encrypt)
                ENCRYPT_EXPORT="true"
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

    if [[ -n "${EXPORT_FROM_ARCHIVE}" ]]; then
        require_command borg
        load_backup_settings
        write_borgmatic_config
        EXPORT_FROM_ARCHIVE="$(resolve_archive_name "${EXPORT_FROM_ARCHIVE}")"
        [[ -n "${EXPORT_PATH}" ]] || die "--export-from-archive requires --export PATH"
        export_from_borg_archive "${EXPORT_FROM_ARCHIVE}" "${EXPORT_PATH}"
        exit 0
    fi

    if [[ "${EXPORT_ONLY}" == "true" ]]; then
        load_backup_settings "false"
        [[ -n "${EXPORT_PATH}" ]] || die "--export-only requires --export PATH"
        trap cleanup_staging EXIT
        create_backup
        exit 0
    fi

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
