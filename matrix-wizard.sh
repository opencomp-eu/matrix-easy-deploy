#!/usr/bin/env bash
# =============================================================================
#  matrix-easy-deploy  —  setup.sh
#  Interactive setup wizard entrypoint for a self-hosted Matrix homeserver.
#
#  This file orchestrates the setup flow and delegates implementation details
#  to scripts under scripts/setup/.
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"
# shellcheck source=scripts/sso.sh
source "${SCRIPT_DIR}/scripts/sso.sh"

# shellcheck source=scripts/setup/banner.sh
source "${SCRIPT_DIR}/scripts/setup/banner.sh"
# shellcheck source=scripts/setup/dependencies.sh
source "${SCRIPT_DIR}/scripts/setup/dependencies.sh"
# shellcheck source=scripts/setup/config.sh
source "${SCRIPT_DIR}/scripts/setup/config.sh"
# shellcheck source=scripts/setup/generate.sh
source "${SCRIPT_DIR}/scripts/setup/generate.sh"
# shellcheck source=scripts/setup/runtime.sh
source "${SCRIPT_DIR}/scripts/setup/runtime.sh"
# shellcheck source=scripts/setup/summary.sh
source "${SCRIPT_DIR}/scripts/setup/summary.sh"
# shellcheck source=scripts/setup/modules.sh
source "${SCRIPT_DIR}/scripts/setup/modules.sh"

IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"
DEPLOY_ENV="${SCRIPT_DIR}/.env"

