#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/lib.sh"

REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_ENV="${REPO_DIR}/.env"

COMMAND=""
ASSUME_YES="false"
BASE_URL=""
SERVER_NAME=""
SHARED_SECRET=""
AUTH_USERNAME=""
AUTH_PASSWORD=""
ACCESS_TOKEN=""
BOOTSTRAP_USERNAME="med-admin"
BOOTSTRAP_PASSWORD=""
BOOTSTRAP_GENERATE_PASSWORD="false"
LIST_FILTER=""
LIST_LIMIT="100"
LIST_FROM=""
TARGET_ACCOUNT=""
RESET_PASSWORD_VALUE=""

usage() {
    cat <<'EOF'
Usage:
  bash scripts/med-admin.sh bootstrap [--username med-admin] [--password 'long-secret'] [--yes]
  bash scripts/med-admin.sh list-accounts [--filter alice] [--limit 100] [--from 0]
  bash scripts/med-admin.sh list-admins [--filter alice] [--limit 100] [--from 0]
  bash scripts/med-admin.sh get-account USERNAME_OR_MXID
  bash scripts/med-admin.sh reset-password USERNAME_OR_MXID [--password 'new-long-secret'] [--yes]

Auth options for admin API commands:
  --access-token VALUE   Use an existing Synapse admin access token.
  --admin-username VALUE Use this admin username to obtain a token.
  --admin-password VALUE Use this admin password to obtain a token.

Shared options:
  --base-url VALUE       Override Synapse base URL instead of reading MATRIX_DOMAIN from .env.
  --yes                  Skip confirmation prompts where applicable.
  -h, --help             Show this help text.
EOF
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
        if [[ -z "$AUTH_USERNAME" ]]; then
            AUTH_USERNAME="$(sed -n 's/^ADMIN_USERNAME=//p' "$DEPLOY_ENV" | head -n1)"
        fi
    fi
}

check_common_dependencies() {
    command -v curl &>/dev/null || die "curl is required."
    command -v python3 &>/dev/null || die "python3 is required."
}

ensure_base_url() {
    [[ -n "$BASE_URL" ]] || die "Could not determine Synapse base URL. Pass --base-url or ensure MATRIX_DOMAIN exists in .env."
}

ensure_server_name() {
    [[ -n "$SERVER_NAME" ]] || die "Could not determine SERVER_NAME. Ensure it exists in .env."
}

ensure_auth_username() {
    [[ -n "$AUTH_USERNAME" ]] || die "Could not determine an admin username. Pass --admin-username or ensure ADMIN_USERNAME exists in .env."
}

ensure_bootstrap_config() {
    [[ -n "$SHARED_SECRET" ]] || die "Could not determine REGISTRATION_SHARED_SECRET. Pass --shared-secret through create-account or ensure it exists in .env."
}

json_get() {
    local expr="$1"
    python3 -c "import sys,json; data=json.load(sys.stdin); print(${expr})"
}

to_user_id() {
    local raw="$1"

    ensure_server_name

    if [[ "$raw" == @*:* ]]; then
        printf '%s' "$raw"
        return
    fi

    raw="${raw#@}"
    printf '@%s:%s' "$raw" "$SERVER_NAME"
}

prompt_for_auth_password() {
    if [[ -n "$AUTH_PASSWORD" ]]; then
        return
    fi

    ensure_auth_username
    info "Password login is required to obtain an admin token."
    ask_secret AUTH_PASSWORD "Password for ${AUTH_USERNAME}"
    [[ -n "$AUTH_PASSWORD" ]] || die "Admin password is required unless --access-token is provided."
}

