#!/usr/bin/env python3

import argparse
import shlex
from pathlib import Path

import yaml


MODULE_NAME_TO_KEY = {
    "hookshot": "hookshot",
    "whatsapp-bridge": "whatsapp_bridge",
    "slack-bridge": "slack_bridge",
}


def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def load_or_init(path: Path) -> dict:
    if not path.exists():
        return {
            "matrix": {
                "domain": "matrix.example.com",
                "server_name": "example.com",
                "admin_username": "admin",
            },
            "features": {
                "registration_enabled": False,
                "federation_enabled": True,
                "element": {"enabled": True, "domain": "element.example.com"},
                "calls": {"enabled": True, "livekit_domain": "livekit.example.com"},
                "sso": {"enabled": False, "providers": []},
            },
            "modules": {
                "hookshot": {"enabled": False, "domain": "hookshot.example.com"},
                "whatsapp_bridge": {"enabled": False, "admin_username": "admin"},
                "slack_bridge": {"enabled": False, "admin_username": "admin"},
            },
        }

    with path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("deploy.yaml root must be an object")
    return data


def save(path: Path, data: dict) -> None:
    with path.open("w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def ensure_module_enabled(config: dict, module_name: str) -> None:
    key = MODULE_NAME_TO_KEY.get(module_name)
    if not key:
        return

    modules = config.setdefault("modules", {})
    if not isinstance(modules, dict):
        modules = {}
        config["modules"] = modules

    module_cfg = modules.setdefault(key, {})
    if not isinstance(module_cfg, dict):
        module_cfg = {}
        modules[key] = module_cfg

    module_cfg["enabled"] = True


def update_module_config(
    config: dict,
    module_name: str,
    enabled: bool | None = None,
    admin_username: str | None = None,
    db_name: str | None = None,
    domain: str | None = None,
) -> None:
    key = MODULE_NAME_TO_KEY.get(module_name)
    if not key:
        raise ValueError(f"Unsupported module name: {module_name}")

    modules = config.setdefault("modules", {})
    if not isinstance(modules, dict):
        modules = {}
        config["modules"] = modules

    module_cfg = modules.setdefault(key, {})
    if not isinstance(module_cfg, dict):
        module_cfg = {}
        modules[key] = module_cfg

    if enabled is not None:
        module_cfg["enabled"] = bool(enabled)
    if admin_username is not None:
        module_cfg["admin_username"] = admin_username
    if db_name is not None:
        module_cfg["db_name"] = db_name
    if domain is not None:
        module_cfg["domain"] = domain


def update_core_config(
    config: dict,
    matrix_domain: str,
    server_name: str,
    admin_username: str,
    registration_enabled: bool,
    federation_enabled: bool,
    install_element: bool,
    element_domain: str,
    calls_enabled: bool,
    livekit_domain: str,
) -> None:
    matrix = config.setdefault("matrix", {})
    if not isinstance(matrix, dict):
        matrix = {}
        config["matrix"] = matrix
    matrix["domain"] = matrix_domain
    matrix["server_name"] = server_name
    matrix["admin_username"] = admin_username

    features = config.setdefault("features", {})
    if not isinstance(features, dict):
        features = {}
        config["features"] = features

    features["registration_enabled"] = bool(registration_enabled)
    features["federation_enabled"] = bool(federation_enabled)

    element = features.setdefault("element", {})
    if not isinstance(element, dict):
        element = {}
        features["element"] = element
    element["enabled"] = bool(install_element)
    element["domain"] = element_domain if install_element else ""

    calls = features.setdefault("calls", {})
    if not isinstance(calls, dict):
        calls = {}
        features["calls"] = calls
    calls["enabled"] = bool(calls_enabled)
    calls["livekit_domain"] = livekit_domain if calls_enabled else ""


def shell_bool_default(value: bool, yes_default: str = "y", no_default: str = "n") -> str:
    return yes_default if value else no_default


def emit_wizard_defaults(config: dict) -> str:
    matrix = config.get("matrix", {}) if isinstance(config.get("matrix", {}), dict) else {}
    features = config.get("features", {}) if isinstance(config.get("features", {}), dict) else {}

    element = features.get("element", {}) if isinstance(features.get("element", {}), dict) else {}
    calls = features.get("calls", {}) if isinstance(features.get("calls", {}), dict) else {}

    matrix_domain = matrix.get("domain", "matrix.example.com")
    server_name = matrix.get("server_name", "example.com")
    admin_username = matrix.get("admin_username", "admin")

    defaults = {
        "config_matrix_domain": matrix_domain,
        "config_server_name": server_name,
        "config_admin_username": admin_username,
        "config_registration_default": shell_bool_default(
            to_bool(features.get("registration_enabled", False)), yes_default="y", no_default="n"
        ),
        "config_federation_default": shell_bool_default(
            to_bool(features.get("federation_enabled", True)), yes_default="y", no_default="n"
        ),
        "config_element_default": shell_bool_default(
            to_bool(element.get("enabled", True)), yes_default="y", no_default="n"
        ),
        "config_element_domain": element.get("domain", ""),
        "config_calls_default": shell_bool_default(
            to_bool(calls.get("enabled", True)), yes_default="y", no_default="n"
        ),
        "config_livekit_domain": calls.get("livekit_domain", ""),
    }

    lines = []
    for key, value in defaults.items():
        lines.append(f"{key}={shlex.quote(str(value))}")
    return "\n".join(lines)


def emit_module_defaults(config: dict, module_name: str) -> str:
    key = MODULE_NAME_TO_KEY.get(module_name)
    if not key:
        raise ValueError(f"Unsupported module name: {module_name}")

    modules = config.get("modules", {}) if isinstance(config.get("modules", {}), dict) else {}
    module_cfg = modules.get(key, {}) if isinstance(modules.get(key, {}), dict) else {}

    defaults: dict[str, str] = {}
    if key == "whatsapp_bridge":
        defaults["module_enabled"] = "true" if bool(module_cfg.get("enabled", False)) else "false"
        defaults["module_admin_username"] = str(module_cfg.get("admin_username", ""))
        defaults["module_db_name"] = str(module_cfg.get("db_name", ""))
    elif key == "slack_bridge":
        defaults["module_enabled"] = "true" if bool(module_cfg.get("enabled", False)) else "false"
        defaults["module_admin_username"] = str(module_cfg.get("admin_username", ""))
        defaults["module_db_name"] = str(module_cfg.get("db_name", ""))
    elif key == "hookshot":
        defaults["module_enabled"] = "true" if bool(module_cfg.get("enabled", False)) else "false"
        defaults["module_domain"] = str(module_cfg.get("domain", ""))

    lines = []
    for out_key, out_value in defaults.items():
        lines.append(f"{out_key}={shlex.quote(str(out_value))}")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Edit deploy.yaml")
    parser.add_argument("--deploy-yaml", required=True)

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--enable-module")
    action.add_argument("--set-module-config")
    action.add_argument("--set-core", action="store_true")
    action.add_argument("--print-wizard-defaults", action="store_true")
    action.add_argument("--print-module-defaults")

    parser.add_argument("--matrix-domain")
    parser.add_argument("--server-name")
    parser.add_argument("--admin-username")
    parser.add_argument("--registration-enabled")
    parser.add_argument("--federation-enabled")
    parser.add_argument("--install-element")
    parser.add_argument("--element-domain")
    parser.add_argument("--calls-enabled")
    parser.add_argument("--livekit-domain")

    parser.add_argument("--module-enabled")
    parser.add_argument("--module-admin-username")
    parser.add_argument("--module-db-name")
    parser.add_argument("--module-domain")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    deploy_yaml = Path(args.deploy_yaml)
    config = load_or_init(deploy_yaml)

    if args.print_wizard_defaults:
        print(emit_wizard_defaults(config))
        return 0

    if args.print_module_defaults:
        print(emit_module_defaults(config, args.print_module_defaults))
        return 0

    if args.enable_module:
        ensure_module_enabled(config, args.enable_module)
        save(deploy_yaml, config)
        return 0

    if args.set_module_config:
        enabled = to_bool(args.module_enabled) if args.module_enabled is not None else None
        update_module_config(
            config,
            module_name=args.set_module_config,
            enabled=enabled,
            admin_username=args.module_admin_username,
            db_name=args.module_db_name,
            domain=args.module_domain,
        )
        save(deploy_yaml, config)
        return 0

    if args.set_core:
        required = {
            "matrix_domain": args.matrix_domain,
            "server_name": args.server_name,
            "admin_username": args.admin_username,
            "registration_enabled": args.registration_enabled,
            "federation_enabled": args.federation_enabled,
            "install_element": args.install_element,
            "element_domain": args.element_domain,
            "calls_enabled": args.calls_enabled,
            "livekit_domain": args.livekit_domain,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"Missing required --set-core arguments: {', '.join(missing)}")

        update_core_config(
            config=config,
            matrix_domain=args.matrix_domain,
            server_name=args.server_name,
            admin_username=args.admin_username,
            registration_enabled=to_bool(args.registration_enabled),
            federation_enabled=to_bool(args.federation_enabled),
            install_element=to_bool(args.install_element),
            element_domain=args.element_domain,
            calls_enabled=to_bool(args.calls_enabled),
            livekit_domain=args.livekit_domain,
        )
        save(deploy_yaml, config)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())