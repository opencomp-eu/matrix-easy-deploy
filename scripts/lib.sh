#!/usr/bin/env bash
# scripts/lib.sh — shared utilities for matrix-easy-deploy scripts
# Source this file; do not execute it directly.

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
info()    { echo -e "${CYAN}  -->${RESET} $*"; }
success() { echo -e "${GREEN}  [ok]${RESET} $*"; }
warn()    { echo -e "${YELLOW}  [!]${RESET}  $*"; }
error()   { echo -e "${RED}  [ERR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

# ask VAR "Question" ["default"]
# Reads into variable VAR; uses default if user hits Enter with no input.
ask() {
    local _var="$1"
    local _prompt="$2"
    local _default="${3:-}"

    if [[ -n "$_default" ]]; then
        echo -ne "${BOLD}  ${_prompt}${RESET} ${CYAN}[${_default}]${RESET}: "
    else
        echo -ne "${BOLD}  ${_prompt}${RESET}: "
    fi

    local _input
    read -r _input
    printf -v "$_var" '%s' "${_input:-$_default}"
}

# ask_secret VAR "Question"
# Like ask but hides input (for passwords).
ask_secret() {
    local _var="$1"
    local _prompt="$2"

    echo -ne "${BOLD}  ${_prompt}${RESET}: "
    local _input
    read -rs _input
    echo
    printf -v "$_var" '%s' "$_input"
}

# ask_yn VAR "Question" [y|n]
# Returns 0 for yes, 1 for no. Stores "y" or "n" in VAR.
ask_yn() {
    local _var="$1"
    local _prompt="$2"
    local _default="${3:-n}"

    local _hint
    if [[ "$_default" == "y" ]]; then _hint="Y/n"; else _hint="y/N"; fi

    echo -ne "${BOLD}  ${_prompt}${RESET} ${CYAN}[${_hint}]${RESET}: "
    local _input
    read -r _input
    _input="${_input:-$_default}"
    _input="${_input,,}"   # lowercase

    case "$_input" in
        y|yes) printf -v "$_var" 'y' ;;
        *)     printf -v "$_var" 'n' ;;
    esac
}

# ---------------------------------------------------------------------------
# Secret generation
# ---------------------------------------------------------------------------
generate_secret() {
    openssl rand -hex 32
}

# ---------------------------------------------------------------------------
# Template rendering
# Replaces all {{KEY}} occurrences in a file using a plain-text env var map.
# Usage: render_template src.template dest replacements_file
#
# replacements_file is a file of lines like:  KEY=value
# (Values may NOT contain newlines.)
# ---------------------------------------------------------------------------
render_template() {
    local src="$1"
    local dest="$2"
    local vars_file="$3"

    cp "$src" "$dest"

    while IFS='=' read -r key value; do
        # Skip blank lines and comments
        [[ -z "$key" || "$key" == \#* ]] && continue
        # Escape characters that would confuse sed
        local esc_value
        esc_value=$(printf '%s\n' "$value" | sed 's/[&/\]/\\&/g')
        sed -i "s|{{${key}}}|${esc_value}|g" "$dest"
    done < "$vars_file"
}

# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------
docker_compose_cmd() {
    # Prefer "docker compose" (v2) over the legacy "docker-compose"
    if docker compose version &>/dev/null 2>&1; then
        echo "docker compose"
    elif command -v docker-compose &>/dev/null; then
        echo "docker-compose"
    else
        die "Neither 'docker compose' nor 'docker-compose' found. Please install Docker Compose."
    fi
}

ensure_docker_network() {
    local name="$1"
    if ! docker network inspect "$name" &>/dev/null; then
        info "Creating Docker network: ${name}"
        docker network create "$name" || die "Failed to create Docker network '$name'."
        success "Network '${name}' created."
    else
        info "Docker network '${name}' already exists — skipping."
    fi
}

ensure_docker_volume() {
    local name="$1"
    if ! docker volume inspect "$name" &>/dev/null; then
        info "Creating Docker volume: ${name}"
        docker volume create "$name" || die "Failed to create Docker volume '$name'."
        success "Volume '${name}' created."
    fi
}

# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------

# extract_base_domain "matrix.example.com" → "example.com"
extract_base_domain() {
    local fqdn="$1"
    echo "$fqdn" | awk -F. '{
        n=NF;
        if(n>=3) { for(i=2;i<=n;i++) printf "%s%s",$i,(i<n?".":""); print "" }
        else print $0
    }'
}

# ---------------------------------------------------------------------------
# Wait for a URL to become reachable
# ---------------------------------------------------------------------------
wait_for_url() {
    local url="$1"
    local label="${2:-service}"
    local max_attempts="${3:-30}"
    local sleep_secs="${4:-5}"

    info "Waiting for ${label} to be ready…"
    local attempt=0
    until curl -fsSL --max-time 5 "$url" &>/dev/null; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max_attempts ]]; then
            die "Timed out waiting for ${label} at ${url}"
        fi
        echo -ne "    attempt ${attempt}/${max_attempts}…\r"
        sleep "$sleep_secs"
    done
    echo
    success "${label} is up."
}

