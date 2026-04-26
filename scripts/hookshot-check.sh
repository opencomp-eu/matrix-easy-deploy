#!/usr/bin/env bash
# =============================================================================
#  scripts/hookshot-check.sh
#  Diagnostic script to verify the Hookshot <-> Synapse appservice wiring.
#
#  Usage:  bash scripts/hookshot-check.sh
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/lib.sh"

DEPLOY_ENV="${PROJECT_ROOT}/.env"
SYNAPSE_DATA_REG="${PROJECT_ROOT}/modules/core/synapse_data/hookshot-registration.yml"
LOCAL_REG="${PROJECT_ROOT}/modules/hookshot/hookshot/registration.yml"
HOOKSHOT_CONFIG="${PROJECT_ROOT}/modules/hookshot/hookshot/config.yml"
HOMESERVER_YAML="${PROJECT_ROOT}/modules/core/synapse/homeserver.yaml"

PASS=0
FAIL=0

check_pass() { echo -e "  ${GREEN}[PASS]${RESET} $*"; PASS=$((PASS+1)); }
check_fail() { echo -e "  ${RED}[FAIL]${RESET} $*"; FAIL=$((FAIL+1)); }
check_warn() { echo -e "  ${YELLOW}[WARN]${RESET} $*"; }
section()    { echo; echo -e "${BOLD}── $* ${RESET}"; }

# ---------------------------------------------------------------------------
section "1. Environment"
# ---------------------------------------------------------------------------

if [[ -f "$DEPLOY_ENV" ]]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$DEPLOY_ENV"
    set +o allexport
    check_pass ".env loaded"
else
    check_fail ".env not found — run setup.sh first"
    exit 1
fi

for var in MATRIX_DOMAIN SERVER_NAME HOOKSHOT_DOMAIN HOOKSHOT_AS_TOKEN HOOKSHOT_HS_TOKEN; do
    if [[ -n "${!var:-}" ]]; then
        check_pass "${var} is set (${!var})"
    else
        check_fail "${var} is missing from .env"
    fi
done

SHARED_REDIS_HOST="${SHARED_REDIS_HOST:-matrix_redis}"
SHARED_REDIS_PORT="${SHARED_REDIS_PORT:-6379}"
SHARED_REDIS_URL="${SHARED_REDIS_URL:-redis://${SHARED_REDIS_HOST}:${SHARED_REDIS_PORT}}"
HOOKSHOT_REDIS_DB="${HOOKSHOT_REDIS_DB:-1}"
EXPECTED_HOOKSHOT_REDIS_URI="${HOOKSHOT_REDIS_URI:-${SHARED_REDIS_URL}/${HOOKSHOT_REDIS_DB}}"

# ---------------------------------------------------------------------------
section "2. homeserver.yaml server_name vs .env SERVER_NAME"
# ---------------------------------------------------------------------------

if [[ -f "$HOMESERVER_YAML" ]]; then
    actual_sn="$(grep -E '^server_name:' "$HOMESERVER_YAML" | head -1 | awk '{print $2}' | tr -d '"')"
    if [[ "$actual_sn" == "$SERVER_NAME" ]]; then
        check_pass "server_name matches: ${SERVER_NAME}"
    else
        check_fail "MISMATCH — homeserver.yaml says '${actual_sn}' but .env SERVER_NAME='${SERVER_NAME}'"
        check_warn "Re-run: bash setup.sh --module hookshot  (it will auto-fix this)"
    fi
else
    check_warn "homeserver.yaml not found — skipping check"
fi

# ---------------------------------------------------------------------------
section "3. Registration file contents"
# ---------------------------------------------------------------------------

for f in "$LOCAL_REG" "$SYNAPSE_DATA_REG"; do
    label="${f#$PROJECT_ROOT/}"
    if [[ ! -f "$f" ]]; then
        check_fail "${label} does not exist"
        continue
    fi

    url="$(grep '^url:' "$f" | awk '{print $2}' | tr -d '"')"
    if [[ "$url" == *"_"* ]]; then
        check_fail "${label}: url contains underscore → '${url}'"
        check_warn "Synapse rejects underscore hostnames. Re-run: bash setup.sh --module hookshot"
    else
        check_pass "${label}: url = ${url}"
    fi

    # Check SERVER_NAME stamps in namespace regexes
    if grep -q "{{SERVER_NAME}}" "$f" 2>/dev/null; then
        check_fail "${label}: contains un-rendered template placeholder {{SERVER_NAME}}"
        check_warn "Re-run: bash setup.sh --module hookshot"
    else
        check_pass "${label}: no un-rendered placeholders"
    fi
done

# ---------------------------------------------------------------------------
section "4. homeserver.yaml app_service_config_files"
# ---------------------------------------------------------------------------

