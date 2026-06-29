"""Build minimal fake project trees for apply/smoke tests."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

ProjectPreset = str  # "full" | "core_only"


def default_modules_config() -> dict[str, dict[str, Any]]:
    return {
        "hookshot": {"enabled": False, "domain": "hookshot.example.com"},
        "whatsapp_bridge": {"enabled": False},
        "slack_bridge": {"enabled": False},
    }


def default_deploy_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
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
            "local_login_enabled": True,
            "sso": {"enabled": False, "providers": []},
        },
        "modules": default_modules_config(),
    }
    for key, value in overrides.items():
        if key in cfg and isinstance(cfg[key], dict) and isinstance(value, dict):
            merged = deepcopy(cfg[key])
            merged.update(value)
            cfg[key] = merged
        else:
            cfg[key] = value
    return cfg


def write_deploy_config(root: Path, **overrides: Any) -> dict[str, Any]:
    cfg = default_deploy_config(**overrides)
    (root / "deploy.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return cfg


def _write_hookshot_module(root: Path) -> None:
    (root / "modules/hookshot").mkdir(parents=True, exist_ok=True)
    (root / "modules/hookshot/module.yaml").write_text(
        "name: hookshot\n"
        "config_key: hookshot\n"
        "generated_files:\n"
        "  - modules/hookshot/hookshot/config.yml\n"
        "  - modules/hookshot/hookshot/registration.yml\n"
        "runtime:\n"
        "  config_exists: modules/hookshot/hookshot/config.yml\n"
    )
    (root / "modules/hookshot/setup.sh").write_text("#!/usr/bin/env bash\nexit 0\n")


def _write_bridge_modules(root: Path) -> None:
    (root / "modules/whatsapp-bridge/whatsapp").mkdir(parents=True, exist_ok=True)
    (root / "modules/whatsapp-bridge/module.yaml").write_text(
        "name: whatsapp-bridge\n"
        "config_key: whatsapp_bridge\n"
        "generated_files:\n"
        "  - modules/whatsapp-bridge/whatsapp/config.yaml\n"
        "  - modules/whatsapp-bridge/whatsapp/registration.yaml\n"
        "runtime:\n"
        "  config_exists: modules/whatsapp-bridge/whatsapp/config.yaml\n"
    )
    (root / "modules/whatsapp-bridge/setup.sh").write_text("#!/usr/bin/env bash\nexit 0\n")

    (root / "modules/slack-bridge/slack").mkdir(parents=True, exist_ok=True)
    (root / "modules/slack-bridge/module.yaml").write_text(
        "name: slack-bridge\n"
        "config_key: slack_bridge\n"
        "generated_files:\n"
        "  - modules/slack-bridge/slack/config.yaml\n"
        "  - modules/slack-bridge/slack/registration.yaml\n"
        "runtime:\n"
        "  config_exists: modules/slack-bridge/slack/config.yaml\n"
    )
    (root / "modules/slack-bridge/setup.sh").write_text("#!/usr/bin/env bash\nexit 0\n")


def _write_core_templates(root: Path, *, full: bool) -> None:
    (root / "caddy").mkdir(parents=True, exist_ok=True)
    (root / "modules/core/synapse").mkdir(parents=True, exist_ok=True)
    (root / "modules/core/element").mkdir(parents=True, exist_ok=True)
    (root / "modules/core/synapse_data").mkdir(parents=True, exist_ok=True)
    (root / "modules/calls/coturn").mkdir(parents=True, exist_ok=True)
    (root / "modules/calls/livekit").mkdir(parents=True, exist_ok=True)

    if full:
        (root / "caddy/Caddyfile.template").write_text(
            "{{CADDY_MATRIX_HOSTS}} {\n"
            "{{CADDY_MAS_BLOCK}}"
            "    reverse_proxy {{HOMESERVER_UPSTREAM}}\n"
            "{{CADDY_SYNAPSE_ADMIN_BLOCK}}"
            "}\n\n"
            "{{LIVEKIT_DOMAIN}} {\n"
            "    handle_path /livekit/jwt* {\n"
            "        reverse_proxy matrix_lk_jwt_service:8080\n"
            "    }\n"
            "    handle_path /livekit/sfu* {\n"
            "        reverse_proxy host.docker.internal:7880\n"
            "    }\n"
            "}\n"
        )
        (root / "modules/core/synapse/homeserver.yaml.template").write_text(
            "server_name: {{SERVER_NAME}}\n"
            "public_baseurl: https://{{MATRIX_DOMAIN}}\n"
            "login_via_existing_session:\n"
            "  enabled: {{LOGIN_VIA_EXISTING_SESSION_ENABLED}}\n"
            "password_config:\n  enabled: {{LOCAL_LOGIN_ENABLED}}\n"
            "oidc_providers: {{OIDC_PROVIDERS_JSON}}\n"
            "extra_well_known_client_content:\n"
            "  org.matrix.msc4143.rtc_foci:\n"
            "    - type: livekit\n"
            "      livekit_service_url: \"https://{{LIVEKIT_DOMAIN}}/livekit/jwt\"\n"
            "{{SYNAPSE_MAS_WELL_KNOWN_SECTION}}\n"
            "experimental_features:\n"
            "  msc3266_enabled: true\n"
            "{{SYNAPSE_MAS_EXPERIMENTAL_SECTION}}\n"
            "matrix_rtc:\n"
            "  transports:\n"
            "    - type: livekit\n"
            "      livekit_service_url: \"https://{{LIVEKIT_DOMAIN}}/livekit/jwt\"\n"
        )
    else:
        (root / "caddy/Caddyfile.template").write_text("{{MATRIX_DOMAIN}} {{CADDY_MATRIX_HOSTS}}\n")
        (root / "modules/core/synapse/homeserver.yaml.template").write_text(
            "server_name: {{SERVER_NAME}}\npublic_baseurl: https://{{MATRIX_DOMAIN}}\n"
        )

    (root / "modules/mas").mkdir(parents=True, exist_ok=True)
    (root / "modules/mas/config.yaml.template").write_text(
        "public_base: {{MAS_PUBLIC_BASE}}\n"
        "passwords:\n  enabled: {{MAS_LOCAL_LOGIN_ENABLED}}\n"
        "{{MAS_SIGNING_KEYS_YAML}}"
        "{{MAS_UPSTREAM_OAUTH2_YAML}}"
    )
    (root / "modules/mas/setup.sh").write_text("#!/usr/bin/env bash\nexit 0\n")

    (root / "caddy/Caddyfile-no-element.template").write_text(
        "no-element {{MATRIX_DOMAIN}}" if full else "no-element {{MATRIX_DOMAIN}}\n"
    )
    element_template = (
        '{"base_url":"https://{{MATRIX_DOMAIN}}"}'
        if full
        else '{"base_url":"https://{{MATRIX_DOMAIN}}"}\n'
    )
    (root / "modules/core/element/config.json.template").write_text(element_template)
    coturn_template = "realm={{MATRIX_DOMAIN}}" if full else "realm={{MATRIX_DOMAIN}}\n"
    (root / "modules/calls/coturn/turnserver.conf.template").write_text(coturn_template)
    if full:
        livekit_template = (
            "room:\n  auto_create: false\n"
            "keys:\n  {{LIVEKIT_KEY}}: {{LIVEKIT_SECRET}}"
        )
    else:
        livekit_template = "keys:\n  {{LIVEKIT_KEY}}: {{LIVEKIT_SECRET}}\n"
    (root / "modules/calls/livekit/livekit.yaml.template").write_text(livekit_template)


def _write_full_only_templates(root: Path) -> None:
    (root / "modules/core/tuwunel").mkdir(parents=True, exist_ok=True)
    (root / "modules/core/tuwunel_data").mkdir(parents=True, exist_ok=True)
    (root / "modules/core/tuwunel/tuwunel.toml.template").write_text(
        "server_name = \"{{SERVER_NAME}}\"\n"
        "allow_registration = {{TUWUNEL_ALLOW_REGISTRATION}}\n"
        "{{TUWUNEL_AUTO_JOIN_SECTION}}\n"
    )
    _write_bridge_modules(root)


def build_minimal_project(root: Path, *, preset: ProjectPreset = "full") -> Path:
    """Create a minimal deploy tree under root.

    Presets:
      - full: all module dirs and templates used by apply tests
      - core_only: slimmer tree for smoke workflow tests
    """
    full = preset == "full"
    _write_core_templates(root, full=full)
    _write_hookshot_module(root)
    if full:
        _write_full_only_templates(root)
    return root
