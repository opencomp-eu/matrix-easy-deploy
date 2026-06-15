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

# shellcheck source=scripts/setup/banner.sh
source "${SCRIPT_DIR}/scripts/setup/banner.sh"
# shellcheck source=scripts/setup/dependencies.sh
source "${SCRIPT_DIR}/scripts/setup/dependencies.sh"
# shellcheck source=scripts/setup/runtime.sh
source "${SCRIPT_DIR}/scripts/setup/runtime.sh"
# shellcheck source=scripts/setup/summary.sh
source "${SCRIPT_DIR}/scripts/setup/summary.sh"
# shellcheck source=scripts/setup/modules.sh
source "${SCRIPT_DIR}/scripts/setup/modules.sh"

IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"
DEPLOY_ENV="${SCRIPT_DIR}/.env"
DEPLOY_YAML="${SCRIPT_DIR}/deploy.yaml"

edit_deploy_config() {
    echo
    echo -e "${BOLD}  Configuration${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  Press Enter to accept a ${CYAN}[default]${RESET}.\n"

    # Load existing config defaults
    local config_matrix_domain="matrix.example.com"
    local config_server_name="example.com"
    local config_admin_username="admin"
    local config_registration_default="n"
    local config_federation_default="y"
    local config_element_default="y"
    local config_element_domain=""
    local config_calls_default="y"
    local config_livekit_domain=""
    local config_server_implementation="synapse"

    if [[ -f "$DEPLOY_YAML" ]]; then
        info "Loading existing configuration from deploy.yaml"
        eval "$(python3 "${SCRIPT_DIR}/scripts/config_edit.py" --deploy-yaml "$DEPLOY_YAML" --print-wizard-defaults)"
    fi

    ask MATRIX_DOMAIN \
        "Matrix homeserver domain  (e.g. matrix.example.com)" \
        "$config_matrix_domain"
    while [[ -z "$MATRIX_DOMAIN" ]]; do
        warn "Matrix domain is required."
        ask MATRIX_DOMAIN "Matrix homeserver domain" "$config_matrix_domain"
    done

    local _suggested_server_name
    _suggested_server_name="$(extract_base_domain "$MATRIX_DOMAIN")"
    ask SERVER_NAME \
        "Matrix server name (used in user IDs: @user:SERVER_NAME)" \
        "${config_server_name:-$_suggested_server_name}"

    ask ADMIN_USERNAME "Admin username" "$config_admin_username"
    while [[ -z "$ADMIN_USERNAME" ]]; do
        warn "Admin username is required."
        ask ADMIN_USERNAME "Admin username" "$config_admin_username"
    done

    echo
    echo -e "  ${BOLD}Homeserver software${RESET}"
    echo -e "  ${CYAN}synapse${RESET} — mature Matrix reference server (default)"
    echo -e "  ${CYAN}tuwunel${RESET} — Rust homeserver (lower resource use; no Synapse migration yet)"
    ask SERVER_IMPLEMENTATION \
        "Homeserver implementation (synapse or tuwunel)" \
        "$config_server_implementation"
    SERVER_IMPLEMENTATION="${SERVER_IMPLEMENTATION,,}"
  case "$SERVER_IMPLEMENTATION" in
        synapse|tuwunel) ;;
        *)
            warn "Invalid choice '${SERVER_IMPLEMENTATION}'. Using synapse."
            SERVER_IMPLEMENTATION="synapse"
            ;;
    esac

    echo
    echo -e "  ${BOLD}Optional features${RESET}"

    ask_yn ENABLE_REGISTRATION_INPUT \
        "Allow public user registration?" \
        "$config_registration_default"
    ENABLE_REGISTRATION="$([ "$ENABLE_REGISTRATION_INPUT" == "y" ] && echo "true" || echo "false")"

    ask_yn ENABLE_FEDERATION_INPUT \
        "Enable federation with other Matrix servers?" \
        "$config_federation_default"
    ENABLE_FEDERATION="$([ "$ENABLE_FEDERATION_INPUT" == "y" ] && echo "true" || echo "false")"

    # SSO placeholder
    ENABLE_SSO="false"

    ask_yn INSTALL_ELEMENT_INPUT \
        "Install Element web client? (skip if you already have a client)" \
        "$config_element_default"
    INSTALL_ELEMENT="$([ "$INSTALL_ELEMENT_INPUT" == "y" ] && echo "true" || echo "false")"
    if [[ "$INSTALL_ELEMENT" == "true" ]]; then
        local _suggested_element_domain
        _suggested_element_domain="element.$(extract_base_domain "$MATRIX_DOMAIN")"
        ask ELEMENT_DOMAIN \
            "Element domain  (e.g. element.example.com)" \
            "${config_element_domain:-$_suggested_element_domain}"
        while [[ -z "$ELEMENT_DOMAIN" ]]; do
            warn "Element domain is required when installing Element."
            ask ELEMENT_DOMAIN "Element domain" "${config_element_domain:-$_suggested_element_domain}"
        done
    else
        ELEMENT_DOMAIN=""
    fi

    echo
    echo -e "  ${BOLD}Calls (TURN + LiveKit SFU)${RESET}"
    ask_yn ENABLE_CALLS_INPUT \
        "Enable TURN + LiveKit calls services?" \
        "$config_calls_default"
    ENABLE_CALLS="$([ "$ENABLE_CALLS_INPUT" == "y" ] && echo "true" || echo "false")"
    if [[ "$ENABLE_CALLS" == "true" ]]; then
        local _suggested_livekit_domain
        _suggested_livekit_domain="livekit.$(extract_base_domain "$MATRIX_DOMAIN")"
        ask LIVEKIT_DOMAIN \
            "LiveKit domain  (e.g. livekit.example.com)" \
            "${config_livekit_domain:-$_suggested_livekit_domain}"
        while [[ -z "$LIVEKIT_DOMAIN" ]]; do
            warn "LiveKit domain is required when calls are enabled."
            ask LIVEKIT_DOMAIN "LiveKit domain" "${config_livekit_domain:-$_suggested_livekit_domain}"
        done
    else
        LIVEKIT_DOMAIN=""
    fi

    echo
    echo -e "${BOLD}  Configuration summary${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  Matrix domain   : ${CYAN}${MATRIX_DOMAIN}${RESET}"
    echo -e "  Server name     : ${CYAN}${SERVER_NAME}${RESET}  (IDs look like @${ADMIN_USERNAME}:${SERVER_NAME})"
    echo -e "  Admin user      : ${CYAN}${ADMIN_USERNAME}${RESET}"
    echo -e "  Homeserver      : ${CYAN}${SERVER_IMPLEMENTATION}${RESET}"
    echo -e "  Public reg.     : ${CYAN}${ENABLE_REGISTRATION}${RESET}"
    echo -e "  Federation      : ${CYAN}${ENABLE_FEDERATION_INPUT}${RESET}"
    echo -e "  SSO (OIDC)      : ${CYAN}disabled${RESET}"
    if [[ "$INSTALL_ELEMENT" == "true" ]]; then
        echo -e "  Element client  : ${CYAN}${ELEMENT_DOMAIN}${RESET}"
    else
        echo -e "  Element client  : ${CYAN}not installed${RESET}"
    fi
    if [[ "$ENABLE_CALLS" == "true" ]]; then
        echo -e "  LiveKit (calls) : ${CYAN}${LIVEKIT_DOMAIN}${RESET}"
    else
        echo -e "  LiveKit (calls) : ${CYAN}disabled${RESET}"
    fi
    echo
    echo -e "  ${YELLOW}DNS check:${RESET} make sure these A records point to this server before proceeding:"
    echo -e "    ${CYAN}${MATRIX_DOMAIN}${RESET}  →  <this server's IP>"
    if [[ "$SERVER_NAME" != "$MATRIX_DOMAIN" ]]; then
        echo -e "    ${CYAN}${SERVER_NAME}${RESET}  →  <this server's IP>  ${YELLOW}(required for federation delegation)${RESET}"
    fi
    if [[ "$INSTALL_ELEMENT" == "true" ]]; then
        echo -e "    ${CYAN}${ELEMENT_DOMAIN}${RESET}  →  <this server's IP>"
    fi
    if [[ "$ENABLE_CALLS" == "true" ]]; then
        echo -e "    ${CYAN}${LIVEKIT_DOMAIN}${RESET}  →  <this server's IP>"
    fi
    echo

    ask_yn _confirm "Does this look right? Proceed?" "y"
    if [[ "$_confirm" != "y" ]]; then
        warn "Restarting configuration…"
        echo
        edit_deploy_config
        return $?
    fi

    python3 "${SCRIPT_DIR}/scripts/config_edit.py" \
        --deploy-yaml "$DEPLOY_YAML" \
        --set-core \
        --matrix-domain "$MATRIX_DOMAIN" \
        --server-name "$SERVER_NAME" \
        --admin-username "$ADMIN_USERNAME" \
        --server-implementation "$SERVER_IMPLEMENTATION" \
        --registration-enabled "$ENABLE_REGISTRATION" \
        --federation-enabled "$ENABLE_FEDERATION" \
        --install-element "$INSTALL_ELEMENT" \
        --element-domain "$ELEMENT_DOMAIN" \
        --calls-enabled "$ENABLE_CALLS" \
        --livekit-domain "$LIVEKIT_DOMAIN"
    success "Configuration saved to deploy.yaml"

    echo
    ask_yn _proceed_deploy "Proceed to deployment?" "y"
    if [[ "$_proceed_deploy" != "y" ]]; then
        info "Stopping after configuration step."
        info "You can deploy anytime with 'bash apply.sh' followed by 'bash start.sh'."
        return 1
    fi

    return 0
}

