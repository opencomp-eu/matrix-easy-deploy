#!/usr/bin/env bash
# =============================================================================
#  matrix-easy-deploy  —  modules/hookshot/setup.sh
#  Sets up the Hookshot bridge as an appservice on the existing Synapse server.
#
#  Run via:  bash setup.sh --module hookshot
#
#  What this does:
#    1. Reads the existing .env to discover your homeserver details.
#    2. Asks for a webhook ingress domain (e.g. hookshot.example.com).
#    3. Generates the appservice tokens and RSA passkey.
#    4. Renders config.yml and registration.yml from templates.
#    5. Registers the appservice with Synapse (via homeserver.yaml).
#    6. Adds a Caddy reverse-proxy block for the webhook domain.
#    7. Starts Hookshot and restarts Synapse so the registration takes effect.
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../../scripts/lib.sh
source "${PROJECT_ROOT}/scripts/lib.sh"

IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"

# Temp file used in generate_config(); declared here so the EXIT trap can
# always reference it — local variables go out of scope before EXIT fires.
VARS_FILE=""
cleanup() { [[ -n "$VARS_FILE" ]] && rm -f "$VARS_FILE"; }
trap cleanup EXIT

DEPLOY_ENV="${PROJECT_ROOT}/.env"
MODULE_DIR="${SCRIPT_DIR}"
HOOKSHOT_DATA_DIR="${MODULE_DIR}/hookshot"
CORE_SYNAPSE_DATA_DIR="${PROJECT_ROOT}/modules/core/synapse_data"
HOMESERVER_YAML="${PROJECT_ROOT}/modules/core/synapse/homeserver.yaml"
CADDYFILE="${PROJECT_ROOT}/caddy/Caddyfile"

# =============================================================================
# Step 1 — Load existing deployment environment
# =============================================================================
load_env() {
    if [[ ! -f "$DEPLOY_ENV" ]]; then
        die "No .env file found at ${DEPLOY_ENV}. Please run setup.sh first."
    fi

    info "Loading existing deployment configuration from .env…"
    # Export each non-comment, non-empty line
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" == \#* ]] && continue
        export "${key}=${value}"
    done < "$DEPLOY_ENV"

    # Validate expected vars are present
    local required_vars=(MATRIX_DOMAIN SERVER_NAME)
    for var in "${required_vars[@]}"; do
        if [[ -z "${!var:-}" ]]; then
            die "Required variable '${var}' not found in .env. Please re-run setup.sh."
        fi
    done

    # Shared Redis defaults (provided by root setup.sh; fallback for older .env files)
    SHARED_REDIS_HOST="${SHARED_REDIS_HOST:-matrix_redis}"
    SHARED_REDIS_PORT="${SHARED_REDIS_PORT:-6379}"
    SHARED_REDIS_URL="${SHARED_REDIS_URL:-redis://${SHARED_REDIS_HOST}:${SHARED_REDIS_PORT}}"
    HOOKSHOT_REDIS_DB="${HOOKSHOT_REDIS_DB:-1}"
    HOOKSHOT_REDIS_URI="${HOOKSHOT_REDIS_URI:-${SHARED_REDIS_URL}/${HOOKSHOT_REDIS_DB}}"
    export SHARED_REDIS_HOST SHARED_REDIS_PORT SHARED_REDIS_URL HOOKSHOT_REDIS_DB HOOKSHOT_REDIS_URI

    success "Loaded: MATRIX_DOMAIN=${MATRIX_DOMAIN}, SERVER_NAME=${SERVER_NAME}"
}

