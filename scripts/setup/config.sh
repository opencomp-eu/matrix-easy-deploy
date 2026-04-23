# scripts/setup/config.sh
# Interactive configuration prompts.

gather_config() {
    echo
    echo -e "${BOLD}  Configuration${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  Press Enter to accept a ${CYAN}[default]${RESET}.\n"

    ask MATRIX_DOMAIN \
        "Matrix homeserver domain  (e.g. matrix.example.com)" \
        ""
    while [[ -z "$MATRIX_DOMAIN" ]]; do
        warn "Matrix domain is required."
        ask MATRIX_DOMAIN "Matrix homeserver domain" ""
    done

    local _suggested_server_name
    _suggested_server_name="$(extract_base_domain "$MATRIX_DOMAIN")"
    ask SERVER_NAME \
        "Matrix server name (used in user IDs: @user:SERVER_NAME)" \
        "$_suggested_server_name"

    echo
    echo -e "  ${BOLD}Admin account${RESET}"
    ask ADMIN_USERNAME "Admin username" "admin"
    while [[ -z "$ADMIN_USERNAME" ]]; do
        warn "Admin username is required."
        ask ADMIN_USERNAME "Admin username" "admin"
    done

    local pw_a pw_b
    while true; do
        ask_secret pw_a "Admin password"
        if [[ ${#pw_a} -lt 10 ]]; then
            warn "Password must be at least 10 characters. Try again."
            continue
        fi
        ask_secret pw_b "Confirm admin password"
        if [[ "$pw_a" != "$pw_b" ]]; then
            warn "Passwords do not match. Try again."
        else
            break
        fi
    done
    ADMIN_PASSWORD="$pw_a"

    echo
    echo -e "  ${BOLD}Optional features${RESET}"

    ask_yn ENABLE_REGISTRATION_INPUT \
        "Allow public user registration?" \
        "n"
    if [[ "$ENABLE_REGISTRATION_INPUT" == "y" ]]; then
        ENABLE_REGISTRATION="true"
    else
        ENABLE_REGISTRATION="false"
    fi

    ask_yn ENABLE_FEDERATION_INPUT \
        "Enable federation with other Matrix servers?" \
        "y"
    if [[ "$ENABLE_FEDERATION_INPUT" == "y" ]]; then
        FEDERATION_WHITELIST="~"
        ALLOW_PUBLIC_ROOMS_FEDERATION="true"
    else
        FEDERATION_WHITELIST="[]"
        ALLOW_PUBLIC_ROOMS_FEDERATION="false"
    fi

    gather_sso_config

    ask_yn INSTALL_ELEMENT_INPUT \
        "Install Element web client? (skip if you already have a client)" \
        "y"
    if [[ "$INSTALL_ELEMENT_INPUT" == "y" ]]; then
        INSTALL_ELEMENT="true"
        local _suggested_element_domain
        _suggested_element_domain="element.$(extract_base_domain "$MATRIX_DOMAIN")"
        ask ELEMENT_DOMAIN \
            "Element domain  (e.g. element.example.com)" \
            "$_suggested_element_domain"
        while [[ -z "$ELEMENT_DOMAIN" ]]; do
            warn "Element domain is required when installing Element."
            ask ELEMENT_DOMAIN "Element domain" "$_suggested_element_domain"
        done
    else
        INSTALL_ELEMENT="false"
        ELEMENT_DOMAIN=""
    fi

    echo
    echo -e "  ${BOLD}Calls (TURN + LiveKit SFU)${RESET}"
    local _suggested_livekit_domain
    _suggested_livekit_domain="livekit.$(extract_base_domain "$MATRIX_DOMAIN")"
    ask LIVEKIT_DOMAIN \
        "LiveKit domain  (e.g. livekit.example.com)" \
        "$_suggested_livekit_domain"
    while [[ -z "$LIVEKIT_DOMAIN" ]]; do
        warn "LiveKit domain is required."
        ask LIVEKIT_DOMAIN "LiveKit domain" "$_suggested_livekit_domain"
    done

    echo
    echo -e "${BOLD}  Configuration summary${RESET}"
    echo -e "  ─────────────────────────────────────────────────────"
    echo -e "  Matrix domain   : ${CYAN}${MATRIX_DOMAIN}${RESET}"
    echo -e "  Server name     : ${CYAN}${SERVER_NAME}${RESET}  (IDs look like @${ADMIN_USERNAME}:${SERVER_NAME})"
    echo -e "  Admin user      : ${CYAN}${ADMIN_USERNAME}${RESET}"
    echo -e "  Public reg.     : ${CYAN}${ENABLE_REGISTRATION}${RESET}"
    echo -e "  Federation      : ${CYAN}${ENABLE_FEDERATION_INPUT}${RESET}"
    if [[ "$ENABLE_SSO" == "true" ]]; then
        echo -e "  SSO (OIDC)      : ${CYAN}enabled${RESET} (${OIDC_PROVIDER_COUNT} provider(s))"
        echo -e "  Providers       : ${CYAN}${OIDC_PROVIDER_NAMES}${RESET}"
    else
        echo -e "  SSO (OIDC)      : ${CYAN}disabled${RESET}"
    fi
    if [[ "$INSTALL_ELEMENT" == "true" ]]; then
        echo -e "  Element client  : ${CYAN}${ELEMENT_DOMAIN}${RESET}"
    else
        echo -e "  Element client  : ${CYAN}not installed${RESET}"
    fi
    echo -e "  LiveKit (calls) : ${CYAN}${LIVEKIT_DOMAIN}${RESET}"
    echo
    echo -e "  ${YELLOW}DNS check:${RESET} make sure these A records point to this server before proceeding:"
    echo -e "    ${CYAN}${MATRIX_DOMAIN}${RESET}  →  <this server's IP>"
    if [[ "$SERVER_NAME" != "$MATRIX_DOMAIN" ]]; then
        echo -e "    ${CYAN}${SERVER_NAME}${RESET}  →  <this server's IP>  ${YELLOW}(required for federation delegation)${RESET}"
    fi
    if [[ "$INSTALL_ELEMENT" == "true" ]]; then
        echo -e "    ${CYAN}${ELEMENT_DOMAIN}${RESET}  →  <this server's IP>"
    fi
    echo -e "    ${CYAN}${LIVEKIT_DOMAIN}${RESET}  →  <this server's IP>"
    echo

    ask_yn _confirm "Does this look right? Proceed?" "y"
    if [[ "$_confirm" != "y" ]]; then
        warn "Restarting configuration…"
        echo
        gather_config
    fi
}
