#!/usr/bin/env bash
# update.sh — update all matrix-easy-deploy services to the latest images
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"

# Load settings from .env so we know what's actually installed
INSTALL_ELEMENT="true"   # default: assume Element is present if .env is missing
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    # shellcheck disable=SC1090
    source "${SCRIPT_DIR}/.env"
fi

info "Stopping services…"
bash "${SCRIPT_DIR}/stop.sh"

info "Pulling updated images…"
docker pull caddy:2-alpine
docker pull postgres:16-alpine
docker pull matrixdotorg/synapse:latest
docker pull coturn/coturn:latest
docker pull livekit/livekit-server:latest

if [[ "${INSTALL_ELEMENT:-true}" == "true" ]]; then
    docker pull vectorim/element-web:latest
fi

if [[ -n "${HOOKSHOT_DOMAIN:-}" && -f "${SCRIPT_DIR}/modules/hookshot/hookshot/config.yml" ]]; then
    docker pull halfshot/matrix-hookshot:latest
fi

if [[ -f "${SCRIPT_DIR}/modules/whatsapp-bridge/whatsapp/config.yaml" ]]; then
    docker pull dock.mau.dev/mautrix/whatsapp:latest
fi

if [[ -f "${SCRIPT_DIR}/modules/slack-bridge/slack/config.yaml" ]]; then
    docker pull dock.mau.dev/mautrix/slack:latest
fi

info "Restarting services…"
bash "${SCRIPT_DIR}/start.sh"

success "Update complete."