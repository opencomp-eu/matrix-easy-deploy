#!/usr/bin/env bash
# Shared helpers for Tuwunel admin commands via docker exec.

set -euo pipefail

TUWUNEL_CONTAINER="${TUWUNEL_CONTAINER:-matrix_tuwunel}"
TUWUNEL_BINARY="${TUWUNEL_BINARY:-tuwunel}"

tuwunel_container_running() {
    docker ps --format '{{.Names}}' | grep -qx "${TUWUNEL_CONTAINER}"
}

tuwunel_admin_execute() {
    local command="$1"
    tuwunel_container_running || die "Tuwunel (${TUWUNEL_CONTAINER}) is not running. Start the core stack first."

    docker exec "${TUWUNEL_CONTAINER}" "${TUWUNEL_BINARY}" --execute "${command}"
}

tuwunel_create_user() {
    local username="$1"
    local password="$2"
    local grant_admin="${3:-false}"

    local output
    output="$(tuwunel_admin_execute "users create_user ${username} ${password}" 2>&1)" || {
        if [[ "$output" == *"already exists"* ]]; then
            warn "User '${username}' already exists. Skipping."
            return 0
        fi
        die "Tuwunel user creation failed: ${output}"
    }

    if [[ "$grant_admin" == "true" ]]; then
        tuwunel_admin_execute "users make-user-admin ${username}" >/dev/null 2>&1 || \
            tuwunel_admin_execute "users make_user_admin ${username}" >/dev/null 2>&1 || \
            warn "User created but could not grant admin privileges. Promote manually in the admin room."
    fi

    printf '%s' "$output"
}

tuwunel_reset_password() {
    local username="$1"
    local password="$2"
    tuwunel_admin_execute "users reset-password ${username} ${password}"
}

tuwunel_list_users() {
    tuwunel_admin_execute "users list"
}