run_full_setup() {
    print_banner
    check_dependencies

    echo
    echo -e "${BOLD}  Step 1 of 5 — Configuration${RESET}"
    if ! edit_deploy_config; then
        return
    fi

    echo
    echo -e "${BOLD}  Step 2 of 5 — Applying configuration${RESET}"
    bash "${SCRIPT_DIR}/apply.sh"

    # Load the generated .env for runtime
    if [[ -f "$DEPLOY_ENV" ]]; then
        set -o allexport
        # shellcheck disable=SC1090
        source "$DEPLOY_ENV"
        set +o allexport
    fi

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
            local selected_module
            selected_module="${modules[$((choice - 1))]}"
            info "Marking module '${selected_module}' as enabled in deploy.yaml…"
            python3 "${SCRIPT_DIR}/scripts/config_edit.py" \
                --deploy-yaml "${DEPLOY_YAML}" \
                --enable-module "${selected_module}"
            info "Applying updated configuration…"
            bash "${SCRIPT_DIR}/apply.sh"
            run_module_setup "$selected_module"
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
            info "Marking module '${module_name}' as enabled in deploy.yaml…"
            python3 "${SCRIPT_DIR}/scripts/config_edit.py" \
                --deploy-yaml "${DEPLOY_YAML}" \
                --enable-module "${module_name}"
            info "Applying updated configuration…"
            bash "${SCRIPT_DIR}/apply.sh"
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
    bash "${SCRIPT_DIR}/scripts/create-account.sh" \
        --base-url "https://${MATRIX_DOMAIN}" \
        --shared-secret "${REGISTRATION_SHARED_SECRET}" \
        --username "${admin_username}" \
        --password "${pw_a}" \
        --admin \
        --yes
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
        1)
            if [[ -f "$DEPLOY_ENV" ]]; then
                container="$(sed -n 's/^HOMESERVER_CONTAINER=//p' "$DEPLOY_ENV" | head -n1)"
            fi
            container="${container:-matrix_synapse}"
            ;;
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

