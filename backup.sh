#!/usr/bin/env bash
# backup.sh — create/list matrix-easy-deploy backups (shared easydeploy-lib engine)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"

LIST_ONLY="false"
EXPORT_PATH=""
EXPORT_ONLY="false"
EXPORT_FROM_ARCHIVE=""
ENCRYPT_EXPORT="false"
COLD="false"
SCHEDULE_ONLY="false"

BACKUP_STATE_DIR="${SCRIPT_DIR}/.matrix-easy-deploy/backup"
SECRETS_FILE_PATH="${SCRIPT_DIR}/.matrix-easy-deploy/secrets.yaml"
BACKUP_STAGING_ROOT="${BACKUP_STATE_DIR}/staging"
BACKUP_STAGING_CURRENT="${BACKUP_STAGING_ROOT}/current"
BORG_CONFIG_PATH="${BACKUP_STATE_DIR}/borgmatic.yaml"

print_help() {
    cat <<EOF
Usage:
  bash backup.sh [--list]
  bash backup.sh [--export PATH] [--export-only] [--encrypt] [--cold]
  bash backup.sh --export-from-archive ARCHIVE --export PATH [--encrypt]
  bash backup.sh --schedule

Options:
  --list                  List available archives in the configured repository.
  --export PATH           Write a portable .tar.gz archive after staging (or from Borg).
  --export-only PATH      Stage payload and export without updating the Borg repository.
  --export-from-archive   Re-export an existing Borg archive to a portable file.
  --encrypt               Encrypt portable export with age (passphrase).
  --cold                  Stop services before staging and start them afterwards.
  --schedule              Reconcile the automatic backup systemd timer from deploy.yaml.
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
        load_deploy_env "${SCRIPT_DIR}/.env"
    fi
}

load_backup_settings() {
    local require_enabled="${1:-true}"

    eval "$(easydeploy_backup_settings_shell "${SCRIPT_DIR}/deploy.yaml")"

    if [[ "${require_enabled}" == "true" && "${BACKUP_ENABLED}" != "true" ]]; then
        die "Backups are disabled. Set backup.enabled=true in deploy.yaml."
    fi
}

load_plan_json() {
    PLAN_JSON="$(mktemp)"
    easydeploy_backup_py "${EASYDEPLOY_LIB}/python/backup_plan.py" \
        --project-root "${SCRIPT_DIR}" --emit-plan-json > "${PLAN_JSON}"
}

plan_timer_name() {
    "${EASYDEPLOY_BACKUP_PYTHON}" -c 'import json,sys; print(json.load(open(sys.argv[1]))["timer_name"])' "${PLAN_JSON}"
}

write_borgmatic_config() {
    mkdir -p "${BACKUP_STATE_DIR}" "${BACKUP_STAGING_CURRENT}"
    easydeploy_backup_write_borgmatic_config \
        "${BORG_CONFIG_PATH}" \
        "${BACKUP_REPO_URL}" \
        "${BACKUP_STAGING_CURRENT}" \
        "MED_Backup"
}

run_stack_hooks() {
    local phase="$1"
    local hook
    hook="$("${EASYDEPLOY_BACKUP_PYTHON}" -c \
        'import json,sys
print(json.load(open(sys.argv[1])).get("hooks", {}).get(sys.argv[2], ""))' \
        "${PLAN_JSON}" "${phase}")"
    [[ -n "${hook}" ]] || return 0
    easydeploy_backup_run_hook "${SCRIPT_DIR}" "${hook}"
}

list_archives() {
    info "Listing backup archive names from ${BACKUP_REPO_URL}..."
    easydeploy_backup_list_archives "${BACKUP_REPO_URL}" | awk '{print $1}'
}

create_backup() {
    # easydeploy_backup_stage_payload uses a RETURN trap that references this
    # name after its local variable scope ends under `set -u`.
    plan_json=""
    if [[ "${COLD}" == "true" ]]; then
        info "Cold backup: stopping services before staging..."
        run_stack_hooks stop
    fi

    easydeploy_backup_stage_payload \
        "${SCRIPT_DIR}" "${BACKUP_STAGING_CURRENT}" "" "${ENCRYPT_EXPORT}"

    if [[ "${EXPORT_ONLY}" == "true" ]]; then
        [[ -n "${EXPORT_PATH}" ]] || die "--export-only requires --export PATH"
        easydeploy_backup_export_portable "${EXPORT_PATH}" "${BACKUP_STAGING_CURRENT}" "${ENCRYPT_EXPORT}"
        return 0
    fi

    easydeploy_backup_repo_create "${BORG_CONFIG_PATH}"

    info "Creating backup archive..."
    borgmatic --config "${BORG_CONFIG_PATH}" create --verbosity 1 --stats

    info "Applying retention policy..."
    borgmatic --config "${BORG_CONFIG_PATH}" prune

    info "Checking repository consistency..."
    borgmatic --config "${BORG_CONFIG_PATH}" check

    if [[ -n "${EXPORT_PATH}" ]]; then
        easydeploy_backup_export_portable "${EXPORT_PATH}" "${BACKUP_STAGING_CURRENT}" "${ENCRYPT_EXPORT}"
    fi

    success "Backup completed successfully."
    list_archives
}

reconcile_schedule() {
    load_runtime_env
    load_backup_settings
    load_plan_json
    easydeploy_backup_py "${EASYDEPLOY_LIB}/python/backup_schedule.py" \
        --project-root "${SCRIPT_DIR}" \
        --deploy-yaml "${SCRIPT_DIR}/deploy.yaml" \
        --unit-name "$(plan_timer_name)"
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
            --cold)
                COLD="true"
                ;;
            --schedule)
                SCHEDULE_ONLY="true"
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

    if [[ "${SCHEDULE_ONLY}" == "true" ]]; then
        reconcile_schedule
        exit 0
    fi

    require_command docker

    if [[ -n "${EXPORT_FROM_ARCHIVE}" ]]; then
        require_command borg
        load_backup_settings
        easydeploy_backup_repo_env "${SECRETS_FILE_PATH}"
        EXPORT_FROM_ARCHIVE="$(easydeploy_backup_resolve_archive "${BACKUP_REPO_URL}" "${EXPORT_FROM_ARCHIVE}")"
        [[ -n "${EXPORT_PATH}" ]] || die "--export-from-archive requires --export PATH"
        easydeploy_backup_export_from_archive \
            "${BACKUP_REPO_URL}" "${EXPORT_FROM_ARCHIVE}" "${EXPORT_PATH}" "${ENCRYPT_EXPORT}"
        exit 0
    fi

    if [[ "${EXPORT_ONLY}" == "true" ]]; then
        load_backup_settings "false"
        load_plan_json
        trap cleanup_staging EXIT
        if [[ "${COLD}" == "true" ]]; then
            trap 'run_stack_hooks start; cleanup_staging' EXIT
        fi
        create_backup
        exit 0
    fi

    require_command borgmatic
    require_command borg

    load_runtime_env
    load_backup_settings
    load_plan_json
    easydeploy_backup_repo_env "${SECRETS_FILE_PATH}"
    write_borgmatic_config

    if [[ "${LIST_ONLY}" == "true" ]]; then
        list_archives
        exit 0
    fi

    trap cleanup_staging EXIT

    if [[ "${COLD}" == "true" ]]; then
        trap 'run_stack_hooks start; cleanup_staging' EXIT
    fi

    create_backup
}

main "$@"
