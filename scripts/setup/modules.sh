# scripts/setup/modules.sh
# Module setup dispatcher for setup.sh --module.

list_available_modules() {
    local module_dir
    for module_dir in "${SCRIPT_DIR}"/modules/*; do
        [[ -d "$module_dir" ]] || continue
        [[ -f "$module_dir/setup.sh" ]] || continue
        basename "$module_dir"
    done | sort
}

run_module_setup() {
    local module="$1"
    local module_script="${SCRIPT_DIR}/modules/${module}/setup.sh"

    if [[ ! -f "$module_script" ]]; then
        die "Module '${module}' not found. Expected: ${module_script}"
    fi

    info "Running setup for module: ${module}"
    bash "$module_script"
}
