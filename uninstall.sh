#!/usr/bin/env bash
# uninstall.sh — remove matrix-easy-deploy runtime resources and generated state
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/scripts/lib.sh"

ASSUME_YES="false"

print_help() {
    cat <<EOF
Usage:
  bash uninstall.sh [--yes]

Options:
  -y, --yes   Skip interactive confirmation prompts.
  -h, --help  Show this help message.

Behavior:
  - Stops matrix-easy-deploy services if present.
  - Removes project-owned containers, volumes, and networks.
  - Deletes generated runtime/config state in the repository.
  - Preserves deploy.yaml.
EOF
}

remove_path_if_present() {
    local rel_path="$1"
    local abs_path="${SCRIPT_DIR}/${rel_path}"

    if [[ -e "$abs_path" ]]; then
        rm -rf "$abs_path"
        info "Removed ${rel_path}"
    fi
}

remove_docker_container_if_present() {
    local container_name="$1"
    if docker container inspect "$container_name" &>/dev/null; then
        docker rm -f "$container_name" >/dev/null
        info "Removed container ${container_name}"
    fi
}

remove_docker_volume_if_present() {
    local volume_name="$1"
    if docker volume inspect "$volume_name" &>/dev/null; then
        if docker volume rm "$volume_name" >/dev/null 2>&1; then
            info "Removed volume ${volume_name}"
        else
            warn "Could not remove volume '${volume_name}' (it may still be in use)."
        fi
    fi
}

remove_docker_network_if_present() {
    local network_name="$1"
    if docker network inspect "$network_name" &>/dev/null; then
        if docker network rm "$network_name" >/dev/null 2>&1; then
            info "Removed network ${network_name}"
        else
            warn "Could not remove network '${network_name}' (it may still be in use)."
        fi
    fi
}

confirm_uninstall() {
    echo
    warn "This will remove Matrix services, runtime data, and generated files from this repository."
    info "deploy.yaml will be preserved."

    local confirm
    ask_yn confirm "Continue with uninstall?" "n"
    [[ "$confirm" == "y" ]] || return 1

    local final_confirm
    ask_yn final_confirm "Final confirmation: uninstall now?" "n"
    [[ "$final_confirm" == "y" ]]
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -y|--yes)
                ASSUME_YES="true"
                ;;
            -h|--help)
                print_help
                exit 0
                ;;
            *)
                die "Unknown argument: $1"
                ;;
        esac
        shift
    done

    if [[ "$ASSUME_YES" != "true" ]]; then
        confirm_uninstall || {
            info "Uninstall cancelled."
            exit 0
        }
    fi

    info "Starting uninstall cleanup..."

    if [[ -x "${SCRIPT_DIR}/stop.sh" ]]; then
        info "Stopping running services..."
        if ! bash "${SCRIPT_DIR}/stop.sh"; then
            warn "stop.sh reported an error; continuing with direct cleanup."
        fi
    fi

    if command -v docker &>/dev/null; then
        local containers=(
            "caddy"
            "matrix_synapse"
            "matrix_postgres"
            "matrix_redis"
            "matrix_element"
            "matrix_coturn"
            "matrix_livekit"
            "matrix-hookshot"
            "mautrix-whatsapp"
            "mautrix-slack"
            "matrix-draupnir"
        )

        for container in "${containers[@]}"; do
            remove_docker_container_if_present "$container"
        done

        local volumes=(
            "caddy_data"
            "caddy_caddy_config"
            "core_postgres_data"
            "core_redis_data"
        )

        for volume in "${volumes[@]}"; do
            remove_docker_volume_if_present "$volume"
        done

        local networks=(
            "core_matrix_internal"
            "caddy_net"
        )

        for network in "${networks[@]}"; do
            remove_docker_network_if_present "$network"
        done
    else
        warn "Docker is not installed or not in PATH. Skipping Docker resource cleanup."
    fi

    local generated_paths=(
        ".env"
        ".matrix-easy-deploy"
        "caddy/Caddyfile"
        "modules/core/synapse/homeserver.yaml"
        "modules/core/element/config.json"
        "modules/core/synapse_data"
        "modules/calls/coturn/turnserver.conf"
        "modules/calls/livekit/livekit.yaml"
        "modules/hookshot/hookshot"
        "modules/whatsapp-bridge/whatsapp"
        "modules/slack-bridge/slack"
        "modules/draupnir/draupnir"
    )

    for generated_path in "${generated_paths[@]}"; do
        remove_path_if_present "$generated_path"
    done

    success "Uninstall cleanup complete."
    info "Preserved file: deploy.yaml"
}

main "$@"