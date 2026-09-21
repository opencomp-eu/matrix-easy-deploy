#!/usr/bin/env bash
# update.sh — pull git, skip if unchanged, otherwise apply
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}" && pwd)"
# shellcheck source=scripts/lib.sh
if [[ -f "${SCRIPT_DIR}/scripts/lib.sh" ]]; then
	source "${SCRIPT_DIR}/scripts/lib.sh"
else
	clear_parent_python_env() { :; }
	ensure_docker_group_session() { :; }
fi

clear_parent_python_env
ensure_docker_group_session "$@"

ensure_dependencies="false"
python_args=()

for arg in "$@"; do
	case "$arg" in
		--ensure-dependencies)
			ensure_dependencies="true"
			;;
		*)
			python_args+=("$arg")
			;;
	esac
done

if [[ "$ensure_dependencies" == "true" ]]; then
	bash "${SCRIPT_DIR}/ensure_dependencies.sh"
fi

python3 "${SCRIPT_DIR}/scripts/update.py" "${python_args[@]}"
