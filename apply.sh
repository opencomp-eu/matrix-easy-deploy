#!/usr/bin/env bash
# apply.sh — Apply configuration changes from deploy.yaml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}" && pwd)"

python3 "${SCRIPT_DIR}/scripts/apply.py" "$@"