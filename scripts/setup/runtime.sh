# scripts/setup/runtime.sh
# Docker infrastructure, startup, and admin user bootstrap.

setup_docker() {
    info "Setting up Docker infrastructure…"

    ensure_docker_network "caddy_net"
    ensure_docker_volume "caddy_data"

    local synapse_data_dir="${SCRIPT_DIR}/modules/core/synapse_data"
    if [[ ! -d "$synapse_data_dir" ]]; then
        info "Creating synapse_data directory…"
        mkdir -p "$synapse_data_dir"
        chmod 777 "$synapse_data_dir"
        success "synapse_data directory created."
    fi

    success "Docker infrastructure ready."
}

stop_existing_services_for_first_setup_reset() {
    local stop_script="${SCRIPT_DIR}/stop.sh"

    warn "Existing 'core_postgres_data' volume detected from an earlier setup attempt."
    info "Stopping running services automatically before resetting core database state…"

    if [[ -f "$stop_script" ]]; then
        if ! bash "$stop_script"; then
            warn "Automatic stop reported an issue. Continuing with core shutdown fallback."
        fi
    fi

    local _element_profile=()
    if [[ "${INSTALL_ELEMENT:-true}" == "true" ]]; then
        _element_profile=(--profile element)
    fi

    (cd "${SCRIPT_DIR}/modules/core" && "${DOCKER_COMPOSE[@]}" "${_element_profile[@]}" down --remove-orphans) || true
    (cd "${SCRIPT_DIR}/modules/calls" && "${DOCKER_COMPOSE[@]}" down) || true
    (cd "${SCRIPT_DIR}/caddy" && "${DOCKER_COMPOSE[@]}" down) || true
}

reset_core_postgres_volume_if_present() {
    if ! docker volume inspect core_postgres_data &>/dev/null; then
        return
    fi

    stop_existing_services_for_first_setup_reset
    warn "Removing 'core_postgres_data' so PostgreSQL is re-initialised with the current POSTGRES_PASSWORD."
    docker volume rm core_postgres_data
}

start_services() {
    local _element_label=""
    local _element_profile=()
    if [[ "$INSTALL_ELEMENT" == "true" ]]; then
        _element_label=" + Element"
        _element_profile=(--profile element)
    fi

    reset_core_postgres_volume_if_present

    echo
    info "Starting Caddy…"
    (cd "${SCRIPT_DIR}/caddy" && "${DOCKER_COMPOSE[@]}" up -d --pull always)
    success "Caddy is up."

    echo
    info "Starting core Matrix services (Redis + PostgreSQL + Synapse${_element_label})…"
    info "  Pulling images — this may take a few minutes on first run."

    (
        cd "${SCRIPT_DIR}/modules/core"
        POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
            "${DOCKER_COMPOSE[@]}" "${_element_profile[@]}" up -d --pull always
    )
    success "Core services started."

    echo
    info "Starting calls services (coturn + LiveKit)…"
    (cd "${SCRIPT_DIR}/modules/calls" && "${DOCKER_COMPOSE[@]}" up -d --pull always)
    success "Calls services started."
}

setup_admin() {
    echo
    info "Waiting for Synapse to finish starting…"
    echo -e "  ${CYAN}(This usually takes 20–60 s on first boot while the database is initialised.)${RESET}"

    local attempt=0
    local max=30
    until [[ "$(docker inspect --format='{{.State.Health.Status}}' matrix_synapse 2>/dev/null)" == "healthy" ]]; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max ]]; then
            warn "Synapse hasn't responded after $((max * 5))s."
            warn "It may still be starting. You can create the admin user later:"
            echo
            echo -e "  ${CYAN}bash scripts/create-admin.sh \\\"
            echo -e "      https://${MATRIX_DOMAIN} \\\"
            echo -e "      <registration_shared_secret> \\\"
            echo -e "      ${ADMIN_USERNAME} \\\"
            echo -e "      <your_password>${RESET}"
            echo
            return 0
        fi
        printf "    %ds elapsed…\r" $((attempt * 5))
        sleep 5
    done
    echo
    success "Synapse is responding."

    echo
    local _admin_password=""
    if [[ -n "${ADMIN_PASSWORD:-}" ]]; then
        info "Using ADMIN_PASSWORD from environment for non-interactive admin bootstrap."
        _admin_password="${ADMIN_PASSWORD}"
    else
        local _pw_confirm=""
        info "Set a password for '@${ADMIN_USERNAME}:${SERVER_NAME}'"
        while true; do
            ask_secret _admin_password "Admin password"
            if [[ ${#_admin_password} -lt 10 ]]; then
                warn "Password must be at least 10 characters."
                continue
            fi

            ask_secret _pw_confirm "Confirm admin password"
            if [[ "$_admin_password" != "$_pw_confirm" ]]; then
                warn "Passwords do not match. Try again."
                continue
            fi
            break
        done
        unset _pw_confirm
    fi

    info "Creating admin user '@${ADMIN_USERNAME}:${SERVER_NAME}'…"
    bash "${SCRIPT_DIR}/scripts/create-admin.sh" \
        "https://${MATRIX_DOMAIN}" \
        "${REGISTRATION_SHARED_SECRET}" \
        "${ADMIN_USERNAME}" \
        "${_admin_password}"

    unset _admin_password
}