if [[ -f "$HOMESERVER_YAML" ]]; then
    if grep -q "hookshot-registration.yml" "$HOMESERVER_YAML"; then
        check_pass "hookshot-registration.yml is referenced in homeserver.yaml"
    else
        check_fail "hookshot-registration.yml is NOT in homeserver.yaml app_service_config_files"
        check_warn "Re-run: bash setup.sh --module hookshot"
    fi

    for f in msc3202_device_masquerading msc3202_transaction_extensions msc2409_to_device_messages_enabled; do
        if grep -Eq "^\s*${f}:\s*true" "$HOMESERVER_YAML"; then
            check_pass "homeserver.yaml: ${f}=true"
        else
            check_fail "homeserver.yaml: ${f} is missing or not true"
            check_warn "Hookshot encrypted room support requires this flag"
        fi
    done
fi

# ---------------------------------------------------------------------------
section "4b. Hookshot encryption/cache config"
# ---------------------------------------------------------------------------

if [[ -f "$HOOKSHOT_CONFIG" ]]; then
    if grep -Eq '^\s*cache:\s*$' "$HOOKSHOT_CONFIG" && grep -Fq "redisUri: ${EXPECTED_HOOKSHOT_REDIS_URI}" "$HOOKSHOT_CONFIG"; then
        check_pass "config.yml: cache.redisUri matches ${EXPECTED_HOOKSHOT_REDIS_URI}"
    else
        check_fail "config.yml: cache.redisUri does not match ${EXPECTED_HOOKSHOT_REDIS_URI}"
    fi

    if grep -Eq '^\s*encryption:\s*$' "$HOOKSHOT_CONFIG" && grep -Eq '^\s*storagePath:\s*/data/cryptostore\s*$' "$HOOKSHOT_CONFIG"; then
        check_pass "config.yml: encryption.storagePath is set to /data/cryptostore"
    else
        check_fail "config.yml: encryption.storagePath is missing"
    fi
else
    check_fail "modules/hookshot/hookshot/config.yml not found"
fi

# ---------------------------------------------------------------------------
section "5. Docker containers running"
# ---------------------------------------------------------------------------

for container in matrix_synapse matrix-hookshot matrix_redis; do
    status="$(docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null || echo 'missing')"
    if [[ "$status" == "running" ]]; then
        check_pass "${container} is running"
        health="$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo 'none')"
        [[ "$health" != "none" && "$health" != "" ]] && echo -e "         health: ${health}"
    else
        check_fail "${container} status: ${status}"
    fi
done

# ---------------------------------------------------------------------------
section "6. Network connectivity — Synapse → Hookshot (appservice port 9993)"
# ---------------------------------------------------------------------------

if docker inspect matrix_synapse &>/dev/null && docker inspect matrix-hookshot &>/dev/null; then
    result="$(docker exec matrix_synapse \
        python3 -c "
import urllib.request, sys
try:
    urllib.request.urlopen('http://matrix-hookshot:9993', timeout=5)
    print('ok')
except urllib.error.HTTPError as e:
    print('http_error_' + str(e.code))   # any HTTP reply means TCP works
except Exception as e:
    print('fail: ' + str(e))
" 2>&1 || echo 'exec_failed')"

    case "$result" in
        ok|http_error_*)
            check_pass "Synapse can reach matrix-hookshot:9993 (got: ${result})" ;;
        *)
            check_fail "Synapse CANNOT reach matrix-hookshot:9993 (${result})"
            check_warn "Check both containers are on caddy_net: docker network inspect caddy_net | grep -A2 Name" ;;
    esac
else
    check_warn "One or both containers not running — skipping network check"
fi

# ---------------------------------------------------------------------------
section "7. Network connectivity — Hookshot → Synapse (client-server API :8008)"
# ---------------------------------------------------------------------------

if docker inspect matrix-hookshot &>/dev/null; then
    result="$(docker exec matrix-hookshot \
        node -e "
const h = require('http');
h.get('http://matrix_synapse:8008/_matrix/client/versions', r => {
    let d = '';
    r.on('data', c => d += c);
    r.on('end', () => process.stdout.write(d.slice(0,80)));
}).on('error', e => { process.stdout.write('fail: ' + e.message); process.exit(1); });
" 2>&1 || echo 'fail')"

    if echo "$result" | grep -q '"versions"'; then
        check_pass "Hookshot can reach Synapse :8008 (got Matrix versions response)"
    else
        check_fail "Hookshot CANNOT reach Synapse :8008 (${result})"
        check_warn "Check hookshot's docker-compose is on caddy_net"
    fi
else
    check_warn "matrix-hookshot not running — skipping"
fi

# ---------------------------------------------------------------------------
section "8. Hookshot webhook port reachable from host"
# ---------------------------------------------------------------------------

if curl -fsSL --max-time 5 "http://localhost:9000/" &>/dev/null 2>&1; then
    check_pass "localhost:9000 is reachable from host"
