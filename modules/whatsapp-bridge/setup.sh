#!/usr/bin/env bash
# =============================================================================
#  matrix-easy-deploy  —  modules/whatsapp-bridge/setup.sh
#  Sets up the mautrix-whatsapp bridge as an appservice on Synapse.
#
#  Run via:  bash matrix-wizard.sh --module whatsapp-bridge
#
#  What this does:
#    1. Reads the existing .env to discover homeserver details.
#    2. Asks for bridge admin username and relay-mode preference.
#    3. Creates a dedicated PostgreSQL database for the bridge.
#    4. Pulls the bridge image and lets it generate a starter config.yaml.
#    5. Patches config.yaml with mandatory fields (homeserver, DB, permissions).
#    6. Runs the bridge container once more to generate registration.yaml.
#    7. Registers the appservice with Synapse (via homeserver.yaml).
#    8. Starts the bridge container and restarts Synapse.
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../../scripts/lib.sh
source "${PROJECT_ROOT}/scripts/lib.sh"
# shellcheck source=../../scripts/module_common.sh
source "${PROJECT_ROOT}/scripts/module_common.sh"

IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"

DEPLOY_ENV="${PROJECT_ROOT}/.env"
MODULE_DIR="${SCRIPT_DIR}"
BRIDGE_DATA_DIR="${MODULE_DIR}/whatsapp"
CORE_SYNAPSE_DATA_DIR="${PROJECT_ROOT}/modules/core/synapse_data"
HOMESERVER_YAML="${PROJECT_ROOT}/modules/core/synapse/homeserver.yaml"
CADDYFILE="${PROJECT_ROOT}/caddy/Caddyfile"

BRIDGE_IMAGE="dock.mau.dev/mautrix/whatsapp:latest"
BRIDGE_CONTAINER="mautrix-whatsapp"
BRIDGE_PORT="29318"
APP_SERVICE_CHANGED="0"

# =============================================================================
# Step 1 — Load existing deployment environment
# =============================================================================
load_env() {
    module_load_env "$DEPLOY_ENV" "the main setup wizard"

    # Derive a sensible default admin username from .env if available
    ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
}

# =============================================================================
# Step 2 — Verify SERVER_NAME matches Synapse's actual server_name
# =============================================================================
verify_server_name() {
    module_verify_server_name "$HOMESERVER_YAML" "bridge config"
}

# =============================================================================
# Step 3 — Gather configuration from the user
# =============================================================================
gather_config() {
    if [[ "${MED_NON_INTERACTIVE:-0}" == "1" ]]; then
        WA_ADMIN_USERNAME="${MODULE_WA_ADMIN_USERNAME:-${WA_ADMIN_USERNAME:-${ADMIN_USERNAME:-admin}}}"
        WA_DB_NAME="${MODULE_WA_DB_NAME:-${WA_DB_NAME:-mautrix_whatsapp}}"
        if [[ -z "$WA_ADMIN_USERNAME" || -z "$WA_DB_NAME" ]]; then
            die "WA_ADMIN_USERNAME and WA_DB_NAME are required in non-interactive mode."
        fi
        info "Non-interactive mode: using WA_ADMIN_USERNAME=${WA_ADMIN_USERNAME}, WA_DB_NAME=${WA_DB_NAME}"
        return
    fi

    echo
    echo -e "${BOLD}  WhatsApp Bridge Configuration${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  mautrix-whatsapp bridges your Matrix account to WhatsApp."
    echo -e "  After setup you scan a QR code to link your WhatsApp account."
    echo -e "  Press Enter to accept a ${CYAN}[default]${RESET}.\n"

    # Admin user on the homeserver
    ask WA_ADMIN_USERNAME \
        "Matrix admin username for full bridge access (without @/server part)" \
        "${ADMIN_USERNAME:-admin}"
    while [[ -z "$WA_ADMIN_USERNAME" ]]; do
        warn "Admin username is required."
        ask WA_ADMIN_USERNAME "Matrix admin username" "${ADMIN_USERNAME:-admin}"
    done

    # Database name for the bridge
    ask WA_DB_NAME \
        "PostgreSQL database name for the WhatsApp bridge" \
        "mautrix_whatsapp"

    echo
    echo -e "${BOLD}  Configuration summary${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  Homeserver      : ${CYAN}${MATRIX_DOMAIN}${RESET}"
    echo -e "  Server name     : ${CYAN}${SERVER_NAME}${RESET}"
    echo -e "  Bridge admin    : ${CYAN}@${WA_ADMIN_USERNAME}:${SERVER_NAME}${RESET}"
    echo -e "  Database name   : ${CYAN}${WA_DB_NAME}${RESET}"
    echo

    ask_yn _confirm "Does this look right? Proceed?" "y"
    if [[ "$_confirm" != "y" ]]; then
        warn "Restarting configuration…"
        echo
        gather_config
    fi
}