# =============================================================================
# Step 1b — Verify SERVER_NAME matches Synapse's actual server_name
# =============================================================================
verify_server_name() {
    if [[ ! -f "$HOMESERVER_YAML" ]]; then
        warn "homeserver.yaml not found — skipping server_name cross-check."
        return
    fi

    # Read the server_name Synapse is actually using
    local actual_server_name
    actual_server_name="$(grep -E '^server_name:' "$HOMESERVER_YAML" \
        | head -1 | awk '{print $2}' | tr -d '"' )"

    if [[ -z "$actual_server_name" ]]; then
        warn "Could not read server_name from homeserver.yaml — skipping check."
        return
    fi

    if [[ "$actual_server_name" == "$SERVER_NAME" ]]; then
        success "server_name check passed: ${SERVER_NAME}"
        return
    fi

    # Mismatch — this is the root cause of the 'Can't join remote room' error.
    echo
    warn   "SERVER_NAME mismatch detected!"
    echo   -e "  ${BOLD}.env has:${RESET}             ${RED}${SERVER_NAME}${RESET}"
    echo   -e "  ${BOLD}homeserver.yaml has:${RESET}  ${GREEN}${actual_server_name}${RESET}"
    echo
    echo   -e "  Hookshot's bridge.domain MUST match Synapse's server_name."
    echo   -e "  Using the homeserver.yaml value for this module setup."
    echo
    echo   -e "  ${YELLOW}If you also want to fix .env, update SERVER_NAME=${actual_server_name}${RESET}"
    echo   -e "  ${YELLOW}and re-run: bash setup.sh --module hookshot${RESET}"
    echo

    # Override for the duration of THIS run only
    SERVER_NAME="$actual_server_name"
    export SERVER_NAME
    info "Using server_name=${SERVER_NAME} for hookshot config."
}
# =============================================================================
gather_config() {
    echo
    echo -e "${BOLD}  Hookshot Module Configuration${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  Hookshot bridges your Matrix rooms to GitHub, GitLab, generic webhooks, RSS feeds, and more."
    echo -e "  Press Enter to accept a ${CYAN}[default]${RESET}.\n"

    local _suggested_hookshot_domain
    _suggested_hookshot_domain="hookshot.$(extract_base_domain "$MATRIX_DOMAIN")"
    ask HOOKSHOT_DOMAIN \
        "Hookshot webhook domain  (e.g. hookshot.example.com)" \
        "$_suggested_hookshot_domain"
    while [[ -z "$HOOKSHOT_DOMAIN" ]]; do
        warn "Hookshot domain is required."
        ask HOOKSHOT_DOMAIN "Hookshot webhook domain" "$_suggested_hookshot_domain"
    done

    echo
    echo -e "${BOLD}  Configuration summary${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  Homeserver      : ${CYAN}${MATRIX_DOMAIN}${RESET}"
    echo -e "  Server name     : ${CYAN}${SERVER_NAME}${RESET}"
    echo -e "  Hookshot domain : ${CYAN}${HOOKSHOT_DOMAIN}${RESET}"
    echo
    echo -e "  ${YELLOW}DNS check:${RESET} make sure this A record points to this server:"
    echo -e "    ${CYAN}${HOOKSHOT_DOMAIN}${RESET}  →  <this server's IP>"
    echo

    ask_yn _confirm "Does this look right? Proceed?" "y"
    if [[ "$_confirm" != "y" ]]; then
        warn "Restarting configuration…"
        echo
        gather_config
    fi
}

