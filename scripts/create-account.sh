#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/lib.sh"

REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_ENV="${REPO_DIR}/.env"

NONINTERACTIVE="false"
ASSUME_YES="false"
USERNAME=""
PASSWORD=""
PASSWORD_SOURCE=""
IS_ADMIN="false"
BASE_URL=""
SHARED_SECRET=""
SERVER_NAME=""

usage() {
    cat <<'EOF'
Usage:
  bash scripts/create-account.sh
  bash scripts/create-account.sh --username alice [--password 'long-secret'] [--admin] [--yes]

Options:
  --username VALUE       Localpart for the new Matrix user.
  --password VALUE       Password to assign. Must be at least 12 characters.
  --generate-password    Force generated password output in non-interactive mode.
  --admin                Grant Synapse admin privileges.
  --no-admin             Explicitly create a non-admin user.
  --yes                  Skip confirmation prompts.
  --base-url VALUE       Override Synapse base URL instead of reading MATRIX_DOMAIN from .env.
  --shared-secret VALUE  Override REGISTRATION_SHARED_SECRET instead of reading .env.
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
    fi

    SERVER_NAME="${SERVER_NAME:-unknown-server-name}"
}

check_dependencies() {
    command -v curl &>/dev/null || die "curl is required."
    command -v python3 &>/dev/null || die "python3 is required."
    command -v openssl &>/dev/null || die "openssl is required."
}

ensure_registration_config() {
    [[ -n "$BASE_URL" ]] || die "Could not determine Synapse base URL. Pass --base-url or ensure MATRIX_DOMAIN exists in .env."
    [[ -n "$SHARED_SECRET" ]] || die "Could not determine REGISTRATION_SHARED_SECRET. Pass --shared-secret or ensure it exists in .env."
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

fetch_nonce() {
    local nonce_response nonce

    info "Fetching registration nonce from Synapse…" >&2
    nonce_response="$(curl -fsSL "${BASE_URL}/_synapse/admin/v1/register")"
    nonce="$(echo "$nonce_response" | python3 -c "import sys,json; print(json.load(sys.stdin)['nonce'])")"

    [[ -n "$nonce" ]] || die "Could not retrieve nonce from Synapse. Is the server running and reachable?"
    printf '%s' "$nonce"
}

compute_mac() {
    local nonce="$1"
    local admin_mode="notadmin"

    if [[ "$IS_ADMIN" == "true" ]]; then
        admin_mode="admin"
    fi

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

create_account() {
    local nonce mac response_file http_status response_body err_info err_code err_msg
    local admin_json="False"
    local account_label="user"

    if [[ "$IS_ADMIN" == "true" ]]; then
        admin_json="True"
        account_label="admin user"
    fi

    nonce="$(fetch_nonce)"

    info "Computing registration MAC…"
    mac="$(compute_mac "$nonce")"

    info "Registering ${account_label} '${USERNAME}'…"

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
        success "Account '@${USERNAME}:${SERVER_NAME}' created successfully."
    else
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
            return
        fi

        if [[ -n "$err_code" || -n "$err_msg" ]]; then
            die "Failed to create account (HTTP ${http_status}, ${err_code}: ${err_msg})."
        fi
        die "Failed to create account (HTTP ${http_status}). Response: ${response_body}"
    fi

    echo
    echo -e "  ${BOLD}Login details${RESET}"
    echo -e "    Matrix ID:  ${CYAN}@${USERNAME}:${SERVER_NAME}${RESET}"
    echo -e "    Password:   ${CYAN}${PASSWORD}${RESET}"
    if [[ "$PASSWORD_SOURCE" == "generated" ]]; then
        echo -e "    ${YELLOW}This is a temporary password — ask the user to change it after first login.${RESET}"
    fi
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