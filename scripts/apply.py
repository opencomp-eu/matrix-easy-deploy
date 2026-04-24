#!/usr/bin/env python3
# scripts/apply.py — Shared config/state engine for matrix-easy-deploy

import argparse
import datetime as dt
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import yaml


DEFAULT_SECRET_KEYS = [
    "POSTGRES_PASSWORD",
    "REGISTRATION_SHARED_SECRET",
    "MACAROON_SECRET_KEY",
    "FORM_SECRET",
    "COTURN_SECRET",
    "LIVEKIT_SECRET",
]

MODULE_CONFIG_KEY_TO_DIR = {
    "hookshot": "hookshot",
    "whatsapp_bridge": "whatsapp-bridge",
    "slack_bridge": "slack-bridge",
}


class ApplyContext:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.config_file = project_root / "deploy.yaml"
        self.state_dir = project_root / ".matrix-easy-deploy"
        self.env_file = project_root / ".env"


def extract_base_domain(fqdn: str) -> str:
    parts = fqdn.split(".")
    if len(parts) >= 3:
        return ".".join(parts[1:])
    return fqdn


def load_config(ctx: ApplyContext) -> dict:
    if not ctx.config_file.exists():
        raise ValueError(f"Missing config file: {ctx.config_file}")

    with ctx.config_file.open() as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError("deploy.yaml must contain a YAML object at the root")

    return config


def validate_config(config: dict) -> None:
    matrix = config.get("matrix")
    if not isinstance(matrix, dict):
        raise ValueError("Missing 'matrix' section in deploy.yaml")

    for key in ("domain", "admin_username"):
        value = matrix.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Missing or invalid matrix.{key} in deploy.yaml")

    features = config.get("features", {})
    if features is not None and not isinstance(features, dict):
        raise ValueError("features must be an object when provided")

    modules = config.get("modules", {})
    if modules is not None and not isinstance(modules, dict):
        raise ValueError("modules must be an object when provided")

    if isinstance(features, dict):
        for key in ("registration_enabled", "federation_enabled"):
            if key in features and not isinstance(features[key], bool):
                raise ValueError(f"features.{key} must be true/false")

        for section in ("element", "calls", "sso"):
            if section in features and not isinstance(features.get(section), dict):
                raise ValueError(f"features.{section} must be an object")

        sso = features.get("sso", {}) if isinstance(features.get("sso", {}), dict) else {}
        if "providers" in sso and not isinstance(sso.get("providers"), list):
            raise ValueError("features.sso.providers must be a list")

    if isinstance(modules, dict):
        for key, value in modules.items():
            if not isinstance(value, dict):
                raise ValueError(f"modules.{key} must be an object")
            if "enabled" in value and not isinstance(value.get("enabled"), bool):
                raise ValueError(f"modules.{key}.enabled must be true/false")