else
    # 405 / 404 still means hookshot is listening, which is fine
    http_code="$(curl -o /dev/null -s -w "%{http_code}" --max-time 5 "http://localhost:9000/" || echo "000")"
    if [[ "$http_code" == "000" ]]; then
        check_fail "localhost:9000 is NOT reachable (no port binding?)"
        check_warn "Hookshot uses internal Docker networking — this is only needed if exposing the port. Check caddy → hookshot works via HTTPS."
    else
        check_pass "localhost:9000 responded with HTTP ${http_code} (hook listener is up)"
    fi
fi

# ---------------------------------------------------------------------------
section "9. Caddy → Hookshot HTTPS route"
# ---------------------------------------------------------------------------

if [[ -n "${HOOKSHOT_DOMAIN:-}" ]]; then
    http_code="$(curl -o /dev/null -s -w "%{http_code}" --max-time 10 "https://${HOOKSHOT_DOMAIN}/" || echo "000")"
    if [[ "$http_code" == "000" ]]; then
        check_fail "https://${HOOKSHOT_DOMAIN}/ is unreachable (DNS? Caddy down?)"
    elif [[ "$http_code" == "404" || "$http_code" == "405" || "$http_code" == "200" ]]; then
        check_pass "https://${HOOKSHOT_DOMAIN}/ responded HTTP ${http_code} (Caddy → Hookshot OK)"
    else
        check_warn "https://${HOOKSHOT_DOMAIN}/ returned HTTP ${http_code}"
    fi
fi

# ---------------------------------------------------------------------------
section "10. Synapse appservice ping (admin API)"
# ---------------------------------------------------------------------------

ADMIN_TOKEN=""
if [[ -n "${REGISTRATION_SHARED_SECRET:-}" && -n "${ADMIN_USERNAME:-}" ]]; then
    info "Fetching admin access token to test appservice ping…"
    login_resp="$(curl -fsSL --max-time 10 \
        -X POST "https://${MATRIX_DOMAIN}/_matrix/client/v3/login" \
        -H "Content-Type: application/json" \
        -d "{\"type\":\"m.login.password\",\"user\":\"${ADMIN_USERNAME}\",\"password\":\"${ADMIN_PASSWORD:-}\"}" \
        2>/dev/null || true)"
    ADMIN_TOKEN="$(echo "$login_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)"
fi

if [[ -n "$ADMIN_TOKEN" ]]; then
    ping_resp="$(curl -fsSL --max-time 10 \
        -X POST "https://${MATRIX_DOMAIN}/_synapse/admin/v1/appservice/matrix-hookshot/ping" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{}' 2>/dev/null || true)"

    if echo "$ping_resp" | grep -q '"duration_ms"'; then
        duration="$(echo "$ping_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('duration_ms','?'))" 2>/dev/null || echo '?')"
        check_pass "Synapse → Hookshot ping succeeded in ${duration}ms  ← the key check"
    elif echo "$ping_resp" | grep -q 'errcode'; then
        err="$(echo "$ping_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('errcode','?'),d.get('error',''))" 2>/dev/null || echo "$ping_resp")"
        check_fail "Synapse → Hookshot ping FAILED: ${err}"
        check_warn "This means Synapse cannot push events to Hookshot — bot will never respond."
    else
        check_warn "Unexpected ping response: ${ping_resp}"
    fi
else
    check_warn "Could not obtain admin token (ADMIN_PASSWORD not in .env) — skipping ping test"
    check_warn "Run manually:"
    echo  "    TOKEN=\$(curl -s -X POST https://${MATRIX_DOMAIN}/_matrix/client/v3/login \\"
    echo  "      -H 'Content-Type: application/json' \\"
    echo  "      -d '{\"type\":\"m.login.password\",\"user\":\"admin\",\"password\":\"YOUR_PW\"}' | python3 -c \"import sys,json; print(json.load(sys.stdin)['access_token'])\")"
    echo  "    curl -s -X POST https://${MATRIX_DOMAIN}/_synapse/admin/v1/appservice/matrix-hookshot/ping \\"
    echo  "      -H \"Authorization: Bearer \$TOKEN\" -H 'Content-Type: application/json' -d '{}'"
fi

# ---------------------------------------------------------------------------
section "Summary"
# ---------------------------------------------------------------------------
echo
echo -e "  ${GREEN}Passed: ${PASS}${RESET}   ${RED}Failed: ${FAIL}${RESET}"
echo
if [[ $FAIL -gt 0 ]]; then
    echo -e "  ${YELLOW}Fix the failing checks above, then re-run this script.${RESET}"
    echo -e "  Most issues are resolved by: ${CYAN}bash setup.sh --module hookshot${RESET}"
    exit 1
else
    echo -e "  ${GREEN}All checks passed. Hookshot should be wired up correctly.${RESET}"
    echo
    echo -e "  To test end-to-end: create a new room, invite ${CYAN}@hookshot:${SERVER_NAME}${RESET},"
    echo -e "  wait for it to join, then send: ${CYAN}!hookshot webhook test${RESET}"
fi
