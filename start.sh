#!/usr/bin/env bash
# start.sh — bring up all matrix-easy-deploy services
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"
IFS=' ' read -ra DOCKER_COMPOSE <<< "$(docker_compose_cmd)"

# Ensure external Docker resources exist for direct apply/start workflows.
ensure_docker_network "caddy_net"
ensure_docker_volume "caddy_data"
ensure_homeserver_data_permissions "${SCRIPT_DIR}"

info "Starting Caddy…"
(cd "${SCRIPT_DIR}/caddy" && "${DOCKER_COMPOSE[@]}" up -d)

info "Starting core services…"
# Load .env if it exists so POSTGRES_PASSWORD and INSTALL_ELEMENT are available
INSTALL_ELEMENT="true"  # default: assume Element is present if .env is missing
HOOKSHOT_ENABLED="false"
WHATSAPP_BRIDGE_ENABLED="false"
SLACK_BRIDGE_ENABLED="false"
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "${SCRIPT_DIR}/.env"
    set +o allexport
fi

load_runtime_desired_state "${SCRIPT_DIR}"

_homeserver_profile="${HOMESERVER_COMPOSE_PROFILE:-synapse}"
_core_profiles=(--profile "${_homeserver_profile}")
if [[ "${INSTALL_ELEMENT:-true}" == "true" ]]; then
    _core_profiles+=(--profile element)
fi
(cd "${SCRIPT_DIR}/modules/core" && "${DOCKER_COMPOSE[@]}" "${_core_profiles[@]}" up -d)

info "Starting calls services (coturn + LiveKit)…"
(cd "${SCRIPT_DIR}/modules/calls" && "${DOCKER_COMPOSE[@]}" up -d)

# Start Hookshot if it was installed as a module
if [[ "${HOOKSHOT_ENABLED:-false}" == "true" && -f "${SCRIPT_DIR}/modules/hookshot/hookshot/config.yml" ]]; then
    info "Starting Hookshot…"
    (cd "${SCRIPT_DIR}/modules/hookshot" && "${DOCKER_COMPOSE[@]}" up -d)
elif [[ "${HOOKSHOT_ENABLED:-false}" == "true" ]]; then
    warn "Hookshot is enabled in deploy.yaml but config file is missing. Run 'bash apply.sh' and module setup first."
fi

# Start WhatsApp bridge if it was installed as a module
if [[ "${WHATSAPP_BRIDGE_ENABLED:-false}" == "true" && -f "${SCRIPT_DIR}/modules/whatsapp-bridge/whatsapp/config.yaml" ]]; then
    info "Starting WhatsApp bridge…"
    (cd "${SCRIPT_DIR}/modules/whatsapp-bridge" && "${DOCKER_COMPOSE[@]}" up -d)
elif [[ "${WHATSAPP_BRIDGE_ENABLED:-false}" == "true" ]]; then
    warn "WhatsApp bridge is enabled in deploy.yaml but config file is missing. Run module setup first."
fi

# Start Slack bridge if it was installed as a module
if [[ "${SLACK_BRIDGE_ENABLED:-false}" == "true" && -f "${SCRIPT_DIR}/modules/slack-bridge/slack/config.yaml" ]]; then
    info "Starting Slack bridge…"
    (cd "${SCRIPT_DIR}/modules/slack-bridge" && "${DOCKER_COMPOSE[@]}" up -d)
elif [[ "${SLACK_BRIDGE_ENABLED:-false}" == "true" ]]; then
    warn "Slack bridge is enabled in deploy.yaml but config file is missing. Run module setup first."
fi

success "All services started."
