#!/usr/bin/env bash
# scripts/restore_payload.sh — shared restore payload helpers
# Source this file; do not execute it directly.

: "${SCRIPT_DIR:?SCRIPT_DIR must be set before sourcing restore_payload.sh}"

# shellcheck source=scripts/module_common.sh
source "${SCRIPT_DIR}/scripts/module_common.sh"

BACKUP_PAYLOAD_SCRIPT="${SCRIPT_DIR}/scripts/backup_payload.py"

RESTORE_PERSISTENT_PATHS=(
    "deploy.yaml"
    ".matrix-easy-deploy/secrets.yaml"
    ".matrix-easy-deploy/modules.yaml"
    "modules/core/synapse_data"
    "modules/core/tuwunel_data"
    "modules/hookshot/hookshot"
    "modules/whatsapp-bridge/whatsapp"
    "modules/slack-bridge/slack"
    "modules/draupnir/draupnir"
)

CADDY_VOLUME_EXPORTS=(
    "caddy_data"
    "caddy_caddy_config"
)

restore_payload_read_state_secret() {
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

restore_payload_postgres_password() {
    if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
        printf '%s\n' "${POSTGRES_PASSWORD}"
        return 0
    fi

    restore_payload_read_state_secret "POSTGRES_PASSWORD"
}

restore_payload_path_if_present() {
    local payload_root="$1"
    local rel_path="$2"
    local src="${payload_root}/${rel_path}"
    local dest="${SCRIPT_DIR}/${rel_path}"

    [[ -e "$src" ]] || return 0

    rm -rf "$dest"
    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
}

restore_payload_import_volume_if_present() {
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

restore_payload_start_postgres_prerequisite() {
    info "Starting PostgreSQL prerequisite for restore..."
    ensure_postgres_prerequisite "${SCRIPT_DIR}"
}

restore_payload_reset_synapse_database() {
    local postgres_password_value="$1"

    docker exec -e PGPASSWORD="${postgres_password_value}" matrix_postgres \
        psql -U synapse -d postgres -c "DROP DATABASE IF EXISTS synapse WITH (FORCE);"
    docker exec -e PGPASSWORD="${postgres_password_value}" matrix_postgres \
        psql -U synapse -d postgres -c "CREATE DATABASE synapse OWNER synapse ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C' TEMPLATE template0;"
}

restore_payload_restore_database_dump() {
    local payload_root="$1"
    local dump_name="$2"
    local dump_rel_path="$3"
    local db_user="$4"
    local postgres_password_value="$5"
    local archive_path="${payload_root}/${dump_rel_path}"

    [[ -f "${archive_path}" ]] || return 0

    if [[ "${dump_name}" == "synapse" || "${db_user}" == "synapse" ]]; then
        info "Resetting Synapse PostgreSQL database..."
        restore_payload_reset_synapse_database "${postgres_password_value}"
        info "Restoring Synapse PostgreSQL database..."
        docker exec -i -e PGPASSWORD="${postgres_password_value}" matrix_postgres \
            pg_restore -U synapse -d synapse --no-owner --no-privileges < "${archive_path}"
        return 0
    fi

    if [[ "${db_user}" == "mautrix_whatsapp" || "${db_user}" == "mautrix_slack" ]]; then
        local db_password_key=""
        case "${db_user}" in
            mautrix_whatsapp) db_password_key="WA_DB_PASSWORD" ;;
            mautrix_slack) db_password_key="SL_DB_PASSWORD" ;;
        esac

        local db_password
        db_password="$(restore_payload_read_state_secret "${db_password_key}")"
        [[ -n "${db_password}" ]] || die "${db_password_key} not available in .matrix-easy-deploy/secrets.yaml"

        info "Ensuring bridge database '${dump_name}' exists..."
        ensure_postgres_role_and_database \
            "${postgres_password_value}" \
            "${db_user}" \
            "${db_password}" \
            "${dump_name}"

        info "Restoring bridge database '${dump_name}'..."
        docker exec -e PGPASSWORD="${postgres_password_value}" matrix_postgres \
            psql -U synapse -d postgres -c "DROP DATABASE IF EXISTS ${dump_name} WITH (FORCE);"
        docker exec -e PGPASSWORD="${postgres_password_value}" matrix_postgres \
            psql -U synapse -d postgres -c "CREATE DATABASE ${dump_name} OWNER ${db_user} ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C' TEMPLATE template0;"
        docker exec -i -e PGPASSWORD="${postgres_password_value}" matrix_postgres \
            pg_restore -U "${db_user}" -d "${dump_name}" --no-owner --no-privileges < "${archive_path}"
        return 0
    fi

    warn "Skipping unknown database dump '${dump_name}' (user '${db_user}')."
}

