#!/usr/bin/env bash
# scripts/backup_payload.sh — shared backup payload staging helpers
# Source this file; do not execute it directly.

: "${SCRIPT_DIR:?SCRIPT_DIR must be set before sourcing backup_payload.sh}"

BACKUP_PAYLOAD_SCRIPT="${SCRIPT_DIR}/scripts/backup_payload.py"

CADDY_VOLUME_EXPORTS=(
    "caddy_data"
    "caddy_caddy_config"
)

backup_payload_init_dirs() {
    local staging_current="${1:?staging current dir required}"
    local payload_dir="${staging_current}/payload"

    rm -rf "${staging_current}"
    mkdir -p "${payload_dir}" "${payload_dir}/docker-volumes" "${payload_dir}/database"
}

backup_payload_stage_copy_path() {
    local payload_dir="$1"
    local rel_path="$2"
    local src="${SCRIPT_DIR}/${rel_path}"
    local dest="${payload_dir}/${rel_path}"

    [[ -e "$src" ]] || return 0

    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
}

backup_payload_export_volume_if_present() {
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

backup_payload_read_state_secret() {
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

backup_payload_postgres_password() {
    if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
        printf '%s\n' "${POSTGRES_PASSWORD}"
        return 0
    fi

    backup_payload_read_state_secret "POSTGRES_PASSWORD"
}

backup_payload_dump_database() {
    local postgres_password="$1"
    local db_user="$2"
    local db_name="$3"
    local dump_file="$4"
    local exclude_table="${5:-}"

    local pg_dump_args=(-U "$db_user" -d "$db_name" -Fc)
    if [[ -n "$exclude_table" ]]; then
        pg_dump_args+=(--exclude-table-data="$exclude_table")
    fi

    docker exec -e PGPASSWORD="${postgres_password}" matrix_postgres \
        pg_dump "${pg_dump_args[@]}" > "${dump_file}"
}

backup_payload_dump_databases() {
    local payload_dir="$1"
    local postgres_password
    local plan_json
    local dump_count=0

    postgres_password="$(backup_payload_postgres_password)"
    [[ -n "${postgres_password}" ]] || die "POSTGRES_PASSWORD not available in .env or .matrix-easy-deploy/secrets.yaml"

    if ! docker ps --format '{{.Names}}' | grep -q '^matrix_postgres$'; then
        die "matrix_postgres is not running. Start the core stack before creating a live backup."
    fi

    plan_json="$(python3 "${BACKUP_PAYLOAD_SCRIPT}" --deploy-yaml "${SCRIPT_DIR}/deploy.yaml" --emit-plan-json)"
    dump_count="$(python3 - <<'PY' "$plan_json"
import json
import sys

plan = json.loads(sys.argv[1])
print(len(plan.get("database_dumps", [])))
PY
)"

    if [[ "${dump_count}" -eq 0 ]]; then
        return 0
    fi

    info "Creating logical PostgreSQL dumps..."
    python3 - <<'PY' "$plan_json" "$payload_dir/database" "$postgres_password"
import json
import subprocess
import sys

plan = json.loads(sys.argv[1])
database_dir = sys.argv[2]
postgres_password = sys.argv[3]

for item in plan.get("database_dumps", []):
    db_user = item["db_user"]
    db_name = item["db_name"]
    dump_file = f"{database_dir}/{db_name}.dump"
    cmd = [
        "docker", "exec", "-e", f"PGPASSWORD={postgres_password}",
        "matrix_postgres", "pg_dump", "-U", db_user, "-d", db_name, "-Fc",
    ]
    for table in item.get("exclude_table_data", []):
        cmd.append(f"--exclude-table-data={table}")
    with open(dump_file, "wb") as handle:
        subprocess.run(cmd, check=True, stdout=handle)
    print(f"dumped:{db_name}")
PY
}

backup_payload_write_manifest() {
    local payload_dir="$1"
    local repository_path="${2:-}"
    local encrypted="${3:-false}"

    python3 "${BACKUP_PAYLOAD_SCRIPT}" \
        --write-manifest \
        --manifest-path "${payload_dir}/manifest.json" \
        --project-root "${SCRIPT_DIR}" \
        --repository-path "${repository_path}" \
        $([[ "${encrypted}" == "true" ]] && echo --encrypted)
}

backup_payload_stage() {
    local staging_current="$1"
    local repository_path="${2:-}"
    local encrypted="${3:-false}"
    local payload_dir="${staging_current}/payload"
    local plan_json
    local rel_path

    backup_payload_init_dirs "${staging_current}"

    plan_json="$(python3 "${BACKUP_PAYLOAD_SCRIPT}" --deploy-yaml "${SCRIPT_DIR}/deploy.yaml" --emit-plan-json)"

    python3 - <<'PY' "$plan_json" | while IFS= read -r rel_path; do
import json
import sys

plan = json.loads(sys.argv[1])
for rel_path in plan.get("persistent_paths", []):
    print(rel_path)
PY
        backup_payload_stage_copy_path "${payload_dir}" "${rel_path}"
    done

    backup_payload_dump_databases "${payload_dir}"

    for volume_name in "${CADDY_VOLUME_EXPORTS[@]}"; do
        backup_payload_export_volume_if_present "$volume_name" "${payload_dir}/docker-volumes"
    done

    backup_payload_write_manifest "${payload_dir}" "${repository_path}" "${encrypted}"
}
