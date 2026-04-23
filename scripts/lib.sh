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