run_full_setup() {
    print_banner
    check_dependencies

    echo
    echo -e "${BOLD}  Step 1 of 5 — Configuration${RESET}"
    gather_config

    echo
    echo -e "${BOLD}  Step 2 of 5 — Generating configuration files${RESET}"
    generate_config

    echo
    echo -e "${BOLD}  Step 3 of 5 — Docker infrastructure${RESET}"
    setup_docker

    echo
    echo -e "${BOLD}  Step 4 of 5 — Starting services${RESET}"
    start_services

    echo
    echo -e "${BOLD}  Step 5 of 5 — Creating admin user${RESET}"
    setup_admin

    print_summary

    # Offer optional module installation
    echo
    echo -e "${BOLD}  Optional modules${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  Your core Matrix stack is ready. Would you like to install any optional bridges or bots?"
    echo
    local _available_modules
    mapfile -t _available_modules < <(list_available_modules)
    if [[ ${#_available_modules[@]} -gt 0 ]]; then
        local i=1
        for module in "${_available_modules[@]}"; do
            echo -e "  ${CYAN}${i})${RESET} ${module}"
            ((i++))
        done
    fi
    echo
    ask_yn _install_module "Install an optional module now?" "n"
    if [[ "$_install_module" == "y" ]]; then
        run_module_wizard
    else
        info "Skipping module installation. Run 'bash matrix-wizard.sh' → 'Install/configure module' any time."
    fi
}

pause_screen() {
    echo
    echo -ne "${CYAN}Press Enter to return to wizard...${RESET}"
    read -r _
}

run_module_wizard() {
    local modules=()
    mapfile -t modules < <(list_available_modules)

    echo
    echo -e "${BOLD}  Available modules${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"

    if [[ ${#modules[@]} -eq 0 ]]; then
        warn "No modules with setup scripts were found in modules/."
        pause_screen
        return
    fi

    local i=1
    for module in "${modules[@]}"; do
        echo -e "  ${CYAN}${i})${RESET} ${module}"
        ((i++))
    done
    echo -e "  ${CYAN}${i})${RESET} Enter module name manually"
    echo -e "  ${CYAN}b)${RESET} Back"
    echo -e "  ${CYAN}q)${RESET} Quit"

    echo
    echo -ne "${BOLD}  Select module${RESET}: "
    local choice
    read -r choice
    choice="${choice,,}"

    if [[ "$choice" == "b" ]]; then
        return
    fi

    if [[ "$choice" == "q" ]]; then
        success "Exiting wizard."
        exit 0
    fi

    if [[ "$choice" =~ ^[0-9]+$ ]]; then
        if (( choice >= 1 && choice <= ${#modules[@]} )); then
            run_module_setup "${modules[$((choice - 1))]}"
            pause_screen
            return
        fi

        if (( choice == ${#modules[@]} + 1 )); then
            local module_name
            ask module_name "Module name" ""
            if [[ -z "$module_name" ]]; then
                warn "Module name is required."
                pause_screen
                return
            fi
            run_module_setup "$module_name"
            pause_screen
            return
        fi
    fi

    warn "Invalid selection."
    pause_screen
}

run_create_admin_wizard() {
    if [[ ! -f "$DEPLOY_ENV" ]]; then
        die "No .env found at ${DEPLOY_ENV}. Run first setup first."
    fi

    set -o allexport
    # shellcheck disable=SC1090
    source "$DEPLOY_ENV"
    set +o allexport

    if [[ -z "${MATRIX_DOMAIN:-}" || -z "${REGISTRATION_SHARED_SECRET:-}" ]]; then
        die ".env is missing MATRIX_DOMAIN and/or REGISTRATION_SHARED_SECRET."
    fi

    local admin_username
    ask admin_username "Admin username" "admin"
    while [[ -z "$admin_username" ]]; do
        warn "Admin username is required."
        ask admin_username "Admin username" "admin"
    done

    local pw_a pw_b
    while true; do
        ask_secret pw_a "Admin password"
        if [[ ${#pw_a} -lt 10 ]]; then
            warn "Password must be at least 10 characters."
            continue
        fi
        ask_secret pw_b "Confirm admin password"
        if [[ "$pw_a" != "$pw_b" ]]; then
            warn "Passwords do not match. Try again."
            continue
        fi
        break
    done

    echo
    info "Creating admin user '@${admin_username}:${SERVER_NAME:-${MATRIX_DOMAIN}}'..."
    bash "${SCRIPT_DIR}/scripts/create-admin.sh" \
        "https://${MATRIX_DOMAIN}" \
        "${REGISTRATION_SHARED_SECRET}" \
        "${admin_username}" \
        "${pw_a}"
    pause_screen
}

run_logs_wizard() {
    echo
    echo -e "${BOLD}  Tail logs${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  ${CYAN}1)${RESET} Synapse"
    echo -e "  ${CYAN}2)${RESET} Caddy"
    echo -e "  ${CYAN}3)${RESET} Element"
    echo -e "  ${CYAN}4)${RESET} PostgreSQL"
    echo -e "  ${CYAN}5)${RESET} Redis"
    echo -e "  ${CYAN}6)${RESET} LiveKit"
    echo -e "  ${CYAN}7)${RESET} Coturn"
    echo -e "  ${CYAN}8)${RESET} Hookshot"
    echo -e "  ${CYAN}9)${RESET} WhatsApp bridge"
    echo -e "  ${CYAN}10)${RESET} Slack bridge"
    echo -e "  ${CYAN}b)${RESET} Back"
    echo -e "  ${CYAN}q)${RESET} Quit"

    echo
    echo -ne "${BOLD}  Select service${RESET}: "
    local choice container
    read -r choice
    choice="${choice,,}"

    case "$choice" in
        1) container="matrix_synapse" ;;
        2) container="caddy" ;;
        3) container="matrix_element" ;;
        4) container="matrix_postgres" ;;
        5) container="matrix_redis" ;;
        6) container="matrix_livekit" ;;
        7) container="matrix_coturn" ;;
        8) container="matrix-hookshot" ;;
        9) container="mautrix-whatsapp" ;;
        10) container="mautrix-slack" ;;
        b) return ;;
        q)
            success "Exiting wizard."
            exit 0
            ;;
        *)
            warn "Invalid selection."
            pause_screen
            return
            ;;
    esac

    if ! docker inspect "$container" &>/dev/null; then
        warn "Container '$container' not found."
        pause_screen
        return
    fi

    echo
    info "Streaming logs for ${container}. Press Ctrl+C to stop."
    docker logs -f "$container" || true
    pause_screen
}

print_wizard_menu() {
    print_banner
    echo -e "${BOLD}  Wizard actions${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  ${CYAN}1)${RESET} First setup (full wizard)"
    echo -e "  ${CYAN}2)${RESET} Install/configure module"
    echo -e "  ${CYAN}3)${RESET} Create Matrix user"
    echo -e "  ${CYAN}4)${RESET} Create admin user"
    echo -e "  ${CYAN}5)${RESET} Start services"
    echo -e "  ${CYAN}6)${RESET} Stop services"
    echo -e "  ${CYAN}7)${RESET} Update images + restart"
    echo -e "  ${CYAN}8)${RESET} Show running containers"
    echo -e "  ${CYAN}9)${RESET} Tail service logs"
    echo -e "  ${CYAN}q)${RESET} Exit"
    echo
}

run_wizard_hub() {
    while true; do
        print_wizard_menu
        echo -ne "${BOLD}  Select an action${RESET}: "
        local choice
        read -r choice
        choice="${choice,,}"

        case "$choice" in
            1)
                run_full_setup
                pause_screen
                ;;
            2)
                run_module_wizard
                ;;
            3)
                bash "${SCRIPT_DIR}/scripts/create-user.sh"
                pause_screen
                ;;
            4)
                run_create_admin_wizard
                ;;
            5)
                bash "${SCRIPT_DIR}/start.sh"
                pause_screen
                ;;
            6)
                bash "${SCRIPT_DIR}/stop.sh"
                pause_screen
                ;;
            7)
                bash "${SCRIPT_DIR}/update.sh"
                pause_screen
                ;;
            8)
                docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
                pause_screen
                ;;
            9)
                run_logs_wizard
                ;;
            q)
                success "Exiting wizard."
                return
                ;;
            *)
                warn "Invalid selection."
                pause_screen
                ;;
        esac
    done
}

main() {
    case "${1:-}" in
        --module)
            shift
            local module="${1:?Usage: setup.sh --module <module-name>}"
            run_module_setup "$module"
            ;;
        --full-setup)
            run_full_setup
            ;;
        "" )
            run_wizard_hub
            ;;
        -h|--help)
            cat <<EOF
Usage:
  bash setup.sh                 # Interactive wizard hub
  bash setup.sh --full-setup    # Run first-time setup flow directly
  bash setup.sh --module NAME   # Run setup for one module
EOF
            ;;
        *)
            die "Unknown argument: ${1}. Use --help for usage."
            ;;
    esac
}

main "$@"