prompt_for_reset_password() {
    if [[ -n "$RESET_PASSWORD_VALUE" ]]; then
        [[ ${#RESET_PASSWORD_VALUE} -ge 12 ]] || die "Password must be at least 12 characters."
        return
    fi

    local pw_a=""
    local pw_b=""

    while true; do
        ask_secret pw_a "New password"
        if [[ ${#pw_a} -lt 12 ]]; then
            warn "Password must be at least 12 characters."
            continue
        fi

        ask_secret pw_b "Confirm new password"
        if [[ "$pw_a" != "$pw_b" ]]; then
            warn "Passwords do not match. Try again."
            continue
        fi

        RESET_PASSWORD_VALUE="$pw_a"
        break
    done
}

parse_global_options() {
    REMAINING_ARGS=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --base-url)
                [[ $# -ge 2 ]] || die "Missing value for --base-url"
                BASE_URL="$2"
                shift 2
                ;;
            --access-token)
                [[ $# -ge 2 ]] || die "Missing value for --access-token"
                ACCESS_TOKEN="$2"
                shift 2
                ;;
            --admin-username)
                [[ $# -ge 2 ]] || die "Missing value for --admin-username"
                AUTH_USERNAME="$2"
                shift 2
                ;;
            --admin-password)
                [[ $# -ge 2 ]] || die "Missing value for --admin-password"
                AUTH_PASSWORD="$2"
                shift 2
                ;;
            --yes)
                ASSUME_YES="true"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                REMAINING_ARGS+=("$1")
                shift
                ;;
        esac
    done
}

urlencode() {
    python3 - "$1" <<PYEOF
import sys
import urllib.parse

print(urllib.parse.quote(sys.argv[1], safe=''))
PYEOF
}

parse_bootstrap_args() {
    local args=("${REMAINING_ARGS[@]}")
    local idx=0

    while [[ $idx -lt ${#args[@]} ]]; do
        case "${args[$idx]}" in
            --username)
                ((idx + 1 < ${#args[@]})) || die "Missing value for --username"
                BOOTSTRAP_USERNAME="${args[$((idx + 1))]}"
                idx=$((idx + 2))
                ;;
            --password)
                ((idx + 1 < ${#args[@]})) || die "Missing value for --password"
                BOOTSTRAP_PASSWORD="${args[$((idx + 1))]}"
                idx=$((idx + 2))
                ;;
            --generate-password)
                BOOTSTRAP_GENERATE_PASSWORD="true"
                idx=$((idx + 1))
                ;;
            --shared-secret)
                ((idx + 1 < ${#args[@]})) || die "Missing value for --shared-secret"
                SHARED_SECRET="${args[$((idx + 1))]}"
                idx=$((idx + 2))
                ;;
            *)
                die "Unknown bootstrap argument: ${args[$idx]}"
                ;;
        esac
    done
}

parse_list_args() {
    local args=("${REMAINING_ARGS[@]}")
    local idx=0

    while [[ $idx -lt ${#args[@]} ]]; do
        case "${args[$idx]}" in
            --filter)
                ((idx + 1 < ${#args[@]})) || die "Missing value for --filter"
                LIST_FILTER="${args[$((idx + 1))]}"
                idx=$((idx + 2))
                ;;
            --limit)
                ((idx + 1 < ${#args[@]})) || die "Missing value for --limit"
                LIST_LIMIT="${args[$((idx + 1))]}"
                idx=$((idx + 2))
                ;;
            --from)
                ((idx + 1 < ${#args[@]})) || die "Missing value for --from"
                LIST_FROM="${args[$((idx + 1))]}"
                idx=$((idx + 2))
                ;;
            *)
                die "Unknown list argument: ${args[$idx]}"
                ;;
        esac
    done
}

parse_get_account_args() {
    [[ ${#REMAINING_ARGS[@]} -eq 1 ]] || die "Usage: bash scripts/med-admin.sh get-account USERNAME_OR_MXID"
    TARGET_ACCOUNT="${REMAINING_ARGS[0]}"
}

parse_reset_password_args() {
    [[ ${#REMAINING_ARGS[@]} -ge 1 ]] || die "Usage: bash scripts/med-admin.sh reset-password USERNAME_OR_MXID [--password 'new-long-secret'] [--yes]"
    TARGET_ACCOUNT="${REMAINING_ARGS[0]}"

    local args=("${REMAINING_ARGS[@]:1}")
    local idx=0
    while [[ $idx -lt ${#args[@]} ]]; do
        case "${args[$idx]}" in
            --password)
                ((idx + 1 < ${#args[@]})) || die "Missing value for --password"
                RESET_PASSWORD_VALUE="${args[$((idx + 1))]}"
                idx=$((idx + 2))
                ;;
            *)
                die "Unknown reset-password argument: ${args[$idx]}"
                ;;
        esac
    done
}

get_admin_token() {
    if [[ -n "$ACCESS_TOKEN" ]]; then
        printf '%s' "$ACCESS_TOKEN"
        return
    fi

    ensure_base_url
    ensure_auth_username
    prompt_for_auth_password

    local login_payload login_response token error_info err_code err_msg
    login_payload="$(python3 - <<PYEOF
import json

payload = {
    "type": "m.login.password",
    "user": ${AUTH_USERNAME@Q},
    "password": ${AUTH_PASSWORD@Q},
}

print(json.dumps(payload))
PYEOF
)"

    login_response="$(curl -sS -X POST "${BASE_URL}/_matrix/client/v3/login" \
        -H "Content-Type: application/json" \
        --data-binary @- <<< "$login_payload" || true)"

    token="$(echo "$login_response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)"
    if [[ -n "$token" ]]; then
        ACCESS_TOKEN="$token"
        printf '%s' "$token"
        return
    fi

    error_info="$(python3 - <<'PYEOF' "$login_response"
import json
import sys

raw = sys.argv[1]
try:
    data = json.loads(raw) if raw else {}
except Exception:
    data = {}

print(f"{data.get('errcode','')}\t{data.get('error','')}")
PYEOF
)"
    err_code="${error_info%%$'\t'*}"
    err_msg="${error_info#*$'\t'}"
    die "Could not obtain an admin access token via password login${err_code:+ (${err_code}: ${err_msg})}. If local password login is disabled, this v1 tool requires an explicit --access-token."
}

api_request() {
    local method="$1"
    local endpoint="$2"
    local payload="${3:-}"
    local response_file http_status response_body err_info err_code err_msg curl_args

    ensure_base_url

    response_file="$(mktemp)"
    curl_args=(
        -sS
        -o "$response_file"
        -w "%{http_code}"
        -X "$method"
        "${BASE_URL}/_synapse/admin/${endpoint}"
        -H "Authorization: Bearer $(get_admin_token)"
    )

    if [[ -n "$payload" ]]; then
        curl_args+=(
            -H "Content-Type: application/json"
            --data-binary @-
        )
        http_status="$(curl "${curl_args[@]}" <<< "$payload")"
    else
        http_status="$(curl "${curl_args[@]}")"
    fi

    response_body="$(cat "$response_file")"
    rm -f "$response_file"

    if [[ "$http_status" =~ ^2 ]]; then
        printf '%s' "$response_body"
        return
    fi

    err_info="$(python3 - <<'PYEOF' "$response_body"
import json
import sys

raw = sys.argv[1]
try:
    data = json.loads(raw) if raw else {}
except Exception:
    data = {}

print(f"{data.get('errcode','')}\t{data.get('error','')}")
PYEOF
)"
    err_code="${err_info%%$'\t'*}"
    err_msg="${err_info#*$'\t'}"
    if [[ -n "$err_code" || -n "$err_msg" ]]; then
        die "Synapse admin API request failed (HTTP ${http_status}, ${err_code}: ${err_msg})."
    fi
    die "Synapse admin API request failed (HTTP ${http_status}). Response: ${response_body}"
}

run_bootstrap() {
    local create_args=(--username "$BOOTSTRAP_USERNAME" --admin)

    read_deploy_env
    ensure_bootstrap_config

    if [[ -n "$BASE_URL" ]]; then
        create_args+=(--base-url "$BASE_URL")
    fi
    if [[ -n "$SHARED_SECRET" ]]; then
        create_args+=(--shared-secret "$SHARED_SECRET")
    fi
    if [[ -n "$BOOTSTRAP_PASSWORD" ]]; then
        create_args+=(--password "$BOOTSTRAP_PASSWORD")
    elif [[ "$BOOTSTRAP_GENERATE_PASSWORD" == "true" ]]; then
        create_args+=(--generate-password)
    fi
    if [[ "$ASSUME_YES" == "true" ]]; then
        create_args+=(--yes)
    fi

    info "Bootstrapping admin account '${BOOTSTRAP_USERNAME}' via shared-secret registration…"
    bash "${SCRIPT_DIR}/create-account.sh" "${create_args[@]}"
}

render_account_table() {
    python3 -c "import json, sys; data=json.load(sys.stdin); users=data.get('users', []); print('USER_ID\tADMIN\tDEACTIVATED\tLOCKED\tDISPLAYNAME'); [print(f\"{user.get('name', '')}\\t{user.get('admin', False)}\\t{user.get('deactivated', False)}\\t{user.get('locked', False)}\\t{user.get('displayname') or ''}\") for user in users]; next_token=data.get('next_token'); total=data.get('total'); print(f'NEXT_TOKEN\\t{next_token}') if next_token is not None else None; print(f'TOTAL\\t{total}') if total is not None else None"
}

run_list_accounts() {
    local endpoint query encoded_filter

    read_deploy_env
    check_common_dependencies
    ensure_base_url

    query="v2/users?limit=${LIST_LIMIT}&guests=false"
    if [[ -n "$LIST_FROM" ]]; then
        query+="&from=${LIST_FROM}"
    fi
    if [[ -n "$LIST_FILTER" ]]; then
        encoded_filter="$(urlencode "$LIST_FILTER")"
        query+="&name=${encoded_filter}"
    fi
    if [[ "$COMMAND" == "list-admins" ]]; then
        query+="&admins=true"
    fi

    endpoint="$query"
    api_request GET "$endpoint" | render_account_table
}

run_get_account() {
    local user_id encoded_user_id

    read_deploy_env
    check_common_dependencies
    ensure_base_url
    user_id="$(to_user_id "$TARGET_ACCOUNT")"
    encoded_user_id="$(urlencode "$user_id")"

    api_request GET "v2/users/${encoded_user_id}" | python3 -c "import json, sys; data=json.load(sys.stdin); print(f\"User ID:      {data.get('name', '')}\"); print(f\"Admin:        {data.get('admin', False)}\"); print(f\"Deactivated:  {data.get('deactivated', False)}\"); print(f\"Locked:       {data.get('locked', False)}\"); print(f\"Guest:        {data.get('is_guest', False)}\"); print(f\"Display name: {data.get('displayname') or ''}\"); print(f\"Avatar URL:   {data.get('avatar_url') or ''}\"); print(f\"Creation ts:  {data.get('creation_ts', '')}\"); print(f\"Last seen ts: {data.get('last_seen_ts', '')}\")"
}

confirm_reset_password() {
    local user_id="$1"

    if [[ "$ASSUME_YES" == "true" ]]; then
        return
    fi

    echo
    echo -e "${BOLD}Reset password${RESET}"
    echo -e "  User ID:  ${CYAN}${user_id}${RESET}"
    ask_yn CONFIRM_RESET "Reset this password now?" "n"
    [[ "$CONFIRM_RESET" == "y" ]] || die "Aborted."
}

run_reset_password() {
    local user_id encoded_user_id payload

    read_deploy_env
    check_common_dependencies
    ensure_base_url
    user_id="$(to_user_id "$TARGET_ACCOUNT")"
    encoded_user_id="$(urlencode "$user_id")"
    prompt_for_reset_password
    confirm_reset_password "$user_id"

    payload="$(python3 - <<PYEOF
import json

print(json.dumps({
    "new_password": ${RESET_PASSWORD_VALUE@Q},
    "logout_devices": True,
}))
PYEOF
)"

    api_request POST "v1/reset_password/${encoded_user_id}" "$payload" >/dev/null
    success "Password reset for '${user_id}'."
}

main() {
    [[ $# -ge 1 ]] || {
        usage
        exit 1
    }

    COMMAND="$1"
    shift

    parse_global_options "$@"

    case "$COMMAND" in
        bootstrap)
            parse_bootstrap_args
            run_bootstrap
            ;;
        list-accounts|list-admins)
            parse_list_args
            run_list_accounts
            ;;
        get-account)
            parse_get_account_args
            run_get_account
            ;;
        reset-password)
            parse_reset_password_args
            run_reset_password
            ;;
        -h|--help|help)
            usage
            ;;
        *)
            die "Unknown command: ${COMMAND}"
            ;;
    esac
}

main "$@"