def detect_public_ip() -> str:
    providers = ["https://api4.ipify.org", "https://ifconfig.me"]
    for provider in providers:
        try:
            result = subprocess.run(
                ["curl", "-fsSL", "--max-time", "10", provider],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            value = result.stdout.strip()
            if result.returncode == 0 and value:
                return value
        except Exception:
            continue
    return "REPLACE_WITH_YOUR_PUBLIC_IP"


def build_oidc_providers_json(providers: list) -> str:
    if not providers:
        return "[]"

    # Keep a simple normalized shape that templates can consume directly.
    normalized = []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        normalized.append(
            {
                "idp_id": provider.get("id", provider.get("name", "oidc")).lower().replace(" ", "-"),
                "idp_name": provider.get("name", "OIDC"),
                "issuer": provider.get("issuer", ""),
                "client_id": provider.get("client_id", ""),
                "client_secret": provider.get("client_secret", ""),
                "scopes": provider.get("scopes", ["openid", "profile", "email"]),
                "allow_existing_users": True,
                "enable_registration": bool(provider.get("allow_registration", True)),
                "attribute_requirements": provider.get("attribute_requirements", []),
            }
        )
    return json.dumps(normalized, separators=(",", ":"))


def derive_values(config: dict, server_ip: str | None = None) -> dict:
    derived = {}

    matrix = config["matrix"]
    features = config.get("features", {})
    modules = config.get("modules", {})

    matrix_domain = matrix["domain"]
    server_name = matrix.get("server_name") or extract_base_domain(matrix_domain)
    derived["SERVER_NAME"] = server_name

    fed_enabled = bool(features.get("federation_enabled", True))
    derived["FEDERATION_WHITELIST"] = "~" if fed_enabled else "[]"
    derived["ALLOW_PUBLIC_ROOMS_FEDERATION"] = "true" if fed_enabled else "false"

    reg_enabled = bool(features.get("registration_enabled", False))
    derived["ENABLE_REGISTRATION"] = "true" if reg_enabled else "false"

    element = features.get("element", {}) if isinstance(features.get("element", {}), dict) else {}
    element_enabled = bool(element.get("enabled", True))
    derived["INSTALL_ELEMENT"] = "true" if element_enabled else "false"
    if element_enabled:
        derived["ELEMENT_DOMAIN"] = element.get("domain") or f"element.{extract_base_domain(matrix_domain)}"
    else:
        derived["ELEMENT_DOMAIN"] = ""

    calls = features.get("calls", {}) if isinstance(features.get("calls", {}), dict) else {}
    calls_enabled = bool(calls.get("enabled", True))
    if calls_enabled:
        derived["LIVEKIT_DOMAIN"] = calls.get("livekit_domain") or f"livekit.{extract_base_domain(matrix_domain)}"
    else:
        derived["LIVEKIT_DOMAIN"] = ""

    hosts = [matrix_domain]
    if server_name != matrix_domain:
        hosts.append(server_name)
    derived["CADDY_MATRIX_HOSTS"] = ",".join(hosts)

    sso = features.get("sso", {}) if isinstance(features.get("sso", {}), dict) else {}
    if bool(sso.get("enabled", False)):
        providers = sso.get("providers", []) if isinstance(sso.get("providers", []), list) else []
        derived["ENABLE_SSO"] = "true"
        derived["OIDC_PROVIDER_COUNT"] = str(len(providers))
        derived["OIDC_PROVIDER_NAMES"] = ",".join(
            p.get("name", "") for p in providers if isinstance(p, dict)
        )
        derived["OIDC_PROVIDERS_JSON"] = build_oidc_providers_json(providers)
    else:
        derived["ENABLE_SSO"] = "false"
        derived["OIDC_PROVIDERS_JSON"] = "[]"
        derived["OIDC_PROVIDER_COUNT"] = "0"
        derived["OIDC_PROVIDER_NAMES"] = ""

    derived["SHARED_REDIS_HOST"] = "matrix_redis"
    derived["SHARED_REDIS_PORT"] = "6379"
    derived["SHARED_REDIS_URL"] = "redis://matrix_redis:6379"

    derived["SERVER_IP"] = server_ip or detect_public_ip()

    # Explicit module desired-state flags for runtime orchestration.
    derived["HOOKSHOT_ENABLED"] = "true" if bool(modules.get("hookshot", {}).get("enabled", False)) else "false"
    derived["WHATSAPP_BRIDGE_ENABLED"] = (
        "true" if bool(modules.get("whatsapp_bridge", {}).get("enabled", False)) else "false"
    )
    derived["SLACK_BRIDGE_ENABLED"] = (
        "true" if bool(modules.get("slack_bridge", {}).get("enabled", False)) else "false"
    )

    hookshot_domain = modules.get("hookshot", {}).get("domain")
    derived["HOOKSHOT_DOMAIN"] = hookshot_domain or ""

    return derived


def load_secrets(ctx: ApplyContext) -> dict:
    secrets_file = ctx.state_dir / "secrets.yaml"
    if not secrets_file.exists():
        return {}

    with secrets_file.open() as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def generate_secret() -> str:
    return secrets.token_hex(32)


def create_or_update_secrets(ctx: ApplyContext, existing: dict, rotate: bool = False) -> dict:
    state = dict(existing)

    for key in DEFAULT_SECRET_KEYS:
        if rotate or not state.get(key):
            state[key] = generate_secret()

    # Static key kept for compatibility with current templates.
    state["LIVEKIT_KEY"] = "matrix"

    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    with (ctx.state_dir / "secrets.yaml").open("w") as f:
        yaml.safe_dump(state, f, default_flow_style=False, sort_keys=True)

    return state


def build_env_vars(config: dict, derived: dict, state_secrets: dict) -> dict:
    env_vars = {
        "MATRIX_DOMAIN": config["matrix"]["domain"],
        "SERVER_NAME": derived["SERVER_NAME"],
        "ADMIN_USERNAME": config["matrix"]["admin_username"],
    }
    env_vars.update(derived)
    env_vars.update(state_secrets)
    return env_vars


def write_env_file(ctx: ApplyContext, env_vars: dict) -> None:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# matrix-easy-deploy environment",
        f"# Generated by apply.py on {timestamp}",
        "# Keep this file private - it contains secrets.",
        "",
    ]
    for key in sorted(env_vars.keys()):
        lines.append(f"{key}={env_vars[key]}")
    lines.append("")

    ctx.env_file.write_text("\n".join(lines))
    ctx.env_file.chmod(0o600)


