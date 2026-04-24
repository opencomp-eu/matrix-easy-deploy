#!/usr/bin/env bash
# Shared helper functions for module setup scripts.

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
