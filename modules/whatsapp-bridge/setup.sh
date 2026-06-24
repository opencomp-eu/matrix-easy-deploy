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
DEPLOY_YAML="${PROJECT_ROOT}/deploy.yaml"
STATE_SECRETS="${PROJECT_ROOT}/.matrix-easy-deploy/secrets.yaml"
MODULE_DIR="${SCRIPT_DIR}"
BRIDGE_DATA_DIR="${MODULE_DIR}/whatsapp"
CORE_SYNAPSE_DATA_DIR="${PROJECT_ROOT}/modules/core/synapse_data"
HOMESERVER_CONFIG=""
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

    load_module_defaults

    # Derive a sensible default admin username from .env if available
    ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
}

load_module_defaults() {
    MODULE_WA_ADMIN_USERNAME_DEFAULT=""
    MODULE_WA_DB_NAME_DEFAULT=""

    if [[ -f "$DEPLOY_YAML" ]]; then
        eval "$(python3 "${PROJECT_ROOT}/scripts/config_edit.py" --deploy-yaml "$DEPLOY_YAML" --print-module-defaults whatsapp-bridge 2>/dev/null || true)"
        MODULE_WA_ADMIN_USERNAME_DEFAULT="${module_admin_username:-}"
        MODULE_WA_DB_NAME_DEFAULT="${module_db_name:-}"
    fi
}

# =============================================================================
# Step 2 — Verify SERVER_NAME matches Synapse's actual server_name
# =============================================================================
verify_server_name() {
    HOMESERVER_CONFIG="$(module_homeserver_config_file "$PROJECT_ROOT")"
    module_verify_server_name "$HOMESERVER_CONFIG" "bridge config"
}