# =============================================================================
# Step 3 — Generate secrets and render config files
# =============================================================================
generate_config() {
    info "Generating appservice tokens…"
    HOOKSHOT_AS_TOKEN="$(generate_secret)"
    HOOKSHOT_HS_TOKEN="$(generate_secret)"
    success "Tokens generated."

    # Generate RSA passkey for encrypting stored OAuth/API tokens
    local passkey_path="${HOOKSHOT_DATA_DIR}/passkey.pem"
    if [[ -f "$passkey_path" ]]; then
        info "Passkey already exists at ${passkey_path} — skipping generation."
    else
        info "Generating RSA passkey…"
        openssl genpkey \
            -out "$passkey_path" \
            -outform PEM \
            -algorithm RSA \
            -pkeyopt rsa_keygen_bits:4096 \
            2>/dev/null
        chmod 600 "$passkey_path"
        success "Passkey written to ${passkey_path}."
    fi

    # Append Hookshot vars to the project .env
    if ! grep -q "^HOOKSHOT_DOMAIN=" "$DEPLOY_ENV"; then
        info "Appending Hookshot variables to .env…"
        cat >> "$DEPLOY_ENV" <<EOF

# Hookshot module — added by modules/hookshot/setup.sh
HOOKSHOT_DOMAIN=${HOOKSHOT_DOMAIN}
HOOKSHOT_AS_TOKEN=${HOOKSHOT_AS_TOKEN}
HOOKSHOT_HS_TOKEN=${HOOKSHOT_HS_TOKEN}
HOOKSHOT_REDIS_URI=${HOOKSHOT_REDIS_URI}
EOF

    if ! grep -q "^HOOKSHOT_REDIS_URI=" "$DEPLOY_ENV"; then
        info "Adding HOOKSHOT_REDIS_URI to .env…"
        echo "HOOKSHOT_REDIS_URI=${HOOKSHOT_REDIS_URI}" >> "$DEPLOY_ENV"
    fi
    if ! grep -q "^SHARED_REDIS_HOST=" "$DEPLOY_ENV"; then
        echo "SHARED_REDIS_HOST=${SHARED_REDIS_HOST}" >> "$DEPLOY_ENV"
    fi
    if ! grep -q "^SHARED_REDIS_PORT=" "$DEPLOY_ENV"; then
        echo "SHARED_REDIS_PORT=${SHARED_REDIS_PORT}" >> "$DEPLOY_ENV"
    fi
    if ! grep -q "^SHARED_REDIS_URL=" "$DEPLOY_ENV"; then
        echo "SHARED_REDIS_URL=${SHARED_REDIS_URL}" >> "$DEPLOY_ENV"
    fi
        success ".env updated."
    else
        info "Hookshot variables already present in .env — skipping."
    fi

    # Build substitution map
    VARS_FILE="$(mktemp)"

    cat > "$VARS_FILE" <<EOF
SERVER_NAME=${SERVER_NAME}
MATRIX_DOMAIN=${MATRIX_DOMAIN}
HOOKSHOT_DOMAIN=${HOOKSHOT_DOMAIN}
HOOKSHOT_AS_TOKEN=${HOOKSHOT_AS_TOKEN}
HOOKSHOT_HS_TOKEN=${HOOKSHOT_HS_TOKEN}
HOOKSHOT_REDIS_URI=${HOOKSHOT_REDIS_URI}
EOF

    # Ensure encryption storage directory exists and is writable by container
    mkdir -p "${HOOKSHOT_DATA_DIR}/cryptostore"

    # Render config.yml
    info "Rendering hookshot/config.yml…"
    render_template \
        "${HOOKSHOT_DATA_DIR}/config.yml.template" \
        "${HOOKSHOT_DATA_DIR}/config.yml" \
        "$VARS_FILE"
    success "hookshot/config.yml written."

    # Render registration.yml
    info "Rendering hookshot/registration.yml…"
    render_template \
        "${HOOKSHOT_DATA_DIR}/registration.yml.template" \
        "${HOOKSHOT_DATA_DIR}/registration.yml" \
        "$VARS_FILE"
    success "hookshot/registration.yml written."
}