# ---------------------------------------------------------------------------
# Runtime desired-state loader
# Uses deploy.yaml/.matrix-easy-deploy state to set runtime feature flags.
# ---------------------------------------------------------------------------
load_runtime_desired_state() {
    local project_root="$1"
    local state_script="${project_root}/scripts/runtime_state.py"
    [[ -f "$state_script" ]] || return 0

    local state_exports
    if state_exports="$(python3 "$state_script" --project-root "$project_root" --emit-shell 2>/dev/null)"; then
        [[ -n "$state_exports" ]] && eval "$state_exports"
    fi
}

# ---------------------------------------------------------------------------
# Homeserver data directory permissions
# ---------------------------------------------------------------------------
ensure_homeserver_data_permissions() {
    local project_root="$1"
    local implementation="synapse"

    if [[ -f "${project_root}/.env" ]]; then
        implementation="$(sed -n 's/^SERVER_IMPLEMENTATION=//p' "${project_root}/.env" | head -n1)"
        implementation="${implementation:-synapse}"
    fi

    case "${implementation,,}" in
        tuwunel)
            ensure_tuwunel_data_permissions "$project_root"
            ;;
        *)
            ensure_synapse_data_permissions "$project_root"
            ;;
    esac
}

ensure_tuwunel_data_permissions() {
    local project_root="$1"
    local tuwunel_data_dir="${project_root}/modules/core/tuwunel_data"
    local appservices_dir="${tuwunel_data_dir}/appservices"

    mkdir -p "$appservices_dir"
    chmod -R a+rwX "$tuwunel_data_dir" 2>/dev/null || true
}

# Ensures modules/core/synapse_data is writable by Synapse (UID 991).
ensure_synapse_data_permissions() {
    local project_root="$1"
    local synapse_data_dir="${project_root}/modules/core/synapse_data"

    mkdir -p "$synapse_data_dir"

    # Try host-side ownership/permissions first.
    local chown_ok="false"
    if chown -R 991:991 "$synapse_data_dir" 2>/dev/null; then
        chown_ok="true"
    fi
    find "$synapse_data_dir" -type d -exec chmod 750 {} + 2>/dev/null || true
    find "$synapse_data_dir" -type f -exec chmod 640 {} + 2>/dev/null || true

    # If host-side chown failed, try normalizing via Docker (works even when
    # host user cannot directly chown numeric IDs).
    if [[ "$chown_ok" != "true" ]] && command -v docker &>/dev/null; then
        info "Normalizing Synapse data permissions via helper container…"
        if docker run --rm \
            -v "${synapse_data_dir}:/data" \
            alpine:3 \
            sh -c "chown -R 991:991 /data && find /data -type d -exec chmod 750 {} + && find /data -type f -exec chmod 640 {} +" \
            >/dev/null 2>&1; then
            chown_ok="true"
        else
            warn "Could not normalize Synapse data ownership via helper container."
        fi
    fi

    # Final fallback to keep deployment usable if ownership normalization fails.
    local write_test="${synapse_data_dir}/.med-write-test"
    if ! touch "$write_test" 2>/dev/null; then
        warn "Synapse data directory is still not writable. Applying permissive fallback permissions."
        chmod -R a+rwX "$synapse_data_dir" 2>/dev/null || true
    else
        rm -f "$write_test"
    fi
}
