#!/usr/bin/env bash
# update.sh — update all matrix-easy-deploy services to the latest images
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"

# Load settings from .env so we know what's actually installed
INSTALL_ELEMENT="true"   # default: assume Element is present if .env is missing
HOOKSHOT_ENABLED="false"
WHATSAPP_BRIDGE_ENABLED="false"
SLACK_BRIDGE_ENABLED="false"
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    # shellcheck disable=SC1090
    source "${SCRIPT_DIR}/.env"
fi

load_runtime_desired_state "${SCRIPT_DIR}"

info "Stopping services…"
bash "${SCRIPT_DIR}/stop.sh"

info "Pulling updated images…"
docker pull caddy:2-alpine
docker pull postgres:16-alpine
docker pull matrixdotorg/synapse:latest
docker pull ghcr.io/matrix-construct/tuwunel:latest
docker pull coturn/coturn:latest
docker pull livekit/livekit-server:latest

if [[ "${INSTALL_ELEMENT:-true}" == "true" ]]; then
    docker pull vectorim/element-web:latest
fi

if [[ "${HOOKSHOT_ENABLED:-false}" == "true" && -f "${SCRIPT_DIR}/modules/hookshot/hookshot/config.yml" ]]; then
    docker pull halfshot/matrix-hookshot:latest
fi

if [[ "${WHATSAPP_BRIDGE_ENABLED:-false}" == "true" && -f "${SCRIPT_DIR}/modules/whatsapp-bridge/whatsapp/config.yaml" ]]; then
    docker pull dock.mau.dev/mautrix/whatsapp:latest
fi

if [[ "${SLACK_BRIDGE_ENABLED:-false}" == "true" && -f "${SCRIPT_DIR}/modules/slack-bridge/slack/config.yaml" ]]; then
    docker pull dock.mau.dev/mautrix/slack:latest
fi

info "Restarting services…"
bash "${SCRIPT_DIR}/start.sh"

success "Update complete."