restore_payload_restore_databases_if_present() {
    local payload_root="$1"
    local manifest_path="${payload_root}/manifest.json"
    local postgres_password_value
    local dumps_json

    postgres_password_value="$(restore_payload_postgres_password)"
    [[ -n "${postgres_password_value}" ]] || die "POSTGRES_PASSWORD not available in .env or .matrix-easy-deploy/secrets.yaml"

    if ! docker ps --format '{{.Names}}' | grep -q '^matrix_postgres$'; then
        die "matrix_postgres is not running. Start the core stack prerequisites before restoring."
    fi

    if [[ -f "${manifest_path}" ]]; then
        dumps_json="$(python3 "${BACKUP_PAYLOAD_SCRIPT}" --read-manifest "${manifest_path}" --emit-restore-dumps-json)"
    elif [[ -f "${payload_root}/database/synapse.dump" ]]; then
        dumps_json='[{"name":"synapse","path":"database/synapse.dump","db_user":"synapse"}]'
    else
        return 0
    fi

    python3 - <<'PY' "$dumps_json" | while IFS=$'\t' read -r dump_name dump_path db_user; do
import json
import sys

dumps = json.loads(sys.argv[1])
for item in dumps:
    db_user = item.get("db_user", item["name"])
    print(f"{item['name']}\t{item['path']}\t{db_user}")
PY
        restore_payload_restore_database_dump \
            "${payload_root}" \
            "${dump_name}" \
            "${dump_path}" \
            "${db_user}" \
            "${postgres_password_value}"
    done
}

restore_payload_check_version_warning() {
    local payload_root="$1"
    local manifest_path="${payload_root}/manifest.json"

    [[ -f "${manifest_path}" ]] || return 0

    python3 - <<'PY' "$manifest_path" "${SCRIPT_DIR}/VERSION"
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
version_file = Path(sys.argv[2])

manifest = json.loads(manifest_path.read_text())
backup_version = str(manifest.get("version", "unknown")).strip()
current_version = version_file.read_text().strip() if version_file.exists() else "unknown"

def major_minor(version: str) -> tuple[int, int] | None:
    parts = version.split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None

backup_mm = major_minor(backup_version)
current_mm = major_minor(current_version)
if backup_mm and current_mm and backup_mm != current_mm:
    print(f"WARNING: backup version {backup_version} differs from toolkit version {current_version}")
PY
}

restore_payload_from_directory() {
    local payload_root="$1"

    [[ -d "$payload_root" ]] || die "Payload directory not found: ${payload_root}"

    restore_payload_check_version_warning "${payload_root}" | while IFS= read -r line; do
        [[ -n "$line" ]] && warn "${line#WARNING: }"
    done

    restore_payload_path_if_present "$payload_root" "deploy.yaml"
    restore_payload_path_if_present "$payload_root" ".matrix-easy-deploy/secrets.yaml"
    restore_payload_path_if_present "$payload_root" ".matrix-easy-deploy/modules.yaml"

    info "Regenerating runtime artifacts from restored deploy/state..."
    bash "${SCRIPT_DIR}/apply.sh"

    for volume_name in "${CADDY_VOLUME_EXPORTS[@]}"; do
        restore_payload_import_volume_if_present "$payload_root" "$volume_name"
    done

    for rel_path in "${RESTORE_PERSISTENT_PATHS[@]}"; do
        case "$rel_path" in
            deploy.yaml|.matrix-easy-deploy/secrets.yaml|.matrix-easy-deploy/modules.yaml)
                continue
                ;;
        esac
        restore_payload_path_if_present "$payload_root" "$rel_path"
    done

    restore_payload_start_postgres_prerequisite
    restore_payload_restore_databases_if_present "$payload_root"

    info "Final reconciliation after payload restore..."
    bash "${SCRIPT_DIR}/apply.sh"
}
