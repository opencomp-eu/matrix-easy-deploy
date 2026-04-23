# scripts/setup/dependencies.sh
# Host dependency checks for setup wizard.

check_dependencies() {
    info "Checking dependencies…"

    local missing=()

    if ! command -v docker &>/dev/null; then
        missing+=("docker")
    elif ! docker info &>/dev/null 2>&1; then
        die "Docker is installed but the daemon isn't running (or you need sudo). Please start Docker and re-run."
    fi

    if ! docker compose version &>/dev/null 2>&1 && ! command -v docker-compose &>/dev/null; then
        missing+=("docker-compose")
    fi

    if ! command -v openssl &>/dev/null; then
        missing+=("openssl")
    fi

    if ! command -v curl &>/dev/null; then
        missing+=("curl")
    fi

    if ! command -v python3 &>/dev/null; then
        missing+=("python3")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "The following required tools are missing:"
        for dep in "${missing[@]}"; do
            echo -e "    ${RED}•${RESET} ${dep}"
        done
        echo
        echo "  On Ubuntu/Debian:  sudo apt-get install -y ${missing[*]}"
        echo "  On Fedora/RHEL:    sudo dnf install -y ${missing[*]}"
        echo
        die "Please install the missing dependencies and re-run setup.sh."
    fi

    success "All dependencies satisfied."
}