run_backup_wizard() {
    echo
    bash "${SCRIPT_DIR}/backup.sh"
}

run_backup_config_wizard() {
    echo
    echo -e "${BOLD}  Backup configuration${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"

    local backup_enabled="false"
    local backup_repository_path="/var/backups/med-kit"
    local backup_schedule_enabled="false"
    local backup_schedule_calendar="*-*-* 03:00:00"
    local backup_schedule_persistent="true"
    local backup_keep_daily="7"
    local backup_keep_weekly="4"
    local backup_keep_monthly="6"
    local backup_keep_yearly="0"

    if [[ -f "$DEPLOY_YAML" ]]; then
        eval "$(python3 "${SCRIPT_DIR}/scripts/config_edit.py" --deploy-yaml "$DEPLOY_YAML" --print-backup-defaults)"
    fi

    ask_yn _backup_enabled "Enable backups?" "$( [[ "$backup_enabled" == "true" ]] && echo y || echo n )"
    backup_enabled="$([ "$_backup_enabled" == "y" ] && echo "true" || echo "false")"

    ask backup_repository_path "Backup repository path" "$backup_repository_path"

    ask_yn _schedule_enabled "Enable automatic backup timer?" "$( [[ "$backup_schedule_enabled" == "true" ]] && echo y || echo n )"
    backup_schedule_enabled="$([ "$_schedule_enabled" == "y" ] && echo "true" || echo "false")"

    if [[ "$backup_schedule_enabled" == "true" ]]; then
        ask backup_schedule_calendar "systemd OnCalendar schedule" "$backup_schedule_calendar"
        while [[ -z "$backup_schedule_calendar" ]]; do
            warn "A schedule is required when automatic backups are enabled."
            ask backup_schedule_calendar "systemd OnCalendar schedule" "$backup_schedule_calendar"
        done

        ask_yn _schedule_persistent "Run missed backups after reboot?" "$( [[ "$backup_schedule_persistent" == "true" ]] && echo y || echo n )"
        backup_schedule_persistent="$([ "$_schedule_persistent" == "y" ] && echo "true" || echo "false")"
    fi

    ask backup_keep_daily "Keep daily backups" "$backup_keep_daily"
    ask backup_keep_weekly "Keep weekly backups" "$backup_keep_weekly"
    ask backup_keep_monthly "Keep monthly backups" "$backup_keep_monthly"
    ask backup_keep_yearly "Keep yearly backups" "$backup_keep_yearly"

    python3 "${SCRIPT_DIR}/scripts/config_edit.py" \
        --deploy-yaml "$DEPLOY_YAML" \
        --set-backup-config \
        --backup-enabled "$backup_enabled" \
        --backup-repository-type local \
        --backup-repository-path "$backup_repository_path" \
        --backup-schedule-enabled "$backup_schedule_enabled" \
        --backup-schedule-calendar "$backup_schedule_calendar" \
        --backup-schedule-persistent "$backup_schedule_persistent" \
        --backup-keep-daily "$backup_keep_daily" \
        --backup-keep-weekly "$backup_keep_weekly" \
        --backup-keep-monthly "$backup_keep_monthly" \
        --backup-keep-yearly "$backup_keep_yearly"

    info "Applying updated backup configuration..."
    bash "${SCRIPT_DIR}/apply.sh"
}

