#!/usr/bin/env bash
# =============================================================================
#  scripts/whatsapp_bridge_check.sh
#  Diagnostic script for mautrix-whatsapp <-> Synapse appservice wiring.
#
#  Usage:  bash scripts/whatsapp_bridge_check.sh
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

DEPLOY_ENV="${PROJECT_ROOT}/.env"
BRIDGE_CONFIG="${PROJECT_ROOT}/modules/whatsapp-bridge/whatsapp/config.yaml"
LOCAL_REG="${PROJECT_ROOT}/modules/whatsapp-bridge/whatsapp/registration.yaml"
SYNAPSE_REG="${PROJECT_ROOT}/modules/core/synapse_data/whatsapp-registration.yaml"
HOMESERVER_YAML="${PROJECT_ROOT}/modules/core/synapse/homeserver.yaml"
REG_CONTAINER_PATH="/data/whatsapp-registration.yaml"

PASS=0
FAIL=0

check_pass() { echo -e "  ${GREEN}[PASS]${RESET} $*"; PASS=$((PASS+1)); }
check_fail() { echo -e "  ${RED}[FAIL]${RESET} $*"; FAIL=$((FAIL+1)); }
check_warn() { echo -e "  ${YELLOW}[WARN]${RESET} $*"; }
section()    { echo; echo -e "${BOLD}── $* ${RESET}"; }

section "1. Environment"
if [[ -f "$DEPLOY_ENV" ]]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$DEPLOY_ENV"
    set +o allexport
    check_pass ".env loaded"
else
    check_fail ".env not found"
    exit 1
fi

for var in MATRIX_DOMAIN SERVER_NAME; do
    if [[ -n "${!var:-}" ]]; then
        check_pass "${var}=${!var}"
    else
        check_fail "${var} is missing from .env"
    fi
done

section "2. Required files"
for f in "$BRIDGE_CONFIG" "$LOCAL_REG" "$SYNAPSE_REG" "$HOMESERVER_YAML"; do
    if [[ -f "$f" ]]; then
        check_pass "${f#$PROJECT_ROOT/} exists"
    else
        check_fail "${f#$PROJECT_ROOT/} missing"
    fi
done

section "3. server_name consistency"
if [[ -f "$HOMESERVER_YAML" ]]; then
    actual_sn="$(grep -E '^server_name:' "$HOMESERVER_YAML" | head -1 | awk '{print $2}' | tr -d '"')"
    config_sn="$(python3 -c "import yaml; print((yaml.safe_load(open('${BRIDGE_CONFIG}')) or {}).get('homeserver', {}).get('domain', ''))")"
    if [[ "$actual_sn" == "$SERVER_NAME" ]]; then
        check_pass "homeserver.yaml server_name matches .env SERVER_NAME (${SERVER_NAME})"
    else
        check_fail "homeserver.yaml server_name='${actual_sn}' but .env SERVER_NAME='${SERVER_NAME}'"
    fi
    if [[ "$config_sn" == "$SERVER_NAME" ]]; then
        check_pass "config.yaml homeserver.domain matches SERVER_NAME (${SERVER_NAME})"
    else
        check_fail "config.yaml homeserver.domain='${config_sn}' but SERVER_NAME='${SERVER_NAME}'"
        check_warn "Domain mismatch is a common cause of bridge auth failures — re-run the WhatsApp wizard"
    fi
fi

section "4. appservice registration wiring"
if grep -qF "$REG_CONTAINER_PATH" "$HOMESERVER_YAML"; then
    check_pass "homeserver.yaml references ${REG_CONTAINER_PATH}"
else
    check_fail "homeserver.yaml does not reference ${REG_CONTAINER_PATH}"
fi

if cmp -s "$LOCAL_REG" "$SYNAPSE_REG"; then
    check_pass "registration.yaml matches synapse_data copy"
else
    check_fail "registration.yaml differs from synapse_data/whatsapp-registration.yaml"
fi

section "5. Running containers"
for container in matrix_synapse mautrix-whatsapp; do
    status="$(docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null || echo 'missing')"
    if [[ "$status" == "running" ]]; then
        check_pass "${container} is running"
    else
        check_fail "${container} status: ${status}"
    fi
done

section "6. Files inside Synapse container"
if docker inspect matrix_synapse &>/dev/null; then
    if docker exec matrix_synapse test -f "$REG_CONTAINER_PATH"; then
        check_pass "Synapse container can read ${REG_CONTAINER_PATH}"
        container_token="$(docker exec matrix_synapse python3 -c "import yaml; print(yaml.safe_load(open('${REG_CONTAINER_PATH}'))['as_token'])")"
        host_token="$(python3 -c "import yaml; print(yaml.safe_load(open('${SYNAPSE_REG}'))['as_token'])")"
        if [[ "$container_token" == "$host_token" ]]; then
            check_pass "as_token inside Synapse container matches host synapse_data copy"
        else
            check_fail "as_token inside Synapse container does not match host copy"
            check_warn "Synapse may be using a different data directory than this checkout"
        fi
    else
        check_fail "Synapse container is missing ${REG_CONTAINER_PATH}"
    fi

    if docker exec matrix_synapse grep -qF "$REG_CONTAINER_PATH" /data/homeserver.yaml; then
        check_pass "running Synapse homeserver.yaml references ${REG_CONTAINER_PATH}"
    else
        check_fail "running Synapse homeserver.yaml does not reference ${REG_CONTAINER_PATH}"
        check_warn "The container may be using a different homeserver.yaml mount than ${HOMESERVER_YAML}"
    fi
else
    check_warn "matrix_synapse not available — skipping in-container checks"
fi

section "7. Live as_token check (inside Synapse)"
if [[ -f "$BRIDGE_CONFIG" && -f "$LOCAL_REG" ]]; then
    if python3 "${SCRIPT_DIR}/bridge_appservice_tokens.py" \
        --config-path "$BRIDGE_CONFIG" \
        --registration-path "$LOCAL_REG" \
        --synapse-registration-path "$SYNAPSE_REG" \
        --homeserver-yaml "$HOMESERVER_YAML" \
        --registration-container-path "$REG_CONTAINER_PATH" \
        --server-name "$SERVER_NAME" \
        --bot-username "whatsappbot" \
        --verify-deployment; then
        check_pass "Synapse accepted the bridge as_token"
    else
        check_fail "Synapse rejected the bridge as_token or deployment verification failed"
        check_warn "Inspect Synapse logs: docker logs matrix_synapse 2>&1 | grep -i appservice | tail -30"
    fi
fi

section "Summary"
echo
echo -e "  ${GREEN}Passed: ${PASS}${RESET}   ${RED}Failed: ${FAIL}${RESET}"
echo
if [[ $FAIL -gt 0 ]]; then
    echo -e "  ${YELLOW}Try:${RESET} ${CYAN}bash matrix-wizard.sh --module whatsapp-bridge${RESET}"
    exit 1
fi
echo -e "  ${GREEN}All checks passed.${RESET}"