# =============================================================================
# Step 4 — Create a dedicated PostgreSQL database for the bridge
# =============================================================================
setup_database() {
    info "Setting up PostgreSQL database '${WA_DB_NAME}' for the WhatsApp bridge…"

    if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
        die "POSTGRES_PASSWORD not found in .env. Please re-run the main wizard."
    fi

    # Reuse existing credentials when present to keep re-runs idempotent.
    WA_DB_USER="${WA_DB_USER:-mautrix_whatsapp}"
    WA_DB_PASSWORD="${WA_DB_PASSWORD:-$(generate_secret)}"
    WA_DB_URI="postgres://${WA_DB_USER}:${WA_DB_PASSWORD}@matrix_postgres/${WA_DB_NAME}?sslmode=disable"
    export WA_DB_USER WA_DB_PASSWORD WA_DB_URI

    ensure_postgres_role_and_database \
        "${POSTGRES_PASSWORD}" \
        "${WA_DB_USER}" \
        "${WA_DB_PASSWORD}" \
        "${WA_DB_NAME}"

    success "Database '${WA_DB_NAME}' ready."
}

# =============================================================================
# Step 5 — Pull image, auto-generate config.yaml, patch mandatory fields
# =============================================================================
generate_config() {
    mkdir -p "$BRIDGE_DATA_DIR"

    local config_file="${BRIDGE_DATA_DIR}/config.yaml"

    # --- Pull the image ---
    info "Pulling mautrix-whatsapp image (${BRIDGE_IMAGE})…"
    docker pull "$BRIDGE_IMAGE" 2>&1 | tail -5 | sed 's/^/    /'
    success "Image pulled."

    # --- Let the container generate a starter config.yaml ---
    if [[ -f "$config_file" ]]; then
        info "config.yaml already exists — skipping auto-generation."
        info "Patching mandatory fields in existing config.yaml…"
    else
        info "Running container once to generate config.yaml…"
        docker run --rm \
            -v "${BRIDGE_DATA_DIR}:/data:z" \
            "$BRIDGE_IMAGE" \
            2>&1 | sed 's/^/    /' || true
        # The container exits after writing config.yaml
        if [[ ! -f "$config_file" ]]; then
            die "config.yaml was not generated. Check Docker output above."
        fi
        success "config.yaml generated."
    fi

    # --- Patch mandatory fields using shared helper ---
    info "Patching config.yaml with homeserver, database, and permissions…"

    python3 "${PROJECT_ROOT}/scripts/bridge_config_patch.py" \
        --config-path "$config_file" \
        --server-name "$SERVER_NAME" \
        --hs-address "http://matrix_synapse:8008" \
        --as-address "http://${BRIDGE_CONTAINER}:${BRIDGE_PORT}" \
        --db-type "postgres" \
        --db-uri "$WA_DB_URI" \
        --admin-user "$WA_ADMIN_USERNAME"

    success "config.yaml patched."

    # --- Upsert bridge vars in .env ---
    info "Upserting WhatsApp bridge variables in .env…"
    python3 "${PROJECT_ROOT}/scripts/env_upsert.py" \
        --env-file "$DEPLOY_ENV" \
        --set "WA_DB_NAME=${WA_DB_NAME}" \
        --set "WA_DB_USER=${WA_DB_USER}" \
        --set "WA_DB_PASSWORD=${WA_DB_PASSWORD}" \
        --set "WA_DB_URI=${WA_DB_URI}" \
        --set "WA_ADMIN_USERNAME=${WA_ADMIN_USERNAME}"
    success ".env updated."
}

