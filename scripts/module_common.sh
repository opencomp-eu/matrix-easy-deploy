#!/usr/bin/env bash
# Shared helper functions for module setup scripts.

module_load_env() {
    local deploy_env="$1"
    local rerun_hint="$2"

    if [[ ! -f "$deploy_env" ]]; then
        die "No .env file found at ${deploy_env}. Please run ${rerun_hint} first."
    fi

    info "Loading existing deployment configuration from .env..."
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" == \#* ]] && continue
        value="${value%%#*}"
        value="${value%"${value##*[![:space:]]}"}"
        export "${key}=${value}"
    done < "$deploy_env"

    local required_vars=(MATRIX_DOMAIN SERVER_NAME)
    for var in "${required_vars[@]}"; do
        if [[ -z "${!var:-}" ]]; then
            die "Required variable '${var}' not found in .env. Please re-run ${rerun_hint}."
        fi
    done

    success "Loaded: MATRIX_DOMAIN=${MATRIX_DOMAIN}, SERVER_NAME=${SERVER_NAME}"
}

module_homeserver_internal_url() {
    printf '%s' "${HOMESERVER_INTERNAL_URL:-http://matrix_synapse:8008}"
}

module_homeserver_config_file() {
    local project_root="$1"
    local impl="${SERVER_IMPLEMENTATION:-synapse}"
    if [[ "${impl,,}" == "tuwunel" ]]; then
        printf '%s' "${project_root}/modules/core/tuwunel/tuwunel.toml"
    else
        printf '%s' "${project_root}/modules/core/synapse/homeserver.yaml"
    fi
}

module_homeserver_appservice_data_dir() {
    local project_root="$1"
    local impl="${SERVER_IMPLEMENTATION:-synapse}"
    if [[ "${impl,,}" == "tuwunel" ]]; then
        printf '%s' "${project_root}/modules/core/tuwunel_data/appservices"
    else
        printf '%s' "${project_root}/modules/core/synapse_data"
    fi
}

module_verify_server_name() {
    local homeserver_config="$1"
    local usage_context="$2"

    if [[ ! -f "$homeserver_config" ]]; then
        warn "homeserver config not found - skipping server_name cross-check."
        return
    fi

    local actual_server_name
    if [[ "$homeserver_config" == *.toml ]]; then
        actual_server_name="$(grep -E '^server_name\s*=' "$homeserver_config" | head -1 | sed -E 's/^[^=]*=\s*"?([^"]+)"?.*/\1/')"
    else
        actual_server_name="$(grep -E '^server_name:' "$homeserver_config" | head -1 | awk '{print $2}' | tr -d '"')"
    fi

    if [[ -z "$actual_server_name" ]]; then
        warn "Could not read server_name from homeserver.yaml - skipping check."
        return
    fi

    if [[ "$actual_server_name" == "$SERVER_NAME" ]]; then
        success "server_name check passed: ${SERVER_NAME}"
        return
    fi

    echo
    warn "SERVER_NAME mismatch detected!"
    echo -e "  ${BOLD}.env has:${RESET}             ${RED}${SERVER_NAME}${RESET}"
    echo -e "  ${BOLD}homeserver.yaml has:${RESET}  ${GREEN}${actual_server_name}${RESET}"
    echo -e "  Using homeserver.yaml value for this module setup."
    echo

    SERVER_NAME="$actual_server_name"
    export SERVER_NAME
    info "Using server_name=${SERVER_NAME} for ${usage_context}."
}

ensure_postgres_role_and_database() {
    local postgres_password="$1"
    local db_user="$2"
    local db_password="$3"
    local db_name="$4"

    if [[ -z "$postgres_password" ]]; then
        die "POSTGRES_PASSWORD is required"
    fi

    if ! docker ps --format '{{.Names}}' | grep -q '^matrix_postgres$'; then
        die "matrix_postgres is not running. Please start the core stack first."
    fi

    docker exec -e PGPASSWORD="${postgres_password}" matrix_postgres \
        psql -U synapse -c \
        "DO \$\$ BEGIN
           IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${db_user}') THEN
             CREATE ROLE ${db_user} LOGIN PASSWORD '${db_password}';
           ELSE
             ALTER ROLE ${db_user} WITH PASSWORD '${db_password}';
           END IF;
         END \$\$;" \
        2>&1 | sed 's/^/    /'

    local db_exists
    db_exists="$(docker exec -e PGPASSWORD="${postgres_password}" matrix_postgres \
        psql -U synapse -tAc "SELECT 1 FROM pg_database WHERE datname = '${db_name}'" | tr -d '[:space:]')"

    if [[ "${db_exists}" != "1" ]]; then
        info "Creating database '${db_name}'…"
        docker exec -e PGPASSWORD="${postgres_password}" matrix_postgres \
            psql -U synapse -c \
            "CREATE DATABASE ${db_name} OWNER ${db_user}
             ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C'
             TEMPLATE template0;" \
            2>&1 | sed 's/^/    /'
    else
        info "Database '${db_name}' already exists — skipping create."
    fi

    docker exec -e PGPASSWORD="${postgres_password}" matrix_postgres \
        psql -U synapse -c \
        "GRANT ALL PRIVILEGES ON DATABASE ${db_name} TO ${db_user};" \
        2>&1 | sed 's/^/    /'
}

module_generate_registration_if_needed() {
    local bridge_data_dir="$1"
    local bridge_image="$2"
    local config_name="${3:-config.yaml}"
    local registration_name="${4:-registration.yaml}"

    local config_file="${bridge_data_dir}/${config_name}"
    local reg_file="${bridge_data_dir}/${registration_name}"
    local project_root="${5:-}"

    if [[ -z "$project_root" ]]; then
        project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    fi

    if [[ -f "$reg_file" && -f "$config_file" ]] \
        && ! python3 "${project_root}/scripts/bridge_appservice_tokens.py" \
            --config-path "$config_file" \
            --registration-path "$reg_file" \
            --needs-regeneration; then
        info "${registration_name} tokens match ${config_name} - skipping regeneration."
        return
    fi

    [[ -f "$reg_file" ]] && rm -f "$reg_file"

    info "Running container to generate ${registration_name}..."
    docker run --rm \
        -v "${bridge_data_dir}:/data:z" \
        "$bridge_image" \
        2>&1 | sed 's/^/    /' || true

    if [[ ! -f "$reg_file" ]]; then
        die "${registration_name} was not generated. Check ${config_name} for errors."
    fi

    if ! python3 "${project_root}/scripts/bridge_appservice_tokens.py" \
        --config-path "$config_file" \
        --registration-path "$reg_file" \
        --verify; then
        die "Bridge appservice tokens are inconsistent after generating ${registration_name}."
    fi
    success "${registration_name} generated."
}

module_sync_appservice_registration() {
    local project_root="$1"
    local registration_src="$2"
    local registration_dest="$3"
    local homeserver_config="$4"
    local registration_path="$5"
    local module_label="$6"

    local changed=0
    local impl="${SERVER_IMPLEMENTATION:-synapse}"
    local registration_filename
    registration_filename="$(basename "$registration_dest")"

    if [[ ! -f "$registration_dest" ]] || ! cmp -s "$registration_src" "$registration_dest"; then
        info "Syncing registration to homeserver data directory..."
        mkdir -p "$(dirname "$registration_dest")"
        cp "$registration_src" "$registration_dest"
        chmod 644 "$registration_dest"
        success "Copied to ${registration_dest}."
        changed=1
    else
        info "registration is unchanged in homeserver data directory - skipping copy."
    fi

    if [[ ! -f "$homeserver_config" ]]; then
        die "homeserver config not found at ${homeserver_config}."
    fi

    if [[ "${impl,,}" == "tuwunel" ]]; then
        local appservice_dir
        appservice_dir="$(module_homeserver_appservice_data_dir "$project_root")"
        mkdir -p "$appservice_dir"
        if python3 "${project_root}/scripts/tuwunel_appservice.py" \
            --appservice-dir "$appservice_dir" \
            --registration-src "$registration_src" \
            --registration-filename "$registration_filename" | grep -q "Synced"; then
            changed=1
        fi
    elif grep -qF "$registration_path" "$homeserver_config"; then
        info "${module_label} already registered in homeserver config - skipping."
    else
        info "Registering ${module_label} appservice in homeserver config..."
        python3 "${project_root}/scripts/synapse_appservice.py" \
            --homeserver-yaml "$homeserver_config" \
            --registration-path "$registration_path"
        success "homeserver config updated."
        changed=1
    fi

    echo "$changed"
}

module_restart_homeserver_if_changed() {
    local changed="$1"
    local project_root="$2"

    local container="${HOMESERVER_CONTAINER:-matrix_synapse}"
    local label="${SERVER_IMPLEMENTATION:-synapse}"

    if [[ "$changed" != "1" ]]; then
        info "${label} appservice wiring unchanged - skipping homeserver restart."
        return
    fi

    info "Restarting ${label} to load the updated appservice registration..."
    if docker ps --format '{{.Names}}' | grep -qx "${container}"; then
        docker restart "${container}"
        success "${label} restarted."
    else
        warn "${label} (${container}) is not running."
        warn "Start the core stack first: cd ${project_root}/modules/core && docker compose up -d"
    fi
}

# Backwards-compatible alias
module_restart_synapse_if_changed() {
    module_restart_homeserver_if_changed "$@"
}

module_start_bridge_after_homeserver() {
    local appservice_changed="$1"
    local project_root="$2"
    local module_dir="$3"
    local bridge_label="$4"

    shift 4
    local -a compose_up_args=("$@")

    module_restart_homeserver_if_changed "$appservice_changed" "$project_root"

    local -a docker_compose
    read -ra docker_compose <<< "$(docker_compose_cmd)"

    echo
    info "Starting ${bridge_label}…"
    (cd "$module_dir" && "${docker_compose[@]}" up -d "${compose_up_args[@]}")
    success "${bridge_label} started."
}
