#!/usr/bin/env bash
# stop.sh — tear down all matrix-easy-deploy services (data is preserved)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"
IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"

# Load .env so Docker Compose can substitute variables (e.g. POSTGRES_PASSWORD)
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

# Stop WhatsApp bridge if it was installed as a module
if [[ -f "${SCRIPT_DIR}/modules/whatsapp-bridge/whatsapp/config.yaml" ]]; then
    info "Stopping WhatsApp bridge…"
    (cd "${SCRIPT_DIR}/modules/whatsapp-bridge" && "${DOCKER_COMPOSE[@]}" down)
fi

# Stop Slack bridge if it was installed as a module
if [[ -f "${SCRIPT_DIR}/modules/slack-bridge/slack/config.yaml" ]]; then
    info "Stopping Slack bridge…"
    (cd "${SCRIPT_DIR}/modules/slack-bridge" && "${DOCKER_COMPOSE[@]}" down)
fi

# Stop Hookshot if it was installed as a module
if [[ -n "${HOOKSHOT_DOMAIN:-}" && -f "${SCRIPT_DIR}/modules/hookshot/hookshot/config.yml" ]]; then
    info "Stopping Hookshot…"
    (cd "${SCRIPT_DIR}/modules/hookshot" && "${DOCKER_COMPOSE[@]}" down)
fi

info "Stopping calls services (coturn + LiveKit)…"
(cd "${SCRIPT_DIR}/modules/calls" && "${DOCKER_COMPOSE[@]}" down)

info "Stopping core services…"
(cd "${SCRIPT_DIR}/modules/core" && "${DOCKER_COMPOSE[@]}" $_element_profile down --remove-orphans)

info "Stopping Caddy…"
(cd "${SCRIPT_DIR}/caddy" && "${DOCKER_COMPOSE[@]}" down)

success "All services stopped. Your data is intact in Docker volumes."
