#!/usr/bin/env bash
# stop.sh — tear down all matrix-easy-deploy services (data is preserved)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"
IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"

# Load .env so Docker Compose can substitute variables (e.g. POSTGRES_PASSWORD)
MAS_ENABLED="false"
HOOKSHOT_ENABLED="false"
WHATSAPP_BRIDGE_ENABLED="false"
SLACK_BRIDGE_ENABLED="false"
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    load_deploy_env "${SCRIPT_DIR}/.env"
fi

load_runtime_desired_state "${SCRIPT_DIR}"

# Stop MAS when it was previously installed
if [[ -f "${SCRIPT_DIR}/modules/mas/config.yaml" ]]; then
    info "Stopping Matrix Authentication Service (MAS)…"
    (cd "${SCRIPT_DIR}/modules/mas" && "${DOCKER_COMPOSE[@]}" down)
fi

build_core_compose_stop_profiles

# Stop WhatsApp bridge when its module was previously installed
if [[ -f "${SCRIPT_DIR}/modules/whatsapp-bridge/whatsapp/config.yaml" ]]; then
    info "Stopping WhatsApp bridge…"
    (cd "${SCRIPT_DIR}/modules/whatsapp-bridge" && "${DOCKER_COMPOSE[@]}" down)
fi

# Stop Slack bridge when its module was previously installed
if [[ -f "${SCRIPT_DIR}/modules/slack-bridge/slack/config.yaml" ]]; then
    info "Stopping Slack bridge…"
    (cd "${SCRIPT_DIR}/modules/slack-bridge" && "${DOCKER_COMPOSE[@]}" down)
fi

# Stop Hookshot when its module was previously installed
if [[ -f "${SCRIPT_DIR}/modules/hookshot/hookshot/config.yml" ]]; then
    info "Stopping Hookshot…"
    (cd "${SCRIPT_DIR}/modules/hookshot" && "${DOCKER_COMPOSE[@]}" down)
fi

info "Stopping calls services (coturn + LiveKit)…"
(cd "${SCRIPT_DIR}/modules/calls" && "${DOCKER_COMPOSE[@]}" down)

info "Stopping core services…"
(cd "${SCRIPT_DIR}/modules/core" && "${DOCKER_COMPOSE[@]}" "${CORE_COMPOSE_PROFILES[@]}" down --remove-orphans)

info "Stopping Caddy…"
(cd "${SCRIPT_DIR}/caddy" && "${DOCKER_COMPOSE[@]}" down)

success "All services stopped. Your data is intact in Docker volumes."