def render_template(src: Path, dest: Path, values: dict) -> None:
    content = src.read_text()
    for key, value in values.items():
        content = content.replace("{{" + key + "}}", str(value))
    dest.write_text(content)


def fail_if_unresolved_placeholder(path: Path) -> None:
    content = path.read_text()
    if "{{" in content:
        raise ValueError(f"{path} still contains unresolved template placeholders")


def render_templates(ctx: ApplyContext, env_vars: dict) -> None:
    install_element = env_vars.get("INSTALL_ELEMENT", "true") == "true"
    caddy_template = (
        ctx.project_root / "caddy" / ("Caddyfile.template" if install_element else "Caddyfile-no-element.template")
    )
    caddy_dest = ctx.project_root / "caddy" / "Caddyfile"
    render_template(caddy_template, caddy_dest, env_vars)
    fail_if_unresolved_placeholder(caddy_dest)

    synapse_template = ctx.project_root / "modules" / "core" / "synapse" / "homeserver.yaml.template"
    synapse_dest = ctx.project_root / "modules" / "core" / "synapse" / "homeserver.yaml"
    render_template(synapse_template, synapse_dest, env_vars)
    fail_if_unresolved_placeholder(synapse_dest)

    if install_element:
        element_template = ctx.project_root / "modules" / "core" / "element" / "config.json.template"
        element_dest = ctx.project_root / "modules" / "core" / "element" / "config.json"
        render_template(element_template, element_dest, env_vars)
        fail_if_unresolved_placeholder(element_dest)

    coturn_template = ctx.project_root / "modules" / "calls" / "coturn" / "turnserver.conf.template"
    coturn_dest = ctx.project_root / "modules" / "calls" / "coturn" / "turnserver.conf"
    render_template(coturn_template, coturn_dest, env_vars)
    fail_if_unresolved_placeholder(coturn_dest)

    livekit_template = ctx.project_root / "modules" / "calls" / "livekit" / "livekit.yaml.template"
    livekit_dest = ctx.project_root / "modules" / "calls" / "livekit" / "livekit.yaml"
    render_template(livekit_template, livekit_dest, env_vars)
    fail_if_unresolved_placeholder(livekit_dest)


def load_module_manifest(ctx: ApplyContext, module_dir_name: str) -> dict:
    manifest_path = ctx.project_root / "modules" / module_dir_name / "module.yaml"
    if not manifest_path.exists():
        return {}
    with manifest_path.open() as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def reconcile_module_state(ctx: ApplyContext, config: dict) -> None:
    modules_cfg = config.get("modules", {}) if isinstance(config.get("modules", {}), dict) else {}
    state = {}

    for config_key, dir_name in MODULE_CONFIG_KEY_TO_DIR.items():
        desired = modules_cfg.get(config_key, {}) if isinstance(modules_cfg.get(config_key, {}), dict) else {}
        enabled = bool(desired.get("enabled", False))
        manifest = load_module_manifest(ctx, dir_name)
        state[config_key] = {
            "enabled": enabled,
            "directory": dir_name,
            "manifest": manifest,
        }

    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    with (ctx.state_dir / "modules.yaml").open("w") as f:
        yaml.safe_dump(state, f, default_flow_style=False, sort_keys=True)


