#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/lib.sh"

REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_ENV="${REPO_DIR}/.env"
SYNAPSE_CONTAINER="matrix_synapse"
NONINTERACTIVE="false"
ASSUME_YES="false"
USERNAME=""
PASSWORD=""
PASSWORD_SOURCE=""
IS_ADMIN="false"

usage() {
    cat <<'EOF'
Usage:
  bash scripts/create-user.sh
  bash scripts/create-user.sh --username alice [--password 'long-secret'] [--admin] [--yes]

Options:
  --username VALUE       Localpart for the new Matrix user.
  --password VALUE       Password to assign. Must be at least 12 characters.
  --generate-password    Force generated password output in non-interactive mode.
  --admin                Grant Synapse admin privileges.
  --no-admin             Explicitly create a non-admin user.
  --yes                  Skip confirmation prompts in interactive mode.
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
    echo -e "${BOLD}Create Matrix user${RESET}"
    echo -e "─────────────────────────────────────────────────────"
    echo -e "Press Enter to accept defaults.\n"
}

read_server_name() {
    if [[ -f "$DEPLOY_ENV" ]]; then
        SERVER_NAME="$(sed -n 's/^SERVER_NAME=//p' "$DEPLOY_ENV" | head -n1)"
    fi
    SERVER_NAME="${SERVER_NAME:-unknown-server-name}"
}

check_dependencies() {
    command -v docker &>/dev/null || die "Docker is required."
    command -v openssl &>/dev/null || die "openssl is required."
}

check_synapse_container() {
    if ! docker inspect "$SYNAPSE_CONTAINER" &>/dev/null; then
        die "Container '$SYNAPSE_CONTAINER' not found. Start services first with: bash start.sh"
    fi

    local running
    running="$(docker inspect --format='{{.State.Running}}' "$SYNAPSE_CONTAINER" 2>/dev/null || echo "false")"
    if [[ "$running" != "true" ]]; then
        die "Container '$SYNAPSE_CONTAINER' is not running. Start services first with: bash start.sh"
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
            if [[ ${#pw_a} -lt 12 ]]; then
                warn "Password must be at least 12 characters."
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
        if [[ ${#PASSWORD} -lt 12 ]]; then
            die "Password must be at least 12 characters."
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
}

confirm_summary() {
    show_summary
    echo

    if [[ "$ASSUME_YES" == "true" ]]; then
        return
    fi

    ask_yn CONFIRM "Create this user now?" "y"
    [[ "$CONFIRM" == "y" ]] || die "Aborted."
}

create_user() {
    local cmd=(
        docker exec -i "$SYNAPSE_CONTAINER"
        register_new_matrix_user
        -c /data/homeserver.yaml
        -u "$USERNAME"
        -p "$PASSWORD"
    )

    if [[ "$IS_ADMIN" == "true" ]]; then
        cmd+=( -a )
    fi

    cmd+=( http://localhost:8008 )

    set +e
    local output
    output="$("${cmd[@]}" 2>&1)"
    local rc=$?
    set -e

    if [[ $rc -ne 0 ]]; then
        error "Failed to create user."
        echo "$output"
        exit $rc
    fi

    success "User created successfully."
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

    read_server_name
    check_dependencies
    check_synapse_container

    if [[ "$NONINTERACTIVE" == "true" ]]; then
        validate_noninteractive_inputs
    else
        prompt_username
        prompt_password
        prompt_admin_flag
    fi

    confirm_summary
    create_user
}

main "$@"