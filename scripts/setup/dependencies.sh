# scripts/setup/dependencies.sh
# Host dependency checks for setup wizard.

required_dependency_keys() {
    printf '%s\n' docker docker-compose openssl curl python3
}

is_dependency_missing() {
    local dep="$1"

    case "$dep" in
        docker)
            ! command -v docker &>/dev/null
            ;;
        docker-compose)
            ! docker compose version &>/dev/null 2>&1 && ! command -v docker-compose &>/dev/null
            ;;
        openssl|curl|python3)
            ! command -v "$dep" &>/dev/null
            ;;
        *)
            die "Unknown dependency key: ${dep}"
            ;;
    esac
}

collect_missing_dependencies() {
    local output_var="$1"
    local missing=()
    local dep

    while IFS= read -r dep; do
        [[ -z "$dep" ]] && continue
        if is_dependency_missing "$dep"; then
            missing+=("$dep")
        fi
    done < <(required_dependency_keys)

    printf -v "$output_var" '%s' "${missing[*]:-}"
}

detect_supported_package_manager() {
    if command -v apt-get &>/dev/null; then
        echo "apt-get"
        return 0
    fi

    if command -v dnf &>/dev/null; then
        echo "dnf"
        return 0
    fi

    if command -v pacman &>/dev/null; then
        echo "pacman"
        return 0
    fi

    return 1
}

dependency_packages_for_manager() {
    local manager="$1"
    local dep="$2"

    case "$manager:$dep" in
        apt-get:openssl) echo "openssl" ;;
        apt-get:curl) echo "curl" ;;
        apt-get:python3) echo "python3" ;;
        dnf:openssl) echo "openssl" ;;
        dnf:curl) echo "curl" ;;
        dnf:python3) echo "python3" ;;
        pacman:openssl) echo "openssl" ;;
        pacman:curl) echo "curl" ;;
        pacman:python3) echo "python" ;;
        *) die "No package mapping for ${dep} via ${manager}" ;;
    esac
}

docker_install_required() {
    is_dependency_missing "docker" || is_dependency_missing "docker-compose"
}

command_prefix_for_privileged_install() {
    local output_var="$1"

    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        printf -v "$output_var" '%s' ""
        return 0
    fi

    if ! command -v sudo &>/dev/null; then
        die "Installing dependencies requires root privileges or sudo."
    fi

    printf -v "$output_var" '%s' "sudo"
}