# =============================================================================
# Step 6 — Generate registration.yaml
# =============================================================================
generate_registration() {
    local config_file="${BRIDGE_DATA_DIR}/config.yaml"
    local reg_file="${BRIDGE_DATA_DIR}/registration.yaml"

    if [[ -f "$reg_file" && -f "$config_file" && "$reg_file" -nt "$config_file" ]]; then
        info "registration.yaml is up to date — skipping regeneration."
        return
    fi

    # Regenerate when missing or when config has changed.
    [[ -f "$reg_file" ]] && rm -f "$reg_file"

    info "Running container to generate registration.yaml…"
    docker run --rm \
        -v "${BRIDGE_DATA_DIR}:/data:z" \
        "$BRIDGE_IMAGE" \
        2>&1 | sed 's/^/    /' || true

    if [[ ! -f "$reg_file" ]]; then
        die "registration.yaml was not generated. Check config.yaml for errors."
    fi
    success "registration.yaml generated."
}

# =============================================================================
# Step 7 — Register the appservice with Synapse
# =============================================================================
register_appservice() {
    local reg_src="${BRIDGE_DATA_DIR}/registration.yaml"
    local reg_dest="${CORE_SYNAPSE_DATA_DIR}/whatsapp-registration.yaml"
    local reg_container_path="/data/whatsapp-registration.yaml"

    if [[ ! -f "$reg_dest" ]] || ! cmp -s "$reg_src" "$reg_dest"; then
        info "Syncing registration.yaml to Synapse data directory…"
        cp "$reg_src" "$reg_dest"
        chmod 644 "$reg_dest"
        success "Copied to ${reg_dest}."
        APP_SERVICE_CHANGED="1"
    else
        info "registration.yaml unchanged in Synapse data directory — skipping copy."
    fi

    if [[ ! -f "$HOMESERVER_YAML" ]]; then
        die "homeserver.yaml not found at ${HOMESERVER_YAML}."
    fi

    if grep -qF "$reg_container_path" "$HOMESERVER_YAML"; then
        info "WhatsApp bridge already registered in homeserver.yaml — skipping."
        return
    fi

    info "Registering WhatsApp appservice in homeserver.yaml…"
    python3 "${PROJECT_ROOT}/scripts/synapse_appservice.py" \
        --homeserver-yaml "$HOMESERVER_YAML" \
        --registration-path "$reg_container_path"
    success "homeserver.yaml updated."
    APP_SERVICE_CHANGED="1"
}

# =============================================================================
# Step 8 — Start the bridge and restart Synapse
# =============================================================================
start_services() {
    echo
    info "Starting mautrix-whatsapp…"
    (cd "$MODULE_DIR" && "${DOCKER_COMPOSE[@]}" up -d --pull always)
    success "mautrix-whatsapp started."

    echo
    if [[ "$APP_SERVICE_CHANGED" == "1" ]]; then
        info "Restarting Synapse to load the updated appservice registration…"
        if docker ps --format '{{.Names}}' | grep -q '^matrix_synapse$'; then
            docker restart matrix_synapse
            success "Synapse restarted."
        else
            warn "Synapse (matrix_synapse) is not running."
            warn "Start the core stack first: cd ${PROJECT_ROOT}/modules/core && docker compose up -d"
        fi
    else
        info "Synapse appservice wiring unchanged — skipping Synapse restart."
    fi
}

