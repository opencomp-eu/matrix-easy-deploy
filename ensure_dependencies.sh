#!/usr/bin/env bash
# ensure_dependencies.sh — install required host dependencies non-interactively.

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"
# shellcheck source=scripts/setup/dependencies.sh
source "${SCRIPT_DIR}/scripts/setup/dependencies.sh"

ensure_dependencies_installed