# =============================================================================
# Step 4b — Ensure Synapse encryption compatibility flags are enabled
# =============================================================================
ensure_synapse_e2ee_flags() {
    if [[ ! -f "$HOMESERVER_YAML" ]]; then
        warn "homeserver.yaml not found — skipping Synapse encryption flags update."
        return
    fi

    info "Ensuring Synapse MSC3202/MSC2409 compatibility flags are enabled…"
    python3 - "$HOMESERVER_YAML" <<'PYEOF'
import sys

filepath = sys.argv[1]
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.read().splitlines()

flags = {
    'msc3202_device_masquerading': 'true',
    'msc3202_transaction_extensions': 'true',
    'msc2409_to_device_messages_enabled': 'true',
}

exp_idx = next((i for i, line in enumerate(lines) if line.startswith('experimental_features:')), None)

if exp_idx is None:
    lines.extend([
        '',
        'experimental_features:',
        '  msc3202_device_masquerading: true',
        '  msc3202_transaction_extensions: true',
        '  msc2409_to_device_messages_enabled: true',
    ])
else:
    j = exp_idx + 1
    while j < len(lines):
        line = lines[j]
        if line.strip() == '':
            j += 1
            continue
        if not line.startswith('  '):
            break
        j += 1

    block_lines = lines[exp_idx + 1:j]
    existing = {}
    for idx, line in enumerate(block_lines):
        stripped = line.strip()
        if ':' not in stripped:
            continue
        key = stripped.split(':', 1)[0].strip()
        if key in flags:
            existing[key] = exp_idx + 1 + idx

    for key, value in flags.items():
        if key in existing:
            lines[existing[key]] = f'  {key}: {value}'
        else:
            lines.insert(j, f'  {key}: {value}')
            j += 1

with open(filepath, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')

print('  Synapse experimental_features updated for Hookshot E2EE.')
PYEOF
    success "Synapse encryption compatibility flags ensured."
}

# =============================================================================
# Step 4 — Register appservice with Synapse
# =============================================================================
register_appservice() {
    local reg_src="${HOOKSHOT_DATA_DIR}/registration.yml"
    local reg_dest="${CORE_SYNAPSE_DATA_DIR}/hookshot-registration.yml"

    info "Copying registration.yml to Synapse data directory…"
    cp "$reg_src" "$reg_dest"
    success "Registration file copied to ${reg_dest}."

    # The file is mounted into Synapse as /data/hookshot-registration.yml.
    # We need to tell Synapse to load it via app_service_config_files.
    local reg_container_path="/data/hookshot-registration.yml"

    if [[ ! -f "$HOMESERVER_YAML" ]]; then
        die "homeserver.yaml not found at ${HOMESERVER_YAML}. Please run setup.sh first."
    fi

    if grep -qF "$reg_container_path" "$HOMESERVER_YAML"; then
        info "Hookshot already registered in homeserver.yaml — skipping."
    else
        info "Registering Hookshot appservice in homeserver.yaml…"
        python3 - "$HOMESERVER_YAML" "$reg_container_path" <<'PYEOF'
import sys, re

filepath = sys.argv[1]
reg_path = sys.argv[2]

with open(filepath, 'r') as f:
    content = f.read()

if 'app_service_config_files' in content:
    # Append our registration to the existing list
    content = re.sub(
        r'(app_service_config_files:(?:\s*\n\s+-[^\n]*)*)',
        lambda m: m.group(0) + f'\n  - {reg_path}',
        content,
        count=1
    )
else:
    content += f'\n# Application services (bridges)\napp_service_config_files:\n  - {reg_path}\n'

with open(filepath, 'w') as f:
    f.write(content)

print(f"  Added {reg_path} to app_service_config_files.")
PYEOF
        success "homeserver.yaml updated."
    fi
}

# =============================================================================
# Step 5 — Add Caddy reverse-proxy block for the webhook domain
# =============================================================================
update_caddy() {
    if grep -qF "$HOOKSHOT_DOMAIN" "$CADDYFILE"; then
        info "Caddy block for ${HOOKSHOT_DOMAIN} already exists — skipping."
        return
    fi

    info "Appending Hookshot Caddy block to ${CADDYFILE}…"
    cat >> "$CADDYFILE" <<EOF

# Hookshot bridge — webhook ingress for GitHub, GitLab, generic hooks, etc.
${HOOKSHOT_DOMAIN} {
    reverse_proxy matrix-hookshot:9000

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options SAMEORIGIN
        Referrer-Policy strict-origin-when-cross-origin
        -Server
    }

    log
}
EOF
    success "Caddy block added."

    # Reload Caddy to pick up the new site block (this also triggers cert issuance)
    info "Reloading Caddy…"
    if docker ps --format '{{.Names}}' | grep -q '^caddy$'; then
        docker exec caddy caddy reload --config /etc/caddy/Caddyfile 2>&1 | sed 's/^/    /'
        success "Caddy reloaded."
    else
        warn "Caddy container is not running. The new site block will be active on next start."
    fi
}

# =============================================================================
# Step 6 — Start Hookshot and restart Synapse
# =============================================================================
start_services() {
    echo
    info "Starting Hookshot…"
    (cd "$MODULE_DIR" && "${DOCKER_COMPOSE[@]}" up -d --pull always)
    success "Hookshot started."

    echo
    info "Restarting Synapse to load the new appservice registration…"
    if docker ps --format '{{.Names}}' | grep -q '^matrix_synapse$'; then
        docker restart matrix_synapse
        success "Synapse restarted."
    else
        warn "Synapse (matrix_synapse) is not running."
        warn "Start the core stack first: cd modules/core && docker compose up -d"
    fi
}

