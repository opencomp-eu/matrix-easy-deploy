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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Edit deploy.yaml")
    parser.add_argument("--deploy-yaml", required=True)

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--enable-module")
    action.add_argument("--set-core", action="store_true")
    action.add_argument("--print-wizard-defaults", action="store_true")

    parser.add_argument("--matrix-domain")
    parser.add_argument("--server-name")
    parser.add_argument("--admin-username")
    parser.add_argument("--registration-enabled")
    parser.add_argument("--federation-enabled")
    parser.add_argument("--install-element")
    parser.add_argument("--element-domain")
    parser.add_argument("--calls-enabled")
    parser.add_argument("--livekit-domain")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    deploy_yaml = Path(args.deploy_yaml)
    config = load_or_init(deploy_yaml)

    if args.print_wizard_defaults:
        print(emit_wizard_defaults(config))
        return 0

    if args.enable_module:
        ensure_module_enabled(config, args.enable_module)
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