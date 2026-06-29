#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/lib.sh"

REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_ENV="${REPO_DIR}/.env"
MAS_CONTAINER="matrix_mas"
MAS_CONFIG="/config/config.yaml"

NONINTERACTIVE="false"
ASSUME_YES="false"
USERNAME=""
PASSWORD=""
PASSWORD_SOURCE=""
IS_ADMIN="false"
BASE_URL=""
SHARED_SECRET=""
SERVER_NAME=""
SERVER_IMPLEMENTATION=""
MAS_ENABLED="false"
MAS_LOCAL_LOGIN_ENABLED="true"
MAS_HOMESERVER_SECRET=""

usage() {
    cat <<'EOF'
Usage:
  bash scripts/create-account.sh
  bash scripts/create-account.sh --username alice [--password 'long-secret'] [--admin] [--yes]

Creates a Matrix account. Auth backend (MAS, legacy Synapse register, Tuwunel, etc.)
handles identity and credentials; Synapse server admin (--admin) is granted separately
via the homeserver admin API when a server admin token is available.

Options:
  --username VALUE       Localpart for the new Matrix user.
  --password VALUE       Password to assign. Must be at least 10 characters.
  --generate-password    Force generated password output in non-interactive mode.
  --admin                Grant Synapse homeserver admin privileges.
  --no-admin             Explicitly create a non-admin user.
  --yes                  Skip confirmation prompts.
  --base-url VALUE       Override homeserver base URL instead of reading MATRIX_DOMAIN from .env.
  --shared-secret VALUE  Override REGISTRATION_SHARED_SECRET (legacy Synapse path only).
  -h, --help             Show this help text.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --username)
                [[ $# -ge 2 ]] || die "Missing value for --username"
                USERNAME="$2"
                NONINTERACTIVE="true"
                shift 2
                ;;
            --password)
                [[ $# -ge 2 ]] || die "Missing value for --password"
                PASSWORD="$2"
                PASSWORD_SOURCE="custom"
                NONINTERACTIVE="true"
                shift 2
                ;;
            --generate-password)
                [[ -z "$PASSWORD" ]] || die "Cannot combine --generate-password with --password"
                PASSWORD_SOURCE="generated"
                NONINTERACTIVE="true"
                shift
                ;;
            --admin)
                IS_ADMIN="true"
                NONINTERACTIVE="true"
                shift
                ;;
            --no-admin)
                IS_ADMIN="false"
                NONINTERACTIVE="true"
                shift
                ;;
            --yes)
                ASSUME_YES="true"
                shift
                ;;
            --base-url)
                [[ $# -ge 2 ]] || die "Missing value for --base-url"
                BASE_URL="$2"
                shift 2
                ;;
            --shared-secret)
                [[ $# -ge 2 ]] || die "Missing value for --shared-secret"
                SHARED_SECRET="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown argument: $1"
                ;;
        esac
    done
}

print_banner() {
    echo
    echo -e "${BOLD}Create Matrix account${RESET}"
    echo -e "─────────────────────────────────────────────────────"
    echo -e "Press Enter to accept defaults.\n"
}

read_deploy_env() {
    if [[ -f "$DEPLOY_ENV" ]]; then
        if [[ -z "$SERVER_NAME" ]]; then
            SERVER_NAME="$(sed -n 's/^SERVER_NAME=//p' "$DEPLOY_ENV" | head -n1)"
        fi
        if [[ -z "$BASE_URL" ]]; then
            local matrix_domain
            matrix_domain="$(sed -n 's/^MATRIX_DOMAIN=//p' "$DEPLOY_ENV" | head -n1)"
            if [[ -n "$matrix_domain" ]]; then
                BASE_URL="https://${matrix_domain}"
            fi
        fi
        if [[ -z "$SHARED_SECRET" ]]; then
            SHARED_SECRET="$(sed -n 's/^REGISTRATION_SHARED_SECRET=//p' "$DEPLOY_ENV" | head -n1)"
        fi
        local _env_impl _mas_enabled _mas_local_login _mas_hs_secret
        _env_impl="$(sed -n 's/^SERVER_IMPLEMENTATION=//p' "$DEPLOY_ENV" | head -n1)"
        if [[ -n "$_env_impl" ]]; then
            SERVER_IMPLEMENTATION="$_env_impl"
        fi
        _mas_enabled="$(sed -n 's/^MAS_ENABLED=//p' "$DEPLOY_ENV" | head -n1)"
        if [[ "$_mas_enabled" == "true" ]]; then
            MAS_ENABLED="true"
        fi
        _mas_local_login="$(sed -n 's/^MAS_LOCAL_LOGIN_ENABLED=//p' "$DEPLOY_ENV" | head -n1)"
        if [[ "$_mas_local_login" == "false" ]]; then
            MAS_LOCAL_LOGIN_ENABLED="false"
        fi
        if [[ -z "$MAS_HOMESERVER_SECRET" ]]; then
            MAS_HOMESERVER_SECRET="$(sed -n 's/^MAS_HOMESERVER_SECRET=//p' "$DEPLOY_ENV" | head -n1)"
        fi
    fi

    SERVER_NAME="${SERVER_NAME:-unknown-server-name}"
    SERVER_IMPLEMENTATION="${SERVER_IMPLEMENTATION:-synapse}"
}

uses_mas_auth() {
    [[ "${SERVER_IMPLEMENTATION,,}" == "synapse" && "$MAS_ENABLED" == "true" ]]
}

is_synapse() {
    [[ "${SERVER_IMPLEMENTATION,,}" == "synapse" ]]
}

check_dependencies() {
    command -v curl &>/dev/null || die "curl is required."
    command -v python3 &>/dev/null || die "python3 is required."
    command -v openssl &>/dev/null || die "openssl is required."
    if uses_mas_auth; then
        command -v docker &>/dev/null || die "docker is required when MAS is enabled."
    fi
}

ensure_registration_config() {
    [[ -n "$BASE_URL" ]] || die "Could not determine homeserver base URL. Pass --base-url or ensure MATRIX_DOMAIN exists in .env."

    if uses_mas_auth; then
        if [[ "$MAS_LOCAL_LOGIN_ENABLED" != "true" ]]; then
            die "Password account creation requires features.local_login_enabled=true (MAS password login is disabled). Enable local login in deploy.yaml, use SSO to sign in, or pass --access-token to med-admin."
        fi
    elif ! is_synapse; then
        [[ -n "$SHARED_SECRET" ]] || die "Could not determine REGISTRATION_SHARED_SECRET. Pass --shared-secret or ensure it exists in .env."
    elif [[ -z "$MAS_HOMESERVER_SECRET" && -z "$SHARED_SECRET" ]]; then
        die "Could not determine MAS_HOMESERVER_SECRET or REGISTRATION_SHARED_SECRET. Run bash apply.sh first."
    fi

    if [[ "$IS_ADMIN" == "true" ]] && is_synapse && [[ -z "$MAS_HOMESERVER_SECRET" && -z "$SHARED_SECRET" ]]; then
        die "Could not determine credentials to grant Synapse admin. Run bash apply.sh first."
    fi
}

generate_temp_password() {
    openssl rand -base64 24 | tr -d '\n=+/' | cut -c1-20
}

prompt_username() {
    ask USERNAME "Username (localpart, e.g. alice)" ""
    while [[ -z "$USERNAME" ]]; do
        warn "Username is required."
        ask USERNAME "Username (localpart, e.g. alice)" ""
    done
}

prompt_password() {
    ask_yn USE_CUSTOM_PASSWORD "Set a custom password now?" "n"

    if [[ "$USE_CUSTOM_PASSWORD" == "y" ]]; then
        local pw_a pw_b
        while true; do
            ask_secret pw_a "Password"
            if [[ ${#pw_a} -lt 10 ]]; then
                warn "Password must be at least 10 characters."
                continue
            fi
            ask_secret pw_b "Confirm password"
            if [[ "$pw_a" != "$pw_b" ]]; then
                warn "Passwords do not match. Try again."
                continue
            fi
            PASSWORD="$pw_a"
            PASSWORD_SOURCE="custom"
            break
        done
    else
        PASSWORD="$(generate_temp_password)"
        PASSWORD_SOURCE="generated"
    fi
}

prompt_admin_flag() {
    ask_yn IS_ADMIN_INPUT "Grant admin privileges?" "n"
    if [[ "$IS_ADMIN_INPUT" == "y" ]]; then
        IS_ADMIN="true"
    else
        IS_ADMIN="false"
    fi
}

validate_noninteractive_inputs() {
    [[ -n "$USERNAME" ]] || die "--username is required in non-interactive mode"

    if [[ -n "$PASSWORD" ]]; then
        if [[ ${#PASSWORD} -lt 10 ]]; then
            die "Password must be at least 10 characters."
        fi
        PASSWORD_SOURCE="custom"
        return
    fi

    PASSWORD="$(generate_temp_password)"
    PASSWORD_SOURCE="generated"
}

show_summary() {
    echo
    echo -e "${BOLD}Summary${RESET}"
    echo -e "─────────────────────────────────────────────────────"
    echo -e "Username       : ${CYAN}${USERNAME}${RESET}"
    echo -e "Matrix ID      : ${CYAN}@${USERNAME}:${SERVER_NAME}${RESET}"
    echo -e "Password mode  : ${CYAN}${PASSWORD_SOURCE}${RESET}"
    echo -e "Admin          : ${CYAN}${IS_ADMIN}${RESET}"
    echo -e "Homeserver     : ${CYAN}${BASE_URL}${RESET}"
    if uses_mas_auth; then
        echo -e "Auth backend   : ${CYAN}MAS${RESET}"
    elif is_synapse; then
        echo -e "Auth backend   : ${CYAN}Synapse (legacy register)${RESET}"
    fi
}

confirm_summary() {
    show_summary
    echo

    if [[ "$ASSUME_YES" == "true" ]]; then
        return
    fi

    ask_yn CONFIRM "Create this account now?" "y"
    [[ "$CONFIRM" == "y" ]] || die "Aborted."
}

print_login_details() {
    echo
    echo -e "  ${BOLD}Login details${RESET}"
    echo -e "    Matrix ID:  ${CYAN}@${USERNAME}:${SERVER_NAME}${RESET}"
    echo -e "    Password:   ${CYAN}${PASSWORD}${RESET}"
    if [[ "$PASSWORD_SOURCE" == "generated" ]]; then
        echo -e "    ${YELLOW}This is a temporary password — ask the user to change it after first login.${RESET}"
    fi
}

matrix_user_id() {
    printf '@%s:%s' "$USERNAME" "$SERVER_NAME"
}

encoded_matrix_user_id() {
    python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$(matrix_user_id)"
}

# ---------------------------------------------------------------------------
# Auth backends — identity + credentials only (no Synapse admin flag).
# ---------------------------------------------------------------------------

wait_for_mas_container() {
    local attempt=0
    local max=30

    if ! docker inspect "$MAS_CONTAINER" &>/dev/null; then
        die "MAS container '${MAS_CONTAINER}' is not running. Start services with 'bash start.sh' first."
    fi

    info "Waiting for MAS to become healthy…" >&2
    until [[ "$(docker inspect --format='{{.State.Health.Status}}' "$MAS_CONTAINER" 2>/dev/null)" == "healthy" ]]; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max ]]; then
            die "MAS has not become healthy after $((max * 5))s. Check 'docker logs ${MAS_CONTAINER}'."
        fi
        sleep 5
    done
}

mas_cli() {
    docker exec "$MAS_CONTAINER" mas-cli -c "$MAS_CONFIG" "$@"
}

mas_register_user() {
    mas_cli manage register-user \
        --username "$USERNAME" \
        --password "$PASSWORD" \
        --no-admin \
        --yes \
        --ignore-password-complexity
}

mas_set_password() {
    mas_cli manage set-password "$USERNAME" "$PASSWORD" --ignore-complexity
}

register_auth_mas() {
    wait_for_mas_container
    info "Registering '${USERNAME}' via MAS…"

    local register_output register_status
    set +e
    register_output="$(mas_register_user 2>&1)"
    register_status=$?
    set -e

    if [[ $register_status -eq 0 ]]; then
        return 0
    fi

    if [[ "$register_output" == *"User already exists"* ]]; then
        if [[ "$MAS_LOCAL_LOGIN_ENABLED" == "true" ]]; then
            warn "User '${USERNAME}' already exists in MAS. Updating password (local login is enabled)…"
            mas_set_password
        else
            warn "User '${USERNAME}' already exists in MAS. Skipping password update (MAS password login is disabled)."
        fi
        return 0
    fi

    if [[ "$register_output" == *"Username not available on homeserver"* ]]; then
        die "User '$(matrix_user_id)' exists on Synapse but not in MAS. Recover manually: remove the Synapse user or link it in MAS before re-running this script."
    fi

    die "Failed to register user in MAS: ${register_output}"
}

register_auth_tuwunel() {
    local payload response_file http_status response_body err_info err_code err_msg

    info "Creating user '${USERNAME}' via Tuwunel registration API…"

    payload="$(python3 - <<PYEOF
import json

print(json.dumps({
    "username": ${USERNAME@Q},
    "password": ${PASSWORD@Q},
    "auth": {
        "type": "m.login.registration_token",
        "token": ${SHARED_SECRET@Q},
    },
}))
PYEOF
)"

    response_file="$(mktemp)"
    trap 'rm -f "${response_file:-}"' EXIT

    http_status="$(curl -sS -o "$response_file" -w "%{http_code}" \
        -X POST "${BASE_URL}/_matrix/client/v3/register" \
        -H "Content-Type: application/json" \
        --data-binary @- <<< "$payload")"

    if [[ "$http_status" == "200" ]] || [[ "$http_status" == "201" ]]; then
        return 0
    fi

    response_body="$(cat "$response_file")"
    err_info="$(python3 - <<'PYEOF' "$response_body"
import json
import sys

raw = sys.argv[1]
try:
    data = json.loads(raw) if raw else {}
except Exception:
    data = {}

print(f"{data.get('errcode', '')}\t{data.get('error', '')}")
PYEOF
)"
    err_code="${err_info%%$'\t'*}"
    err_msg="${err_info#*$'\t'}"

    if [[ "$http_status" == "400" && "$err_code" == "M_USER_IN_USE" ]]; then
        warn "User '${USERNAME}' already exists. Skipping."
        return 0
    fi

    if [[ -n "$err_code" || -n "$err_msg" ]]; then
        die "Failed to create account (HTTP ${http_status}, ${err_code}: ${err_msg})."
    fi
    die "Failed to create account (HTTP ${http_status}). Response: ${response_body}"
}

fetch_synapse_register_nonce() {
    local nonce_response nonce

    info "Fetching registration nonce from Synapse…" >&2
    nonce_response="$(curl -fsSL "${BASE_URL}/_synapse/admin/v1/register")"
    nonce="$(echo "$nonce_response" | python3 -c "import sys,json; print(json.load(sys.stdin)['nonce'])")"

    [[ -n "$nonce" ]] || die "Could not retrieve nonce from Synapse. Is the server running and reachable?"
    printf '%s' "$nonce"
}

compute_synapse_register_mac() {
    local nonce="$1"
    local admin_mode="$2"

    python3 - <<PYEOF
import hmac, hashlib

nonce = ${nonce@Q}
username = ${USERNAME@Q}
password = ${PASSWORD@Q}
secret = ${SHARED_SECRET@Q}
admin_mode = ${admin_mode@Q}

mac = hmac.new(
    secret.encode("utf-8"),
    b"\x00".join([
        nonce.encode("utf-8"),
        username.encode("utf-8"),
        password.encode("utf-8"),
        admin_mode.encode("utf-8"),
    ]),
    hashlib.sha1,
).hexdigest()

print(mac)
PYEOF
}

# Legacy Synapse shared-secret register (auth + optional admin in one call when no server token).
register_auth_synapse_shared_secret() {
    local admin_mode="$1"
    local nonce mac response_file http_status response_body err_info err_code err_msg
    local admin_json="False"

    [[ "$admin_mode" == "admin" ]] && admin_json="True"

    nonce="$(fetch_synapse_register_nonce)"
    mac="$(compute_synapse_register_mac "$nonce" "$admin_mode")"

    local json_payload
    json_payload="$(python3 - <<PYEOF
import json

payload = {
    "nonce": ${nonce@Q},
    "username": ${USERNAME@Q},
    "password": ${PASSWORD@Q},
    "admin": ${admin_json},
    "mac": ${mac@Q},
}

print(json.dumps(payload))
PYEOF
)"

    response_file="$(mktemp)"
    trap 'rm -f "${response_file:-}"' EXIT

    http_status="$(curl -sS -o "$response_file" -w "%{http_code}" \
        -X POST "${BASE_URL}/_synapse/admin/v1/register" \
        -H "Content-Type: application/json" \
        --data-binary @- <<< "$json_payload")"

    unset json_payload

    if [[ "$http_status" == "200" ]] || [[ "$http_status" == "201" ]]; then
        return 0
    fi

    response_body="$(cat "$response_file")"
    err_info="$(python3 - <<'PYEOF' "$response_body"
import json
import sys

raw = sys.argv[1]
try:
    data = json.loads(raw) if raw else {}
except Exception:
    data = {}

errcode = data.get("errcode", "") if isinstance(data, dict) else ""
error = data.get("error", "") if isinstance(data, dict) else ""
print(f"{errcode}\t{error}")
PYEOF
)"

    err_code="${err_info%%$'\t'*}"
    err_msg="${err_info#*$'\t'}"

    if [[ "$http_status" == "400" && "$err_code" == "M_USER_IN_USE" ]]; then
        warn "User '${USERNAME}' already exists. Skipping."
        return 0
    fi

    if [[ -n "$err_code" || -n "$err_msg" ]]; then
        die "Failed to create account (HTTP ${http_status}, ${err_code}: ${err_msg})."
    fi
    die "Failed to create account (HTTP ${http_status}). Response: ${response_body}"
}

register_auth_synapse_legacy() {
    info "Registering '${USERNAME}' via Synapse shared-secret register…"
    register_auth_synapse_shared_secret "notadmin"
}

register_auth_user() {
    if [[ "${SERVER_IMPLEMENTATION,,}" == "tuwunel" ]]; then
        register_auth_tuwunel
    elif uses_mas_auth; then
        register_auth_mas
    elif is_synapse; then
        # Legacy bootstrap: no server admin token — create user + admin in one register call.
        if [[ "$IS_ADMIN" == "true" && -z "$MAS_HOMESERVER_SECRET" ]]; then
            info "Registering admin user '${USERNAME}' via Synapse shared-secret register…"
            register_auth_synapse_shared_secret "admin"
        else
            register_auth_synapse_legacy
        fi
    else
        die "Unsupported server implementation: ${SERVER_IMPLEMENTATION}"
    fi
}

# ---------------------------------------------------------------------------
# Homeserver admin — Synapse is the source of truth; auth backend is irrelevant.
# ---------------------------------------------------------------------------

promote_synapse_admin_via_server_token() {
    local response_file http_status response_body

    response_file="$(mktemp)"
    trap 'rm -f "${response_file:-}"' RETURN

    info "Granting Synapse admin to '$(matrix_user_id)'…"
    http_status="$(curl -sS -o "$response_file" -w "%{http_code}" \
        -X PUT "${BASE_URL}/_synapse/admin/v2/users/$(encoded_matrix_user_id)" \
        -H "Authorization: Bearer ${MAS_HOMESERVER_SECRET}" \
        -H "Content-Type: application/json" \
        --data-binary '{"admin": true}')"

    if [[ "$http_status" == "200" ]] || [[ "$http_status" == "201" ]]; then
        return 0
    fi

    response_body="$(cat "$response_file")"
    die "Failed to grant Synapse admin (HTTP ${http_status}). Response: ${response_body}"
}

grant_homeserver_admin_if_requested() {
    [[ "$IS_ADMIN" == "true" ]] || return 0

    if [[ "${SERVER_IMPLEMENTATION,,}" == "tuwunel" ]]; then
        info "Tuwunel grants admin to the first registered user automatically (grant_admin_to_first_user)."
        return 0
    fi

    if ! is_synapse; then
        return 0
    fi

    if [[ -n "$MAS_HOMESERVER_SECRET" ]]; then
        promote_synapse_admin_via_server_token
        return 0
    fi

    # Legacy Synapse without a server admin token: admin was set during shared-secret register.
    if [[ "$IS_ADMIN" == "true" ]]; then
        info "Synapse admin was granted during shared-secret registration (no server admin token available)."
    fi
}

create_account() {
    register_auth_user
    grant_homeserver_admin_if_requested
    success "Account '$(matrix_user_id)' created successfully."
    print_login_details
}

main() {
    parse_args "$@"

    if [[ "$NONINTERACTIVE" != "true" ]]; then
        print_banner
    fi

    read_deploy_env
    check_dependencies
    ensure_registration_config

    if [[ "$NONINTERACTIVE" == "true" ]]; then
        validate_noninteractive_inputs
    else
        prompt_username
        prompt_password
        prompt_admin_flag
    fi

    confirm_summary
    create_account
}

main "$@"