# =============================================================================
# Step 6 — Smoke-test: create a generic webhook and POST to it
# =============================================================================
test_hookshot() {

    # The provisioning API requires auth we don't have at this stage, so fall
    # back to showing the user the manual curl command to run after inviting the bot.
    echo
    info "Smoke test (manual — requires the bot to be in a room first):"
    echo
    echo -e "  ${CYAN}# 1. Create a BRAND NEW room in Element (important: use a room created\n  #    AFTER hookshot was set up, so its room ID contains your real server_name).${RESET}"
    echo -e "  ${CYAN}#    Then invite the bot:  @hookshot:${SERVER_NAME}${RESET}"
    echo -e "  ${CYAN}!hookshot webhook test${RESET}"
    echo
    echo -e "  ${CYAN}# 2. The bot replies with a unique webhook URL. Test it with:${RESET}"
    echo -e "  ${CYAN}curl -X POST https://${HOOKSHOT_DOMAIN}/webhook/<token> \\${RESET}"
    echo -e "  ${CYAN}     -H 'Content-Type: application/json' \\${RESET}"
    echo -e "  ${CYAN}     -d '{"text": "Hello from Hookshot!"}'${RESET}"
    echo
    echo -e "  ${CYAN}# 3. You should see the message appear in the Matrix room.${RESET}"
    echo
    echo -e "  ${CYAN}# Subscribe to an RSS/Atom feed (in a room with the bot):${RESET}"
    echo -e "  ${CYAN}!hookshot feed https://example.com/feed.rss${RESET}"
    echo
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
  │         Hookshot module installed!                  │
  │                                                     │
  └─────────────────────────────────────────────────────┘
EOF
    echo -e "${RESET}"
    echo -e "  Hookshot is live. Here's a quick reference:\n"
    echo -e "  ${BOLD}Webhook ingress${RESET}     https://${HOOKSHOT_DOMAIN}/"
    echo -e "  ${BOLD}Generic webhook URL${RESET} https://${HOOKSHOT_DOMAIN}/webhook/<token>"
    echo -e "  ${BOLD}Metrics${RESET}             http://matrix-hookshot:9002/metrics (internal)"
    echo
    echo -e "  ${BOLD}Bot username${RESET}        @hookshot:${SERVER_NAME}"
    echo -e "    Invite this bot to a room to start connecting services."
    echo
    echo -e "  ${BOLD}Services enabled by default${RESET}"
    echo -e "    ${CYAN}Generic webhooks${RESET} — create inbound URLs via '!hookshot webhook <name>'"
    echo -e "    ${CYAN}RSS/Atom feeds${RESET}   — subscribe via '!hookshot feed <url>'"
    echo -e "    ${CYAN}Encrypted rooms${RESET}  — supported (E2EE enabled with Redis + cryptostore)"
    echo
    echo -e "  ${BOLD}To enable GitHub / GitLab / Jira${RESET}"
    echo -e "    Uncomment and fill in the relevant section in:"
    echo -e "    ${CYAN}modules/hookshot/hookshot/config.yml${RESET}"
    echo -e "    Then restart: ${CYAN}docker restart matrix-hookshot${RESET}"
    echo
    echo -e "  ${BOLD}Useful commands${RESET}"
    echo -e "    Logs:     ${CYAN}docker logs -f matrix-hookshot${RESET}"
    echo -e "    Restart:  ${CYAN}docker restart matrix-hookshot${RESET}"
    echo -e "    Stop:     ${CYAN}cd modules/hookshot && docker compose down${RESET}"
    echo
    echo -e "  Registration file: ${CYAN}modules/hookshot/hookshot/registration.yml${RESET}"
    echo -e "  Config file:       ${CYAN}modules/hookshot/hookshot/config.yml${RESET}"
    echo -e "  Passkey:           ${CYAN}modules/hookshot/hookshot/passkey.pem${RESET} (keep private)"
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
  │   Hookshot Module Setup                            │
  │   Bridges, webhooks, feeds — all in Matrix.        │
  │                                                    │
  └────────────────────────────────────────────────────┘
EOF
    echo -e "${RESET}"

    echo -e "${BOLD}  Step 1 of 7 — Load existing configuration${RESET}"
    load_env

    echo
    echo -e "${BOLD}  Step 2 of 7 — Verify server_name consistency${RESET}"
    verify_server_name

    echo
    echo -e "${BOLD}  Step 3 of 7 — Hookshot configuration${RESET}"
    gather_config

    echo
    echo -e "${BOLD}  Step 4 of 7 — Generating secrets and config files${RESET}"
    generate_config

    echo
    echo -e "${BOLD}  Step 5 of 7 — Registering appservice with Synapse${RESET}"
    register_appservice
    ensure_synapse_e2ee_flags

    echo
    echo -e "${BOLD}  Step 6 of 7 — Starting services${RESET}"
    update_caddy
    start_services

    echo
    echo -e "${BOLD}  Step 7 of 7 — Smoke test${RESET}"
    test_hookshot

    print_summary
}

main "$@"
