# scripts/setup/summary.sh
# Final summary output.

print_summary() {
    echo
    echo -e "${GREEN}${BOLD}"
    cat << 'EOF'
  ┌─────────────────────────────────────────────────────┐
  │                                                     │
  │              Setup complete!                        │
  │                                                     │
  └─────────────────────────────────────────────────────┘
EOF
    echo -e "${RESET}"
    echo -e "  Your Matrix server is live. Here's where everything lives:\n"
    echo -e "  ${BOLD}Matrix homeserver${RESET}  https://${MATRIX_DOMAIN}/"
    if [[ "${INSTALL_ELEMENT}" == "true" ]]; then
        echo -e "  ${BOLD}Element client${RESET}     https://${ELEMENT_DOMAIN}/"
    fi
    echo -e "  ${BOLD}LiveKit SFU${RESET}        https://${LIVEKIT_DOMAIN}/"
    echo -e "  ${BOLD}TURN server${RESET}        ${MATRIX_DOMAIN}:3478 (UDP/TCP) and :5349 (TLS)"
    echo -e "  ${BOLD}Synapse admin${RESET}      https://${MATRIX_DOMAIN}/_synapse/admin/v1/"
    echo
    echo -e "  ${BOLD}Your admin ID${RESET}      @${ADMIN_USERNAME}:${SERVER_NAME}"
    echo
    echo -e "  ${BOLD}Useful commands${RESET}"
    echo -e "    See logs (Synapse):     ${CYAN}docker logs -f matrix_synapse${RESET}"
    echo -e "    See logs (Redis):       ${CYAN}docker logs -f matrix_redis${RESET}"
    echo -e "    See logs (LiveKit):     ${CYAN}docker logs -f matrix_livekit${RESET}"
    echo -e "    See logs (coturn):      ${CYAN}docker logs -f matrix_coturn${RESET}"
    echo -e "    See logs (Caddy):       ${CYAN}docker logs -f caddy${RESET}"
    echo -e "    Stop all services:      ${CYAN}bash stop.sh${RESET}"
    echo -e "    Restart all services:   ${CYAN}bash start.sh${RESET}"
    echo
    echo -e "  ${BOLD}Add a bridge or bot later${RESET}"
    echo -e "    ${CYAN}bash setup.sh --module <module-name>${RESET}"
    echo
    echo -e "  Secrets are stored in ${CYAN}.env${RESET} — keep it private."
    echo
}
