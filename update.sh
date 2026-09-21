#!/usr/bin/env bash
# update.sh — update all matrix-easy-deploy services to the latest images
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"

verbose="false"
for arg in "$@"; do
    case "$arg" in
        -v|--verbose) verbose="true" ;;
        -h|--help)
            echo "Usage: bash update.sh [--verbose]"
            echo "Pull images and restart services. Default output is a short summary."
            exit 0
            ;;
    esac
done

if [[ "$verbose" == "true" ]]; then
    export EASYDEPLOY_VERBOSE=1
    unset EASYDEPLOY_QUIET || true
else
    export EASYDEPLOY_QUIET=1
    export COMPOSE_PROGRESS=quiet
    export DOCKER_CLI_HINTS=false
    echo "Updating Matrix…"
fi

pull_image() {
    if [[ "$verbose" == "true" ]]; then
        docker pull "$1"
    else
        docker pull -q "$1" >/dev/null
    fi
}

# Load settings from .env so we know what's actually installed
INSTALL_ELEMENT="true"   # default: assume Element is present if .env is missing
HOOKSHOT_ENABLED="false"
WHATSAPP_BRIDGE_ENABLED="false"
SLACK_BRIDGE_ENABLED="false"
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    load_deploy_env "${SCRIPT_DIR}/.env"
fi

load_runtime_desired_state "${SCRIPT_DIR}"

info "Stopping services…"
bash "${SCRIPT_DIR}/stop.sh"

info "Pulling updated images…"
pull_image caddy:2-alpine
pull_image postgres:16-alpine
pull_image matrixdotorg/synapse:latest
pull_image ghcr.io/matrix-construct/tuwunel:latest
pull_image coturn/coturn:latest
pull_image livekit/livekit-server:latest
pull_image ghcr.io/element-hq/lk-jwt-service:latest

if [[ "${GUEST_ACCESS_ENABLED:-false}" == "true" ]]; then
    pull_image ghcr.io/element-hq/element-call:v0.21.0
fi

if [[ "${INSTALL_ELEMENT:-true}" == "true" ]]; then
    pull_image ghcr.io/element-hq/element-web:latest
fi

if [[ "${HOOKSHOT_ENABLED:-false}" == "true" && -f "${SCRIPT_DIR}/modules/hookshot/hookshot/config.yml" ]]; then
    pull_image halfshot/matrix-hookshot:latest
fi

if [[ "${WHATSAPP_BRIDGE_ENABLED:-false}" == "true" && -f "${SCRIPT_DIR}/modules/whatsapp-bridge/whatsapp/config.yaml" ]]; then
    pull_image dock.mau.dev/mautrix/whatsapp:latest
fi

if [[ "${SLACK_BRIDGE_ENABLED:-false}" == "true" && -f "${SCRIPT_DIR}/modules/slack-bridge/slack/config.yaml" ]]; then
    pull_image dock.mau.dev/mautrix/slack:latest
fi

info "Restarting services…"
bash "${SCRIPT_DIR}/start.sh"

if easydeploy_quiet; then
    echo "Update complete."
else
    success "Update complete."
fi