run_export_backup_wizard() {
    echo
    local export_path
    local default_path="${HOME}/med-kit-backup-$(date -u +%Y-%m-%dT%H%M%SZ).tar.gz"
    ask export_path "Portable archive path" "$default_path"
    if [[ -z "$export_path" ]]; then
        warn "Export path is required."
        return
    fi

    ask_yn _encrypt "Encrypt the portable archive?" "y"
    local encrypt_args=()
    if [[ "$_encrypt" == "y" ]]; then
        encrypt_args+=(--encrypt)
        case "$export_path" in
            *.tar.gz|*.tgz) export_path="${export_path}.age" ;;
            *.age|*.enc) ;;
            *) export_path="${export_path}.age" ;;
        esac
    fi

    ask_yn _export_only "Export only (skip Borg repository update)?" "n"
    if [[ "$_export_only" == "y" ]]; then
        bash "${SCRIPT_DIR}/backup.sh" --export-only "$export_path" --export "$export_path" "${encrypt_args[@]}"
    else
        bash "${SCRIPT_DIR}/backup.sh" --export "$export_path" "${encrypt_args[@]}"
    fi
}

run_restore_portable_wizard() {
    echo
    local portable_path
    ask portable_path "Portable archive path (.tar.gz or .tar.gz.age)" ""
    if [[ -z "$portable_path" ]]; then
        warn "Portable archive path is required."
        return
    fi

    local restore_args=(--file "$portable_path")
    case "$portable_path" in
        *.age|*.enc) restore_args+=(--encrypt) ;;
    esac

    ask_yn _keep_stopped "Keep services stopped after restore?" "n"
    if [[ "$_keep_stopped" == "y" ]]; then
        restore_args+=(--keep-stopped)
    fi

    bash "${SCRIPT_DIR}/restore.sh" "${restore_args[@]}"
}

run_restore_wizard() {
    echo
    info "Available backups:"
    if ! bash "${SCRIPT_DIR}/backup.sh" --list; then
        warn "Unable to list backups. Check backup settings in deploy.yaml."
        return
    fi

    local archive_name
    ask archive_name "Archive name to restore" ""
    if [[ -z "$archive_name" ]]; then
        warn "Archive name is required."
        return
    fi

    ask_yn _keep_stopped "Keep services stopped after restore?" "n"
    if [[ "$_keep_stopped" == "y" ]]; then
        bash "${SCRIPT_DIR}/restore.sh" --archive "$archive_name" --keep-stopped
    else
        bash "${SCRIPT_DIR}/restore.sh" --archive "$archive_name"
    fi
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
    echo -e "  ${CYAN}10)${RESET} Uninstall/reset stack"
    echo -e "  ${CYAN}11)${RESET} Create backup"
    echo -e "  ${CYAN}12)${RESET} List backups"
    echo -e "  ${CYAN}13)${RESET} Restore backup (Borg archive)"
    echo -e "  ${CYAN}14)${RESET} Configure backup settings"
    echo -e "  ${CYAN}15)${RESET} Export portable backup"
    echo -e "  ${CYAN}16)${RESET} Restore portable backup"
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
                bash "${SCRIPT_DIR}/scripts/create-account.sh"
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
            10)
                bash "${SCRIPT_DIR}/uninstall.sh"
                pause_screen
                ;;
            11)
                run_backup_wizard
                pause_screen
                ;;
            12)
                bash "${SCRIPT_DIR}/backup.sh" --list
                pause_screen
                ;;
            13)
                run_restore_wizard
                pause_screen
                ;;
            14)
                run_backup_config_wizard
                pause_screen
                ;;
            15)
                run_export_backup_wizard
                pause_screen
                ;;
            16)
                run_restore_portable_wizard
                pause_screen
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