# =============================================================================
# Step 3 — Gather configuration from the user
# =============================================================================
gather_config() {
    if [[ "${MED_NON_INTERACTIVE:-0}" == "1" ]]; then
        WA_ADMIN_USERNAME="${MODULE_WA_ADMIN_USERNAME:-${MODULE_WA_ADMIN_USERNAME_DEFAULT:-${ADMIN_USERNAME:-admin}}}"
        WA_DB_NAME="${MODULE_WA_DB_NAME:-${MODULE_WA_DB_NAME_DEFAULT:-mautrix_whatsapp}}"
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
        "${MODULE_WA_ADMIN_USERNAME_DEFAULT:-${ADMIN_USERNAME:-admin}}"
    while [[ -z "$WA_ADMIN_USERNAME" ]]; do
        warn "Admin username is required."
        ask WA_ADMIN_USERNAME "Matrix admin username" "${ADMIN_USERNAME:-admin}"
    done

    # Database name for the bridge
    ask WA_DB_NAME \
        "PostgreSQL database name for the WhatsApp bridge" \
        "${MODULE_WA_DB_NAME_DEFAULT:-mautrix_whatsapp}"

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
# Step 3b - Persist module desired state in deploy.yaml
# =============================================================================
persist_module_config() {
    info "Persisting WhatsApp module configuration to deploy.yaml..."
    python3 "${PROJECT_ROOT}/scripts/config_edit.py" \
        --deploy-yaml "$DEPLOY_YAML" \
        --set-module-config "whatsapp-bridge" \
        --module-enabled "true" \
        --module-admin-username "$WA_ADMIN_USERNAME" \
        --module-db-name "$WA_DB_NAME"
    success "deploy.yaml updated."
}

# =============================================================================
# Step 4 — Create a dedicated PostgreSQL database for the bridge
# =============================================================================
resolve_database_credentials() {
    WA_DB_USER="mautrix_whatsapp"
    WA_DB_PASSWORD="$(python3 "${PROJECT_ROOT}/scripts/state_secrets.py" --secrets-file "$STATE_SECRETS" --get WA_DB_PASSWORD 2>/dev/null || true)"
    WA_DB_PASSWORD="${WA_DB_PASSWORD:-$(generate_secret)}"

    python3 "${PROJECT_ROOT}/scripts/state_secrets.py" \
        --secrets-file "$STATE_SECRETS" \
        --set "WA_DB_PASSWORD=${WA_DB_PASSWORD}"

    WA_DB_URI="postgres://${WA_DB_USER}:${WA_DB_PASSWORD}@matrix_postgres/${WA_DB_NAME}?sslmode=disable"
    export WA_DB_USER WA_DB_PASSWORD WA_DB_URI
}

setup_database() {
    info "Setting up PostgreSQL database '${WA_DB_NAME}' for the WhatsApp bridge…"

    if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
        die "POSTGRES_PASSWORD not found in .env. Please re-run the main wizard."
    fi

    # Reuse persisted credentials from state secrets to keep re-runs idempotent.
    resolve_database_credentials

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
        --hs-address "$(module_homeserver_internal_url)" \
        --as-address "http://${BRIDGE_CONTAINER}:${BRIDGE_PORT}" \
        --db-type "postgres" \
        --db-uri "$WA_DB_URI" \
        --admin-user "$WA_ADMIN_USERNAME" \
        --enable-e2ee

    success "config.yaml patched."

    info "Skipping direct .env edits for WhatsApp module values (managed by apply from deploy.yaml + state)."
}

# =============================================================================
# Step 6 — Generate registration.yaml
# =============================================================================
generate_registration() {
    module_generate_registration_if_needed \
        "$BRIDGE_DATA_DIR" \
        "$BRIDGE_IMAGE" \
        "config.yaml" \
        "registration.yaml" \
        "$PROJECT_ROOT"
}

# =============================================================================
# Step 7 — Register the appservice with Synapse
# =============================================================================
register_appservice() {
    local reg_src="${BRIDGE_DATA_DIR}/registration.yaml"
    local reg_dest reg_container_path
    if [[ "${SERVER_IMPLEMENTATION:-synapse}" == "tuwunel" ]]; then
        reg_dest="$(module_homeserver_appservice_data_dir "$PROJECT_ROOT")/whatsapp-registration.yaml"
        reg_container_path="/data/appservices/whatsapp-registration.yaml"
    else
        reg_dest="${CORE_SYNAPSE_DATA_DIR}/whatsapp-registration.yaml"
        reg_container_path="/data/whatsapp-registration.yaml"
    fi
    local bridge_config="${BRIDGE_DATA_DIR}/config.yaml"
    if [[ "$(module_sync_appservice_registration "$PROJECT_ROOT" "$reg_src" "$reg_dest" "$HOMESERVER_CONFIG" "$reg_container_path" "WhatsApp bridge" "$bridge_config")" == "1" ]]; then
        APP_SERVICE_CHANGED="1"
    fi

    module_restart_homeserver_if_changed "$APP_SERVICE_CHANGED" "$PROJECT_ROOT"

    if ! module_verify_mautrix_bridge_deployment \
        "$PROJECT_ROOT" \
        "$bridge_config" \
        "$reg_src" \
        "$reg_dest" \
        "$HOMESERVER_CONFIG" \
        "$reg_container_path" \
        "$SERVER_NAME" \
        "whatsappbot"; then
        warn "WhatsApp bridge appservice verification failed — rotating tokens and retrying once…"
        module_repair_mautrix_bridge_tokens \
            "$BRIDGE_DATA_DIR" \
            "$BRIDGE_IMAGE" \
            "$PROJECT_ROOT"
        if [[ "$(module_sync_appservice_registration "$PROJECT_ROOT" "$reg_src" "$reg_dest" "$HOMESERVER_CONFIG" "$reg_container_path" "WhatsApp bridge" "$bridge_config")" == "1" ]]; then
            APP_SERVICE_CHANGED="1"
        fi
        module_restart_homeserver_if_changed "1" "$PROJECT_ROOT"
        if ! module_verify_mautrix_bridge_deployment \
            "$PROJECT_ROOT" \
            "$bridge_config" \
            "$reg_src" \
            "$reg_dest" \
            "$HOMESERVER_CONFIG" \
            "$reg_container_path" \
            "$SERVER_NAME" \
            "whatsappbot"; then
            die "WhatsApp bridge appservice verification failed after token rotation. Run: bash scripts/whatsapp_bridge_check.sh"
        fi
    fi
}

# =============================================================================
# Step 8 — Start the bridge
# =============================================================================
start_services() {
    module_start_bridge_after_homeserver \
        "0" \
        "$PROJECT_ROOT" \
        "$MODULE_DIR" \
        "mautrix-whatsapp" \
        --pull always
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
    echo -e "${BOLD}  Step 3b of 8 — Persist module configuration${RESET}"
    persist_module_config

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

if [[ "${MED_SOURCE_ONLY:-0}" != "1" ]]; then
    main "$@"
fi
