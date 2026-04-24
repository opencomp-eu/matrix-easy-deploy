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

module_verify_server_name() {
    local homeserver_yaml="$1"
    local usage_context="$2"

    if [[ ! -f "$homeserver_yaml" ]]; then
        warn "homeserver.yaml not found - skipping server_name cross-check."
        return
    fi

    local actual_server_name
    actual_server_name="$(grep -E '^server_name:' "$homeserver_yaml" | head -1 | awk '{print $2}' | tr -d '"')"

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