install_missing_dependencies() {
    local manager="$1"
    shift

    local missing=("$@")
    if [[ ${#missing[@]} -eq 0 ]]; then
        success "All required dependencies are already installed."
        return 0
    fi

    local prefix_cmd
    command_prefix_for_privileged_install prefix_cmd

    local packages=()
    local -A seen_packages=()
    local install_docker="false"
    local dep
    local package
    for dep in "${missing[@]}"; do
        if [[ "$dep" == "docker" || "$dep" == "docker-compose" ]]; then
            install_docker="true"
            continue
        fi

        package="$(dependency_packages_for_manager "$manager" "$dep")"
        if [[ -z "${seen_packages[$package]:-}" ]]; then
            packages+=("$package")
            seen_packages["$package"]=1
        fi
    done

    if [[ ${#packages[@]} -gt 0 ]]; then
        info "Installing missing dependencies with ${manager}: ${packages[*]}"

        case "$manager" in
            apt-get)
                if [[ -n "$prefix_cmd" ]]; then
                    DEBIAN_FRONTEND=noninteractive "$prefix_cmd" apt-get update
                    DEBIAN_FRONTEND=noninteractive "$prefix_cmd" apt-get install -y "${packages[@]}"
                else
                    DEBIAN_FRONTEND=noninteractive apt-get update
                    DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
                fi
                ;;
            dnf)
                if [[ -n "$prefix_cmd" ]]; then
                    "$prefix_cmd" dnf install -y "${packages[@]}"
                else
                    dnf install -y "${packages[@]}"
                fi
                ;;
            pacman)
                if [[ -n "$prefix_cmd" ]]; then
                    "$prefix_cmd" pacman -Sy --noconfirm --needed "${packages[@]}"
                else
                    pacman -Sy --noconfirm --needed "${packages[@]}"
                fi
                ;;
            *)
                die "Unsupported package manager: ${manager}"
                ;;
        esac
    fi

    if [[ "$install_docker" == "true" ]]; then
        local docker_script
        docker_script="$(mktemp "${TMPDIR:-/tmp}/get-docker.XXXXXX.sh")"

        info "Installing Docker with the official convenience script."
        curl -fsSL https://get.docker.com -o "$docker_script"

        if [[ -n "$prefix_cmd" ]]; then
            "$prefix_cmd" sh "$docker_script"
        else
            sh "$docker_script"
        fi

        rm -f "$docker_script"
    fi
}

ensure_docker_daemon_running() {
    if ! command -v docker &>/dev/null; then
        return 0
    fi

    if docker info &>/dev/null 2>&1; then
        return 0
    fi

    warn "Docker is installed but not ready. Attempting to start the daemon."

    local prefix_cmd
    command_prefix_for_privileged_install prefix_cmd

    if command -v systemctl &>/dev/null; then
        if [[ -n "$prefix_cmd" ]]; then
            "$prefix_cmd" systemctl enable --now docker || true
        else
            systemctl enable --now docker || true
        fi
    fi

    if docker info &>/dev/null 2>&1; then
        success "Docker daemon is running."
        return 0
    fi

    die "Docker is installed but the daemon isn't running (or your user cannot access it). Please start Docker and re-run."
}

ensure_dependencies_installed() {
    info "Ensuring required dependencies are installed…"

    local missing_text
    collect_missing_dependencies missing_text

    local missing=()
    if [[ -n "$missing_text" ]]; then
        IFS=' ' read -r -a missing <<< "$missing_text"
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        local manager
        manager="$(detect_supported_package_manager)" || die "No supported package manager found. Install docker, docker compose, openssl, curl, and python3 manually."
        install_missing_dependencies "$manager" "${missing[@]}"
    else
        success "All required packages are already present."
    fi

    ensure_docker_daemon_running
    check_dependencies
}

check_dependencies() {
    info "Checking dependencies…"

    local missing=()

    if is_dependency_missing "docker"; then
        missing+=("docker")
    elif ! docker info &>/dev/null 2>&1; then
        die "Docker is installed but the daemon isn't running (or you need sudo). Please start Docker and re-run."
    fi

    local dep
    while IFS= read -r dep; do
        [[ "$dep" == "docker" ]] && continue
        if is_dependency_missing "$dep"; then
            missing+=("$dep")
        fi
    done < <(required_dependency_keys)

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "The following required tools are missing:"
        for dep in "${missing[@]}"; do
            echo -e "    ${RED}•${RESET} ${dep}"
        done

        local apt_packages=()
        local dnf_packages=()
        local pacman_packages=()
        for dep in "${missing[@]}"; do
            if [[ "$dep" == "docker" || "$dep" == "docker-compose" ]]; then
                continue
            fi
            apt_packages+=("$(dependency_packages_for_manager "apt-get" "$dep")")
            dnf_packages+=("$(dependency_packages_for_manager "dnf" "$dep")")
            pacman_packages+=("$(dependency_packages_for_manager "pacman" "$dep")")
        done

        echo
        if [[ ${#apt_packages[@]} -gt 0 ]]; then
            echo "  On Ubuntu/Debian:  sudo apt-get install -y ${apt_packages[*]}"
            echo "  On Fedora/RHEL:    sudo dnf install -y ${dnf_packages[*]}"
            echo "  On Arch Linux:     sudo pacman -Sy --noconfirm --needed ${pacman_packages[*]}"
        fi
        if docker_install_required; then
            echo "  For Docker/Compose: curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh ./get-docker.sh"
        fi
        echo "  Or run:            bash ensure_dependencies.sh"
        echo
        die "Please install the missing dependencies and re-run setup.sh."
    fi

    success "All dependencies satisfied."
}