def module_env_overrides(config: dict, config_key: str) -> dict:
    matrix = config.get("matrix", {}) if isinstance(config.get("matrix", {}), dict) else {}
    modules = config.get("modules", {}) if isinstance(config.get("modules", {}), dict) else {}
    module_cfg = modules.get(config_key, {}) if isinstance(modules.get(config_key, {}), dict) else {}

    if config_key == "hookshot":
        return {
            "MODULE_HOOKSHOT_DOMAIN": str(module_cfg.get("domain", "")),
        }

    if config_key == "whatsapp_bridge":
        return {
            "MODULE_WA_ADMIN_USERNAME": str(module_cfg.get("admin_username", matrix.get("admin_username", "admin"))),
            "MODULE_WA_DB_NAME": str(module_cfg.get("db_name", "mautrix_whatsapp")),
        }

    if config_key == "slack_bridge":
        return {
            "MODULE_SL_ADMIN_USERNAME": str(module_cfg.get("admin_username", matrix.get("admin_username", "admin"))),
            "MODULE_SL_DB_NAME": str(module_cfg.get("db_name", "mautrix_slack")),
        }

    return {}


def reconcile_module_bootstrap(ctx: ApplyContext, config: dict) -> None:
    modules_cfg = config.get("modules", {}) if isinstance(config.get("modules", {}), dict) else {}

    for config_key, dir_name in MODULE_CONFIG_KEY_TO_DIR.items():
        desired = modules_cfg.get(config_key, {}) if isinstance(modules_cfg.get(config_key, {}), dict) else {}
        if not bool(desired.get("enabled", False)):
            continue

        manifest = load_module_manifest(ctx, dir_name)
        runtime = manifest.get("runtime", {}) if isinstance(manifest.get("runtime", {}), dict) else {}
        config_exists = runtime.get("config_exists")
        if not config_exists:
            continue

        config_path = ctx.project_root / str(config_exists)
        if config_path.exists():
            continue

        setup_script = ctx.project_root / "modules" / dir_name / "setup.sh"
        if not setup_script.exists():
            print(
                f"[WARN] Module '{config_key}' is enabled but setup script is missing ({setup_script}).",
                file=sys.stderr,
            )
            continue

        env = dict(os.environ)
        env["MED_NON_INTERACTIVE"] = "1"
        for key, value in module_env_overrides(config, config_key).items():
            env[key] = value

        subprocess.run(["bash", str(setup_script)], check=True, env=env)


def apply_configuration(
    ctx: ApplyContext,
    server_ip: str | None = None,
    rotate_secrets: bool = False,
    reconcile_modules: bool = True,
) -> None:
    config = load_config(ctx)
    validate_config(config)
    derived = derive_values(config, server_ip=server_ip)
    existing = load_secrets(ctx)
    saved = create_or_update_secrets(ctx, existing, rotate=rotate_secrets)
    env_vars = build_env_vars(config, derived, saved)
    write_env_file(ctx, env_vars)
    render_templates(ctx, env_vars)
    reconcile_module_state(ctx, config)
    if reconcile_modules:
        reconcile_module_bootstrap(ctx, config)


def run_runtime_reconcile(ctx: ApplyContext) -> None:
    # Reconcile running services to match current desired state.
    subprocess.run(["bash", str(ctx.project_root / "stop.sh")], check=True)
    subprocess.run(["bash", str(ctx.project_root / "start.sh")], check=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply matrix-easy-deploy configuration")
    parser.add_argument(
        "--project-root",
        default=None,
        help="Override project root (mainly for testing)",
    )
    parser.add_argument(
        "--server-ip",
        default=None,
        help="Override detected server IP (useful in CI or tests)",
    )
    parser.add_argument(
        "--rotate-secrets",
        action="store_true",
        help="Rotate generated secrets intentionally (destructive for existing deployments)",
    )
    parser.add_argument(
        "--reconcile-runtime",
        action="store_true",
        help="After apply, restart services (stop/start) to match desired runtime state",
    )
    parser.add_argument(
        "--skip-module-bootstrap",
        action="store_true",
        help="Do not run module setup scripts for enabled modules missing required generated config",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    project_root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parent.parent
    ctx = ApplyContext(project_root)

    apply_configuration(
        ctx,
        server_ip=args.server_ip,
        rotate_secrets=args.rotate_secrets,
        reconcile_modules=not args.skip_module_bootstrap,
    )
    if args.reconcile_runtime:
        run_runtime_reconcile(ctx)
    print("Configuration applied successfully.")
    print("Generated .env file and rendered templates.")
    if args.reconcile_runtime:
        print("Runtime reconciled via stop/start.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