# =============================================================================
# Summary
# =============================================================================
print_summary() {
    echo
    echo -e "${GREEN}${BOLD}"
    cat << 'EOF'
  ┌─────────────────────────────────────────────────────┐
  │                                                     │
  │   WhatsApp Bridge installed!                        │
  │                                                     │
  └─────────────────────────────────────────────────────┘
EOF
    echo -e "${RESET}"
    echo -e "  ${BOLD}How to link your WhatsApp account${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  1. In Element (or any Matrix client), open a DM with:"
    echo -e "     ${CYAN}@whatsappbot:${SERVER_NAME}${RESET}"
    echo -e "  2. Send: ${CYAN}login${RESET}"
    echo -e "  3. The bot will display a QR code."
    echo -e "  4. Open WhatsApp on your phone → Linked Devices → Link a Device."
    echo -e "  5. Scan the QR code. Your chats will start appearing as Matrix rooms.\n"
    echo -e "  ${BOLD}Bridge admin${RESET}        @${WA_ADMIN_USERNAME}:${SERVER_NAME}"
    echo -e "  ${BOLD}Bot username${RESET}         @whatsappbot:${SERVER_NAME}\n"
    echo -e "  ${BOLD}Useful commands${RESET}"
    echo -e "    Logs:     ${CYAN}docker logs -f mautrix-whatsapp${RESET}"
    echo -e "    Restart:  ${CYAN}docker restart mautrix-whatsapp${RESET}"
    echo -e "    Stop:     ${CYAN}cd ${MODULE_DIR} && docker compose down${RESET}"
    echo -e "    Re-setup: ${CYAN}bash matrix-wizard.sh --module whatsapp-bridge${RESET}"
    echo
    echo -e "  ${BOLD}Files${RESET}"
    echo -e "    Config:       ${CYAN}modules/whatsapp-bridge/whatsapp/config.yaml${RESET}"
    echo -e "    Registration: ${CYAN}modules/whatsapp-bridge/whatsapp/registration.yaml${RESET}"
    echo
    echo -e "  ${YELLOW}Note:${RESET} A WhatsApp mobile app must stay active and connected."
    echo -e "  If you log out or reinstall WhatsApp, run ${CYAN}login${RESET} in the bridge DM again."
    echo
}

# =============================================================================
# Main
# =============================================================================
main() {
    echo
    echo -e "${BOLD}${CYAN}"
    cat << 'EOF'
  ┌────────────────────────────────────────────────────┐
  │                                                    │
  │   WhatsApp Bridge Setup                            │
  │   Powered by mautrix-whatsapp                      │
  │                                                    │
  └────────────────────────────────────────────────────┘
EOF
    echo -e "${RESET}"

    echo -e "${BOLD}  Step 1 of 8 — Load existing configuration${RESET}"
    load_env

    echo
    echo -e "${BOLD}  Step 2 of 8 — Verify server_name consistency${RESET}"
    verify_server_name

    echo
    echo -e "${BOLD}  Step 3 of 8 — Bridge configuration${RESET}"
    gather_config

    echo
    echo -e "${BOLD}  Step 4 of 8 — PostgreSQL database${RESET}"
    setup_database

    echo
    echo -e "${BOLD}  Step 5 of 8 — Generating bridge config${RESET}"
    generate_config

    echo
    echo -e "${BOLD}  Step 6 of 8 — Generating appservice registration${RESET}"
    generate_registration

    echo
    echo -e "${BOLD}  Step 7 of 8 — Registering appservice with Synapse${RESET}"
    register_appservice

    echo
    echo -e "${BOLD}  Step 8 of 8 — Starting services${RESET}"
    start_services

    print_summary
}

main "$@"
