#!/usr/bin/env bash
# start.sh — bring up all matrix-easy-deploy services
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"
IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"

info "Starting Caddy…"
(cd "${SCRIPT_DIR}/caddy" && "${DOCKER_COMPOSE[@]}" up -d)

info "Starting core services…"
# Load .env if it exists so POSTGRES_PASSWORD and INSTALL_ELEMENT are available
INSTALL_ELEMENT="true"  # default: assume Element is present if .env is missing
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "${SCRIPT_DIR}/.env"
    set +o allexport
fi

_element_profile=""
if [[ "${INSTALL_ELEMENT:-true}" == "true" ]]; then
    _element_profile="--profile element"
fi

(cd "${SCRIPT_DIR}/modules/core" && "${DOCKER_COMPOSE[@]}" $_element_profile up -d)

info "Starting calls services (coturn + LiveKit)…"
(cd "${SCRIPT_DIR}/modules/calls" && "${DOCKER_COMPOSE[@]}" up -d)

# Start Hookshot if it was installed as a module
if [[ -n "${HOOKSHOT_DOMAIN:-}" && -f "${SCRIPT_DIR}/modules/hookshot/hookshot/config.yml" ]]; then
    info "Starting Hookshot…"
    (cd "${SCRIPT_DIR}/modules/hookshot" && "${DOCKER_COMPOSE[@]}" up -d)
fi

# Start WhatsApp bridge if it was installed as a module
if [[ -f "${SCRIPT_DIR}/modules/whatsapp-bridge/whatsapp/config.yaml" ]]; then
    info "Starting WhatsApp bridge…"
    (cd "${SCRIPT_DIR}/modules/whatsapp-bridge" && "${DOCKER_COMPOSE[@]}" up -d)
fi

# Start Slack bridge if it was installed as a module
if [[ -f "${SCRIPT_DIR}/modules/slack-bridge/slack/config.yaml" ]]; then
    info "Starting Slack bridge…"
    (cd "${SCRIPT_DIR}/modules/slack-bridge" && "${DOCKER_COMPOSE[@]}" up -d)
fi

success "All services started."
