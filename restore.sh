#!/usr/bin/env bash
# restore.sh — restore matrix-easy-deploy from a Borg archive or portable file
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"
# shellcheck source=scripts/restore_payload.sh
source "${SCRIPT_DIR}/scripts/restore_payload.sh"
# shellcheck source=scripts/backup_crypto.sh
source "${SCRIPT_DIR}/scripts/backup_crypto.sh"

ARCHIVE_NAME=""
PORTABLE_FILE=""
ASSUME_YES="false"
KEEP_STOPPED="false"
LIST_ONLY="false"
STACK_STOPPED="false"
RESTORE_STAGE=""
ENCRYPTED_FILE="false"
PASSPHRASE_FILE=""

BACKUP_STATE_DIR="${SCRIPT_DIR}/.matrix-easy-deploy/backup"
RESTORE_ROOT="${BACKUP_STATE_DIR}/restore"
BORG_CONFIG_PATH="${BACKUP_STATE_DIR}/borgmatic.yaml"

print_help() {
    cat <<EOF
Usage:
  bash restore.sh --archive <archive-name> [--yes] [--keep-stopped]
  bash restore.sh --file <portable-archive> [--encrypt] [--passphrase-file PATH] [--yes] [--keep-stopped]
  bash restore.sh --list

Options:
  --archive NAME        Archive name to restore, or a unique archive ID prefix.
  --file PATH           Restore from a portable .tar.gz or .tar.gz.age archive.
  --list                List available archive names in the configured repository.
  --encrypt             Decrypt an age-encrypted portable archive (.tar.gz.age).
  --passphrase-file     File containing the age passphrase (non-interactive decrypt).
  --yes                 Skip destructive confirmation prompt.
  --keep-stopped        Do not restart services after restore.
  -h, --help            Show this help message.
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

print_post_restore_note() {
    echo
    warn "Existing logged-in sessions can keep showing rooms or messages that no longer exist on the restored server."
    warn "Logging out and back in usually resolves that stale client state."
    warn "For encrypted history on a new login, users typically need another verified session or their recovery key/secret storage; this is not controlled by registration tokens."
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
archive_name_format: 'MED_Backup_{now:%Y-%m-%dT%H:%M:%S}'
keep_daily: ${BACKUP_KEEP_DAILY}
keep_weekly: ${BACKUP_KEEP_WEEKLY}
keep_monthly: ${BACKUP_KEEP_MONTHLY}
keep_yearly: ${BACKUP_KEEP_YEARLY}
working_directory: ${BACKUP_STATE_DIR}
EOF
}

confirm_restore() {
    [[ "${ASSUME_YES}" == "true" ]] && return 0

    local target="${ARCHIVE_NAME:-${PORTABLE_FILE}}"
    echo
    warn "Restore will overwrite current runtime state with '${target}'."
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

detect_encrypted_portable_file() {
    local file_path="$1"

    if [[ "${ENCRYPTED_FILE}" == "true" ]]; then
        return 0
    fi

    if med_backup_is_encrypted_path "${file_path}"; then
        return 0
    fi

    if med_backup_is_openssl_encrypted "${file_path}"; then
        return 0
    fi

    return 1
}

extract_portable_file() {
    local file_path="$1"
    local extract_dir="$2"

    [[ -f "${file_path}" ]] || die "Portable archive not found: ${file_path}"

    mkdir -p "${extract_dir}"

    if detect_encrypted_portable_file "${file_path}"; then
        info "Decrypting and extracting portable archive..."
        med_backup_decrypt_stream "${file_path}" | tar -xf - -C "${extract_dir}"
    else
        info "Extracting portable archive..."
        gzip -dc "${file_path}" | tar -xf - -C "${extract_dir}"
    fi
}

run_restore_from_borg() {
    mkdir -p "${RESTORE_ROOT}"
    RESTORE_STAGE="$(mktemp -d "${RESTORE_ROOT}/restore.XXXXXX")"

    info "Extracting archive '${ARCHIVE_NAME}'..."
    (
        cd "$RESTORE_STAGE"
        borg extract "${BACKUP_REPOSITORY_PATH}::${ARCHIVE_NAME}" payload
    )

    local payload_root="${RESTORE_STAGE}/payload"
    [[ -d "$payload_root" ]] || die "Archive '${ARCHIVE_NAME}' does not contain expected payload/ directory"

    restore_payload_from_directory "${payload_root}"
}

run_restore_from_file() {
    mkdir -p "${RESTORE_ROOT}"
    RESTORE_STAGE="$(mktemp -d "${RESTORE_ROOT}/restore.XXXXXX")"

    extract_portable_file "${PORTABLE_FILE}" "${RESTORE_STAGE}"

    local payload_root="${RESTORE_STAGE}/payload"
    [[ -d "$payload_root" ]] || die "Portable archive does not contain expected payload/ directory"

    restore_payload_from_directory "${payload_root}"
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --archive)
                ARCHIVE_NAME="${2:-}"
                [[ -n "$ARCHIVE_NAME" ]] || die "--archive requires a value"
                shift
                ;;
            --file)
                PORTABLE_FILE="${2:-}"
                [[ -n "$PORTABLE_FILE" ]] || die "--file requires a value"
                shift
                ;;
            --list)
                LIST_ONLY="true"
                ;;
            --encrypt)
                ENCRYPTED_FILE="true"
                ;;
            --passphrase-file)
                PASSPHRASE_FILE="${2:-}"
                [[ -n "$PASSPHRASE_FILE" ]] || die "--passphrase-file requires a value"
                shift
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

    if [[ -n "${PORTABLE_FILE}" && -n "${ARCHIVE_NAME}" ]]; then
        die "Provide either --archive or --file, not both."
    fi

    if [[ "${LIST_ONLY}" == "true" ]]; then
        require_command borg
        require_command borgmatic
        load_backup_settings
        write_borgmatic_config
        list_archives
        exit 0
    fi

    if [[ -n "${PORTABLE_FILE}" ]]; then
        confirm_restore || {
            info "Restore cancelled."
            exit 0
        }

        trap cleanup_and_restart EXIT

        if [[ -f "${SCRIPT_DIR}/deploy.yaml" ]] && docker ps -q 2>/dev/null | grep -q .; then
            info "Stopping services before restore..."
            bash "${SCRIPT_DIR}/stop.sh" || true
            STACK_STOPPED="true"
        elif [[ -f "${SCRIPT_DIR}/stop.sh" ]]; then
            info "Stopping services before restore..."
            bash "${SCRIPT_DIR}/stop.sh" || true
            STACK_STOPPED="true"
        fi

        run_restore_from_file
        success "Restore completed successfully."
        print_post_restore_note
        exit 0
    fi

    [[ -n "${ARCHIVE_NAME}" ]] || die "Provide --archive <archive-name>, --file <portable-archive>, or use --list"

    require_command borgmatic
    require_command borg

    load_runtime_env
    load_backup_settings
    write_borgmatic_config

    ARCHIVE_NAME="$(resolve_archive_name "${ARCHIVE_NAME}")"

    confirm_restore || {
        info "Restore cancelled."
        exit 0
    }

    trap cleanup_and_restart EXIT

    info "Stopping services before restore..."
    bash "${SCRIPT_DIR}/stop.sh"
    STACK_STOPPED="true"

    run_restore_from_borg
    success "Restore completed successfully."
    print_post_restore_note
}

main "$@"
