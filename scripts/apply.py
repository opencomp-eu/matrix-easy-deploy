#!/usr/bin/env python3
# scripts/apply.py — Shared config/state engine for matrix-easy-deploy

import argparse
import datetime as dt
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

try:
    from scripts import synapse_appservice
    from scripts import tuwunel_appservice
    from scripts import hookshot_caddy
    from scripts import homeserver
    from scripts import mas_config
    from scripts import bridge_config_patch
    from scripts import bridge_registration_patch
except ModuleNotFoundError:
    # When run as scripts/apply.py, Python may not include project root in sys.path.
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from scripts import synapse_appservice
    from scripts import tuwunel_appservice
    from scripts import hookshot_caddy
    from scripts import homeserver
    from scripts import mas_config
    from scripts import bridge_config_patch
    from scripts import bridge_registration_patch

# Shared backup timer reconciliation lives in easydeploy-lib.
_EASYDEPLOY_LIB_PYTHON = Path(__file__).resolve().parent.parent / "easydeploy-lib" / "python"
if _EASYDEPLOY_LIB_PYTHON.is_dir() and str(_EASYDEPLOY_LIB_PYTHON) not in sys.path:
    sys.path.insert(0, str(_EASYDEPLOY_LIB_PYTHON))
import backup_schedule  # noqa: E402  (easydeploy-lib/python/backup_schedule.py)


DEFAULT_SECRET_KEYS = [
    "POSTGRES_PASSWORD",
    "REGISTRATION_SHARED_SECRET",
    "MACAROON_SECRET_KEY",
    "FORM_SECRET",
    "COTURN_SECRET",
    "LIVEKIT_SECRET",
    "WA_DB_PASSWORD",
    "SL_DB_PASSWORD",
    "MAS_DB_PASSWORD",
    "MAS_HOMESERVER_SECRET",
    "MAS_SYNAPSE_CLIENT_SECRET",
]

# Operator-managed keys written by tooling outside apply's env_vars; never drop on re-apply.
PRESERVED_ENV_KEYS = frozenset(
    {
        "MED_ADMIN_USERNAME",
        "MED_ADMIN_PASSWORD",
    }
)

# Template-only or secrets.yaml-backed values — never persist in shell-sourced .env.
ENV_FILE_EXCLUDED_KEYS = frozenset(
    {
        "MAS_SIGNING_KEYS",
        "MAS_SIGNING_KEYS_YAML",
        "MAS_UPSTREAM_OAUTH2_YAML",
        "SYNAPSE_MAS_EXPERIMENTAL_SECTION",
        "SYNAPSE_MAS_AUTH_SERVICE_SECTION",
        "SYNAPSE_MAS_WELL_KNOWN_SECTION",
        "SYNAPSE_AUTO_JOIN_SECTION",
        "SYNAPSE_GUEST_FEDERATION_SECTION",
        "TUWUNEL_AUTO_JOIN_SECTION",
    }
)

SCALAR_INTEGRATIONS_UI_URL = "https://scalar.vector.im/"
SCALAR_INTEGRATIONS_REST_URL = "https://scalar.vector.im/api"
SCALAR_INTEGRATIONS_WIDGETS_URLS = [
    "https://scalar.vector.im/_matrix/integrations/v1",
    "https://scalar.vector.im/api",
    "https://scalar-staging.vector.im/_matrix/integrations/v1",
    "https://scalar-staging.vector.im/api",
    "https://scalar-staging.riot.im/_matrix/integrations/v1",
    "https://scalar-staging.riot.im/api",
]

ELEMENT_STRING_KEYS = {
    "brand",
    "default_theme",
    "permalink_prefix",
    "default_device_display_name",
    "default_country_code",
    "logout_redirect_url",
    "help_url",
    "help_encryption_url",
    "help_key_storage_url",
}

ELEMENT_BOOL_KEYS = {
    "mobile_guide_toast",
    "disable_custom_urls",
    "disable_guests",
    "disable_3pid_login",
    "disable_login_language_selector",
}

ELEMENT_UI_FEATURES = {
    "feedback": "UIFeature.feedback",
    "widgets": "UIFeature.widgets",
    "voip": "UIFeature.voip",
    "advanced_settings": "UIFeature.advancedSettings",
    "share_qr_code": "UIFeature.shareQrCode",
    "share_social": "UIFeature.shareSocial",
    "identity_server": "UIFeature.identityServer",
    "third_party_id": "UIFeature.thirdPartyId",
    "registration": "UIFeature.registration",
    "password_reset": "UIFeature.passwordReset",
    "deactivate": "UIFeature.deactivate",
    "advanced_encryption": "UIFeature.advancedEncryption",
    "room_history_settings": "UIFeature.roomHistorySettings",
    "location_sharing": "UIFeature.locationSharing",
    "create_public_rooms": "UIFeature.allowCreatingPublicRooms",
    "create_public_spaces": "UIFeature.allowCreatingPublicSpaces",
}

MODULE_CONFIG_KEY_TO_DIR = {
    "hookshot": "hookshot",
    "whatsapp_bridge": "whatsapp-bridge",
    "slack_bridge": "slack-bridge",
}

MODULES_NEEDING_POSTGRES = frozenset({"whatsapp_bridge", "slack_bridge"})

BRIDGE_CONFIG_PATHS = {
    "whatsapp_bridge": "modules/whatsapp-bridge/whatsapp/config.yaml",
    "slack_bridge": "modules/slack-bridge/slack/config.yaml",
}

def bridge_appservice_specs(spec: homeserver.HomeserverSpec) -> dict[str, dict[str, str]]:
    appservice_data = spec.appservice_data_rel
    return {
      "hookshot": {
          "registration_src": "modules/hookshot/hookshot/registration.yml",
          "registration_dest": f"{appservice_data}/hookshot-registration.yml",
          "registration_filename": "hookshot-registration.yml",
          "registration_path": "/data/hookshot-registration.yml",
      },
      "whatsapp_bridge": {
          "registration_src": "modules/whatsapp-bridge/whatsapp/registration.yaml",
          "registration_dest": f"{appservice_data}/whatsapp-registration.yaml",
          "registration_filename": "whatsapp-registration.yaml",
          "registration_path": "/data/whatsapp-registration.yaml",
      },
      "slack_bridge": {
          "registration_src": "modules/slack-bridge/slack/registration.yaml",
          "registration_dest": f"{appservice_data}/slack-registration.yaml",
          "registration_filename": "slack-registration.yaml",
          "registration_path": "/data/slack-registration.yaml",
      },
  }


class ApplyContext:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.config_file = project_root / "deploy.yaml"
        self.state_dir = project_root / ".matrix-easy-deploy"
        self.env_file = project_root / ".env"

    @property
    def integration_dir(self) -> Path:
        return self.state_dir / "integration"

    @property
    def integration_caddy_fragment(self) -> Path:
        return self.integration_dir / "caddy.caddy"

    @property
    def engine_caddy_overlay(self) -> Path:
        return self.integration_dir / "engine-caddy.yml"

    @property
    def integrate_compose_dir(self) -> Path:
        return self.state_dir / "compose"


DEFAULT_INTEGRATE_NETWORK = "easydeploy-net"


def proxy_mode(config: dict) -> str:
    mode = str((config.get("proxy") or {}).get("mode") or "standalone").strip().lower()
    if mode not in {"standalone", "integrate"}:
        raise ValueError("proxy.mode must be 'standalone' or 'integrate'")
    return mode


def integrate_network_name(config: dict) -> str:
    integrate = (config.get("proxy") or {}).get("integrate") or {}
    if not isinstance(integrate, dict):
        return DEFAULT_INTEGRATE_NETWORK
    name = str(integrate.get("network") or DEFAULT_INTEGRATE_NETWORK).strip()
    return name or DEFAULT_INTEGRATE_NETWORK


def guest_caddy_alias_hosts(config: dict, derived: dict) -> list[str]:
    if derived.get("GUEST_ACCESS_ENABLED") != "true":
        return []
    matrix = config.get("matrix") if isinstance(config.get("matrix"), dict) else {}
    hosts: list[str] = []
    for host in (
        derived.get("GUEST_SERVER_NAME", ""),
        derived.get("SERVER_NAME", ""),
        str(matrix.get("domain") or ""),
    ):
        value = str(host or "").strip()
        if value and value not in hosts:
            hosts.append(value)
    return hosts


def render_integration_fragment(ctx: ApplyContext) -> None:
    caddyfile = ctx.project_root / "caddy" / "Caddyfile"
    if not caddyfile.is_file():
        raise FileNotFoundError(f"Missing Caddyfile at {caddyfile} (needed for integrate fragment)")
    ctx.integration_dir.mkdir(parents=True, exist_ok=True)
    body = caddyfile.read_text().strip()
    ctx.integration_caddy_fragment.write_text(f"# matrix-easy-deploy\n{body}\n")


def render_engine_caddy_overlay(ctx: ApplyContext, config: dict, derived: dict) -> None:
    """Attach easydeploy_caddy to caddy_net (guest aliases + LiveKit host-gateway)."""
    aliases = guest_caddy_alias_hosts(config, derived)
    caddy_net: dict[str, Any] = {}
    if aliases:
        caddy_net["aliases"] = aliases
    network = integrate_network_name(config)
    overlay = {
        "services": {
            "caddy": {
                "extra_hosts": ["host.docker.internal:host-gateway"],
                "networks": {
                    network: {},
                    "caddy_net": caddy_net,
                },
            }
        },
        "networks": {
            "caddy_net": {"external": True, "name": "caddy_net"},
            network: {"external": True, "name": network},
        },
    }
    ctx.integration_dir.mkdir(parents=True, exist_ok=True)
    with ctx.engine_caddy_overlay.open("w") as handle:
        yaml.safe_dump(overlay, handle, default_flow_style=False, sort_keys=False)


def _network_overlay(services: dict[str, Any], network: str) -> dict[str, Any]:
    return {
        "services": services,
        "networks": {network: {"external": True, "name": network}},
    }


def render_integrate_compose_overlays(ctx: ApplyContext, config: dict) -> None:
    """Join public Matrix backends to easydeploy-net; keep caddy_net for the internal mesh."""
    network = integrate_network_name(config)
    compose_dir = ctx.integrate_compose_dir
    compose_dir.mkdir(parents=True, exist_ok=True)

    dual = [network, "caddy_net"]
    dual_internal = ["matrix_internal", "caddy_net", network]

    core = _network_overlay(
        {
            "redis": {"networks": list(dual_internal)},
            "synapse": {"networks": list(dual_internal)},
            "tuwunel": {"networks": list(dual_internal)},
            "element": {"networks": list(dual)},
        },
        network,
    )
    mas = {
        "services": {
            "mas": {
                "networks": {
                    "matrix_internal": {"aliases": ["matrix-mas"]},
                    "caddy_net": {"aliases": ["matrix-mas"]},
                    network: {"aliases": ["matrix-mas"]},
                }
            }
        },
        "networks": {network: {"external": True, "name": network}},
    }
    calls = _network_overlay(
        {
            "lk-jwt-service": {"networks": list(dual)},
            "guest-tuwunel": {"networks": list(dual)},
            "element-call": {"networks": list(dual)},
        },
        network,
    )
    hookshot = _network_overlay({"hookshot": {"networks": list(dual)}}, network)
    whatsapp = _network_overlay(
        {"mautrix-whatsapp": {"networks": ["caddy_net", "matrix_internal", network]}},
        network,
    )
    slack = _network_overlay(
        {"mautrix-slack": {"networks": ["caddy_net", "matrix_internal", network]}},
        network,
    )

    dumps = {
        "core.yml": core,
        "mas.yml": mas,
        "calls.yml": calls,
        "hookshot.yml": hookshot,
        "whatsapp.yml": whatsapp,
        "slack.yml": slack,
    }
    for name, payload in dumps.items():
        with (compose_dir / name).open("w") as handle:
            yaml.safe_dump(payload, handle, default_flow_style=False, sort_keys=False)


def clear_integrate_artifacts(ctx: ApplyContext) -> None:
    for path in (ctx.integration_caddy_fragment, ctx.engine_caddy_overlay):
        if path.is_file():
            path.unlink()
    compose_dir = ctx.integrate_compose_dir
    if compose_dir.is_dir():
        for child in compose_dir.glob("*.yml"):
            child.unlink()


def reconcile_proxy_integration(ctx: ApplyContext, config: dict, derived: dict) -> None:
    if proxy_mode(config) != "integrate":
        clear_integrate_artifacts(ctx)
        return
    render_integration_fragment(ctx)
    render_engine_caddy_overlay(ctx, config, derived)
    render_integrate_compose_overlays(ctx, config)
    print(f"Proxy mode: integrate (Caddy fragment: {ctx.integration_caddy_fragment})")
    print("Run easydeploy-engine apply.sh after enabling Matrix in engine.yaml.")


def load_env_map(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        return {}

    values: dict[str, str] = {}
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value
    return values


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

    features = config.get("features")
    if isinstance(features, dict):
        warnings = mas_config.migrate_legacy_mas_features(features)
        mas_config.emit_migration_warnings(warnings)

    apply_engine_embed_sidecar(config, ctx.integration_dir / "embed.yaml")
    return config


def oidc_provider_sidecar_path(ctx: ApplyContext) -> Path:
    return ctx.integration_dir / "oidc-provider.yaml"


def apply_engine_embed_sidecar(config: dict, sidecar_path: Path | None = None) -> None:
    """Merge extra frame-ancestors from the engine sidecar (Bulwark webmail)."""
    embed = config.get("embed")
    if embed is None:
        embed = {}
    elif not isinstance(embed, dict):
        return
    if mas_config.managed_is_false(embed):
        return
    extra: list[Any] = []
    if sidecar_path is not None and sidecar_path.is_file():
        sidecar = yaml.safe_load(sidecar_path.read_text()) or {}
        if isinstance(sidecar, dict):
            extra = sidecar.get("frame_ancestors") or []
    current = embed.get("frame_ancestors") or []
    merged = mas_config.unique_https_origins(list(current) + list(extra))
    if merged:
        target = config.setdefault("embed", {})
        if isinstance(target, dict):
            target["frame_ancestors"] = merged


def extra_frame_ancestors(config: dict) -> list[str]:
    embed = config.get("embed") or {}
    if not isinstance(embed, dict):
        return []
    return mas_config.unique_https_origins(embed.get("frame_ancestors"))


def provision_local_kanidm_oidc(ctx: ApplyContext, config: dict) -> list[str]:
    """Write Kanidm + MAS sidecars when Kanidm is a sibling (standalone path)."""
    notes: list[str] = []
    features = config.get("features") if isinstance(config.get("features"), dict) else {}
    sso = features.get("sso") if isinstance(features.get("sso"), dict) else {}
    if mas_config.managed_is_false(sso):
        return notes
    provider = str(sso.get("provider") or "").strip().lower()
    if provider and provider not in mas_config.LOCAL_IDP_PROVIDERS:
        return notes

    sidecar_path = oidc_provider_sidecar_path(ctx)
    if sidecar_path.is_file():
        return notes

    wants_kanidm = provider in mas_config.LOCAL_IDP_PROVIDERS
    providers = sso.get("providers") if isinstance(sso.get("providers"), list) else []
    if not wants_kanidm:
        if not bool(sso.get("enabled")):
            return notes
        if providers and not any(mas_config.is_kanidm_sso_provider(item) for item in providers):
            return notes
        if providers and any(
            isinstance(item, dict)
            and mas_config.is_kanidm_sso_provider(item)
            and str(item.get("client_secret") or "").strip()
            for item in providers
        ):
            return notes

    from scripts.config_edit import discover_local_kanidm

    found = discover_local_kanidm(ctx.project_root)
    kanidm_domain = str(found.get("domain") or "").strip()
    kanidm_deploy = str(found.get("deploy") or "").strip()
    if not kanidm_domain:
        if wants_kanidm:
            notes.append(
                "features.sso.provider is kanidm but no local Kanidm was found. "
                "The engine sidecar, or a sibling kanidm-easy-deploy checkout, is required."
            )
        return notes

    matrix = config.get("matrix") if isinstance(config.get("matrix"), dict) else {}
    matrix_domain = str(matrix.get("domain") or "").strip()
    if not matrix_domain or matrix_domain == "matrix.example.com":
        return notes

    element = features.get("element") if isinstance(features.get("element"), dict) else {}
    element_domain = ""
    if bool(element.get("enabled", True)):
        element_domain = str(element.get("domain") or matrix_domain).strip()

    client_id = mas_config.DEFAULT_MATRIX_OIDC_CLIENT_ID
    kanidm_root = Path(kanidm_deploy).parent if kanidm_deploy else None
    kanidm_sidecar = (
        kanidm_root / ".kanidm-easy-deploy" / "integration" / "oidc-clients.d" / f"{client_id}.yaml"
        if kanidm_root is not None
        else Path()
    )
    existing_secret = mas_config.plaintext_oidc_secret(
        str(
            (
                (yaml.safe_load(sidecar_path.read_text()) or {})
                if sidecar_path.is_file()
                else {}
            ).get("client_secret")
            or ""
        )
    )
    if existing_secret.startswith("$"):
        existing_secret = ""
    mas_config.write_oidc_sidecar(
        sidecar_path,
        mas_config.build_mas_kanidm_provider(
            kanidm_domain=kanidm_domain,
            client_id=client_id,
            client_secret=existing_secret,
        ),
    )
    notes.append(f"Wrote Matrix Kanidm OIDC sidecar ({sidecar_path}).")
    if kanidm_root is not None and kanidm_root.is_dir():
        mas_config.write_oidc_sidecar(
            kanidm_sidecar,
            mas_config.build_kanidm_matrix_client(
                matrix_domain=matrix_domain,
                kanidm_domain=kanidm_domain,
                client_id=client_id,
                element_domain=element_domain,
            ),
            header=mas_config.KANIDM_CLIENT_SIDECAR_HEADER,
        )
        notes.append(
            f"Wrote Kanidm client sidecar ({kanidm_sidecar}). "
            "Re-run bash apply.sh in kanidm-easy-deploy if Kanidm is already deployed, "
            "then re-apply Matrix so MAS receives the client secret."
        )
    else:
        redirect = mas_config.matrix_kanidm_redirect_uri(matrix_domain, kanidm_domain)
        notes.append(
            "No sibling Kanidm checkout found. Register a confidential OIDC client on Kanidm "
            f"with redirect URI {redirect} and client_id {client_id}."
        )
    return notes


def prepare_sso_config(ctx: ApplyContext, config: dict) -> list[str]:
    """Assign Kanidm sidecars (if needed) and merge them in memory for this apply."""
    notes = provision_local_kanidm_oidc(ctx, config)
    mas_config.apply_engine_oidc_sidecar(config, oidc_provider_sidecar_path(ctx))
    return notes


def save_config(ctx: ApplyContext, config: dict) -> None:
    with ctx.config_file.open("w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)


def _require_bool(value, path: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be true/false")


def _require_str(value, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")


def _require_str_list(value, path: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{path} must be a list of non-empty strings")


def _require_link_list(value, path: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{path}[{index}] must be an object")
        _require_str(item.get("text"), f"{path}[{index}].text")
        _require_str(item.get("url"), f"{path}[{index}].url")


def _require_bool_map(value, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{path} keys must be non-empty strings")
        _require_bool(item, f"{path}.{key}")


def validate_element_config(element: dict) -> None:
    for key in ("enabled",):
        if key in element:
            _require_bool(element.get(key), f"features.element.{key}")

    if "domain" in element and element.get("domain") not in (None, ""):
        _require_str(element.get("domain"), "features.element.domain")

    for key in ELEMENT_STRING_KEYS:
        if key not in element:
            continue
        _require_str(element.get(key), f"features.element.{key}")

    for key in ELEMENT_BOOL_KEYS:
        if key in element:
            _require_bool(element.get(key), f"features.element.{key}")

    branding = element.get("branding")
    if branding is not None:
        if not isinstance(branding, dict):
            raise ValueError("features.element.branding must be an object")
        if "auth_header_logo_url" in branding:
            _require_str(branding.get("auth_header_logo_url"), "features.element.branding.auth_header_logo_url")
        if "logo_link_url" in branding:
            _require_str(branding.get("logo_link_url"), "features.element.branding.logo_link_url")
        if "welcome_background_url" in branding:
            welcome_background = branding.get("welcome_background_url")
            if isinstance(welcome_background, str):
                _require_str(welcome_background, "features.element.branding.welcome_background_url")
            else:
                _require_str_list(welcome_background, "features.element.branding.welcome_background_url")
        if "auth_footer_links" in branding:
            _require_link_list(branding.get("auth_footer_links"), "features.element.branding.auth_footer_links")

    embedded_pages = element.get("embedded_pages")
    if embedded_pages is not None:
        if not isinstance(embedded_pages, dict):
            raise ValueError("features.element.embedded_pages must be an object")
        for key in ("home_url", "welcome_url"):
            if key in embedded_pages:
                _require_str(embedded_pages.get(key), f"features.element.embedded_pages.{key}")
        if "login_for_welcome" in embedded_pages:
            _require_bool(embedded_pages.get("login_for_welcome"), "features.element.embedded_pages.login_for_welcome")

    sso_redirect_options = element.get("sso_redirect_options")
    if sso_redirect_options is not None:
        if sso_redirect_options is False:
            pass
        elif not isinstance(sso_redirect_options, dict):
            raise ValueError("features.element.sso_redirect_options must be an object or false")
        else:
            for key in ("immediate", "on_welcome_page", "on_login_page"):
                if key in sso_redirect_options:
                    _require_bool(sso_redirect_options.get(key), f"features.element.sso_redirect_options.{key}")

    integrations = element.get("integrations")
    if integrations is not None:
        if not isinstance(integrations, dict):
            raise ValueError("features.element.integrations must be an object")
        if "enabled" in integrations:
            _require_bool(integrations.get("enabled"), "features.element.integrations.enabled")
        if "ui_url" in integrations:
            _require_str(integrations.get("ui_url"), "features.element.integrations.ui_url")
        if "rest_url" in integrations:
            _require_str(integrations.get("rest_url"), "features.element.integrations.rest_url")
        if "widgets_urls" in integrations:
            _require_str_list(integrations.get("widgets_urls"), "features.element.integrations.widgets_urls")

    room_directory = element.get("room_directory")
    if room_directory is not None:
        if not isinstance(room_directory, dict):
            raise ValueError("features.element.room_directory must be an object")
        if "servers" in room_directory:
            _require_str_list(room_directory.get("servers"), "features.element.room_directory.servers")

    labs = element.get("labs")
    if labs is not None:
        if not isinstance(labs, dict):
            raise ValueError("features.element.labs must be an object")
        if "show_settings" in labs:
            _require_bool(labs.get("show_settings"), "features.element.labs.show_settings")
        if "features" in labs:
            _require_bool_map(labs.get("features"), "features.element.labs.features")

    ui_features = element.get("ui_features")
    if ui_features is not None:
        _require_bool_map(ui_features, "features.element.ui_features")

    notice = element.get("notice")
    if notice is not None:
        if not isinstance(notice, dict):
            raise ValueError("features.element.notice must be an object")
        for key in ("title", "description"):
            if key in notice:
                _require_str(notice.get(key), f"features.element.notice.{key}")
        if "show_once" in notice:
            _require_bool(notice.get("show_once"), "features.element.notice.show_once")

    terms_and_conditions = element.get("terms_and_conditions")
    if terms_and_conditions is not None:
        if not isinstance(terms_and_conditions, dict):
            raise ValueError("features.element.terms_and_conditions must be an object")
        if "links" in terms_and_conditions:
            _require_link_list(terms_and_conditions.get("links"), "features.element.terms_and_conditions.links")

    report_event = element.get("report_event")
    if report_event is not None:
        if not isinstance(report_event, dict):
            raise ValueError("features.element.report_event must be an object")
        if "admin_message_md" in report_event:
            _require_str(report_event.get("admin_message_md"), "features.element.report_event.admin_message_md")

    bug_report = element.get("bug_report")
    if bug_report is not None:
        if not isinstance(bug_report, dict):
            raise ValueError("features.element.bug_report must be an object")
        for key in ("endpoint_url", "existing_issues_url", "new_issue_url"):
            if key in bug_report:
                _require_str(bug_report.get(key), f"features.element.bug_report.{key}")
        sentry = bug_report.get("sentry")
        if sentry is not None:
            if not isinstance(sentry, dict):
                raise ValueError("features.element.bug_report.sentry must be an object")
            if "dsn" in sentry:
                _require_str(sentry.get("dsn"), "features.element.bug_report.sentry.dsn")
            if "environment" in sentry:
                _require_str(sentry.get("environment"), "features.element.bug_report.sentry.environment")

    extra_config = element.get("extra_config")
    if extra_config is not None and not isinstance(extra_config, dict):
        raise ValueError("features.element.extra_config must be an object")


def validate_embed_config(embed: dict) -> None:
    ancestors = embed.get("frame_ancestors")
    if ancestors is None:
        return
    if isinstance(ancestors, str):
        ancestors = [ancestors]
    if not isinstance(ancestors, list) or any(not str(item or "").strip() for item in ancestors):
        raise ValueError("embed.frame_ancestors must be a list of hostnames or https origins")


AUTO_JOIN_ROOM_OBJECT_KEYS = frozenset({"alias", "name", "topic", "message", "handover", "federated"})


def normalize_auto_join_room_alias(raw: str, server_name: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("#") and ":" in cleaned:
        return cleaned
    localpart = cleaned.lstrip("#").split(":", 1)[0]
    if not localpart:
        raise ValueError(f"Invalid room alias: {raw!r}")
    return f"#{localpart}:{server_name}"


def validate_auto_join_room_entry(entry: object, path: str) -> None:
    if isinstance(entry, str):
        if not entry.strip():
            raise ValueError(f"{path} must be a non-empty string")
        return
    if isinstance(entry, dict):
        _require_str(entry.get("alias"), f"{path}.alias")
        for key in ("name", "topic", "message"):
            if key in entry and entry[key] is not None and not isinstance(entry[key], str):
                raise ValueError(f"{path}.{key} must be a string")
        if "handover" in entry and entry["handover"] is not None:
            _require_str_list(entry.get("handover"), f"{path}.handover")
        if "federated" in entry and entry["federated"] is not None:
            _require_bool(entry.get("federated"), f"{path}.federated")
        unknown = set(entry) - AUTO_JOIN_ROOM_OBJECT_KEYS
        if unknown:
            raise ValueError(f"{path} has unknown keys: {', '.join(sorted(unknown))}")
        return
    raise ValueError(f"{path} must be a string alias or an object with alias")


def parse_auto_join_room_entry(entry: str | dict, server_name: str) -> dict[str, Any]:
    if isinstance(entry, str):
        return {
            "alias": normalize_auto_join_room_alias(entry, server_name),
            "name": "",
            "topic": "",
            "message": "",
            "handover": [],
            "federated": False,
        }
    handover = entry.get("handover") or []
    if not isinstance(handover, list):
        handover = []
    federated = entry.get("federated", False)
    if federated is None:
        federated = False
    return {
        "alias": normalize_auto_join_room_alias(entry["alias"], server_name),
        "name": entry.get("name") or "",
        "topic": entry.get("topic") or "",
        "message": entry.get("message") or "",
        "handover": [str(u) for u in handover],
        "federated": bool(federated),
    }


def auto_join_room_aliases(rooms: list, server_name: str) -> list[str]:
    return [parse_auto_join_room_entry(entry, server_name)["alias"] for entry in rooms]


def get_auto_join_config(features: dict) -> dict:
    auto_join = features.get("auto_join", {}) if isinstance(features.get("auto_join", {}), dict) else {}
    return auto_join


def get_calls_config(features: dict) -> dict:
    calls = features.get("calls", {}) if isinstance(features.get("calls", {}), dict) else {}
    return calls


def get_guest_access_config(calls: dict) -> dict:
    guest = calls.get("guest_access", {}) if isinstance(calls.get("guest_access", {}), dict) else {}
    return guest


def guest_access_enabled(calls: dict) -> bool:
    return bool(get_guest_access_config(calls).get("enabled", False))


def format_compose_extra_hosts(*hostnames: str) -> str:
    """YAML extra_hosts lines for docker compose (deduplicated, stable order)."""
    seen: list[str] = []
    for hostname in hostnames:
        host = (hostname or "").strip()
        if host and host not in seen:
            seen.append(host)
    return "\n".join(f'      "{host}": "host-gateway"' for host in seen)


def format_caddy_guest_network_aliases(*hostnames: str) -> str:
    """YAML network alias lines for Caddy on caddy_net (deduplicated)."""
    seen: list[str] = []
    for hostname in hostnames:
        host = (hostname or "").strip()
        if host and host not in seen:
            seen.append(host)
    return "\n".join(f'          - "{host}"' for host in seen)


def resolve_guest_access_values(config: dict) -> dict[str, str]:
    matrix = config.get("matrix", {}) if isinstance(config.get("matrix", {}), dict) else {}
    features = config.get("features", {}) if isinstance(config.get("features", {}), dict) else {}
    calls = get_calls_config(features)
    guest = get_guest_access_config(calls)

    matrix_domain = str(matrix.get("domain", ""))
    server_name = str(matrix.get("server_name") or extract_base_domain(matrix_domain))
    base_domain = extract_base_domain(matrix_domain)

    guest_server_name = str(guest.get("server_name") or f"guest.{base_domain}").strip()
    guest_call_domain = str(guest.get("domain") or f"call.{base_domain}").strip()

    # Tuwunel's federation allow-list must include this server itself (issue #489) as well
    # as the principal homeserver, or outbound join/send fails after make_join.
    federation_peers: list[str] = []
    for peer in (server_name, guest_server_name):
        if peer and peer not in federation_peers:
            federation_peers.append(peer)
    if len(federation_peers) == 1:
        federation_regex = re.escape(federation_peers[0])
    else:
        federation_regex = "(?:" + "|".join(re.escape(peer) for peer in federation_peers) + ")"

    return {
        "GUEST_SERVER_NAME": guest_server_name,
        "GUEST_CALL_DOMAIN": guest_call_domain,
        "GUEST_FEDERATION_ALLOW_REGEX": federation_regex,
    }


def validate_calls_guest_access(config: dict) -> None:
    features = config.get("features", {}) if isinstance(config.get("features", {}), dict) else {}
    calls = get_calls_config(features)
    guest = get_guest_access_config(calls)

    if not guest:
        return

    unknown = set(guest) - {"enabled", "domain", "server_name", "matrix_domain"}
    if unknown:
        raise ValueError(
            f"features.calls.guest_access has unknown keys: {', '.join(sorted(unknown))}"
        )

    if "matrix_domain" in guest:
        # Legacy key — guest server_name now covers both MXIDs and the client API.
        pass

    if "enabled" in guest:
        _require_bool(guest.get("enabled"), "features.calls.guest_access.enabled")

    if not guest_access_enabled(calls):
        return

    if not calls.get("enabled", True):
        raise ValueError("features.calls.guest_access.enabled requires features.calls.enabled=true")

    if not bool(features.get("federation_enabled", True)):
        raise ValueError(
            "features.calls.guest_access.enabled requires features.federation_enabled=true"
        )

    if bool(features.get("registration_enabled", False)):
        raise ValueError(
            "features.calls.guest_access.enabled is incompatible with features.registration_enabled=true"
        )

    for key in ("domain", "server_name"):
        if key in guest and guest[key] is not None:
            _require_str(guest.get(key), f"features.calls.guest_access.{key}")


def build_guest_call_share_url(
    call_domain: str,
    room_id: str,
    server_name: str,
    *,
    intent: str = "join_existing",
) -> str:
    """Build an Element Call SPA link for external guests (matches Element Web format)."""
    from urllib.parse import quote

    room_id = room_id.strip()
    if not room_id.startswith("!") or ":" not in room_id:
        raise ValueError("room_id must be a Matrix room ID like !abc:example.com")

    call_domain = call_domain.strip().rstrip("/")
    if call_domain.startswith("https://"):
        call_domain = call_domain[len("https://") :]
    elif call_domain.startswith("http://"):
        call_domain = call_domain[len("http://") :]

    params = [
        f"roomId={quote(room_id, safe='')}",
        f"intent={quote(intent, safe='')}",
        f"viaServers={quote(server_name.strip(), safe='')}",
        # Element Call URL flags (UrlParams.ts): meeting-only UX for external guests.
        "confineToRoom=true",
        "header=none",
    ]
    return f"https://{call_domain}/room/#/{room_id}?{'&'.join(params)}"


def build_caddy_guest_matrix_block(
    guest_server_name: str,
    *,
    main_homeserver_upstream: str,
) -> str:
    return (
        f"\n# Guest Tuwunel homeserver (Element Call external access)\n"
        f"{guest_server_name} {{\n"
        "    # Call rooms live on the main homeserver. Guest Tuwunel's federated room\n"
        "    # summary can omit join_rule; Element Call then treats the room as private.\n"
        "    # Peek summaries from the main homeserver (unauthenticated for public rooms).\n"
        "    handle /_matrix/client/v1/room_summary/* {\n"
        f"        reverse_proxy {main_homeserver_upstream} {{\n"
        "            header_up -Authorization\n"
        "        }\n"
        "    }\n"
        "    handle /_matrix/client/unstable/im.nheko.summary/* {\n"
        f"        reverse_proxy {main_homeserver_upstream} {{\n"
        "            header_up -Authorization\n"
        "        }\n"
        "    }\n\n"
        "    handle /_matrix/* {\n"
        "        header Access-Control-Allow-Origin *\n"
        "        reverse_proxy matrix_guest_tuwunel:8008 {\n"
        "            header_down -Access-Control-Allow-Origin\n"
        "        }\n"
        "    }\n\n"
        "    handle /.well-known/matrix/* {\n"
        "        header Access-Control-Allow-Origin *\n"
        "        reverse_proxy matrix_guest_tuwunel:8008 {\n"
        "            header_down -Access-Control-Allow-Origin\n"
        "        }\n"
        "    }\n\n"
        "    header {\n"
        "        X-Content-Type-Options nosniff\n"
        "        X-Frame-Options SAMEORIGIN\n"
        "        Referrer-Policy strict-origin-when-cross-origin\n"
        '        Permissions-Policy "interest-cohort=()"\n'
        "        -Server\n"
        "    }\n\n"
        "    encode gzip\n"
        "    log\n"
        "}\n"
    )


def build_caddy_element_call_site_block(guest_call_domain: str) -> str:
    return (
        f"\n# Element Call SPA (standalone meeting links)\n"
        f"{guest_call_domain} {{\n"
        "    # Hide login/register UI — guests join only via shared meeting links.\n"
        "    @guest_home path / /login /register\n"
        "    handle @guest_home {\n"
        "        root * /srv/guest-call-landing\n"
        "        rewrite * /index.html\n"
        "        file_server\n"
        "    }\n\n"
        "    handle {\n"
        "        reverse_proxy matrix_element_call:8080\n"
        "    }\n\n"
        "    header {\n"
        "        Access-Control-Allow-Origin *\n"
        '        Access-Control-Allow-Methods "GET, POST, OPTIONS"\n'
        '        Access-Control-Allow-Headers "Authorization, Content-Type"\n'
        "        X-Content-Type-Options nosniff\n"
        "        X-Frame-Options SAMEORIGIN\n"
        "        Referrer-Policy strict-origin-when-cross-origin\n"
        "        -Server\n"
        "    }\n\n"
        "    encode gzip\n"
        "    log\n"
        "}\n"
    )


def build_livekit_cs_api_url_overrides(
    server_name: str,
    matrix_domain: str,
    guest_enabled: bool,
    guest_server_name: str,
) -> str:
    overrides = [f"{server_name}=https://{matrix_domain}"]
    if guest_enabled and guest_server_name:
        overrides.append(f"{guest_server_name}=https://{guest_server_name}")
    return ",".join(overrides)


def build_livekit_full_access_homeservers(
    server_name: str,
    guest_enabled: bool,
    guest_server_name: str,
) -> str:
    if guest_enabled and guest_server_name:
        return f"{server_name},{guest_server_name}"
    return server_name


def install_guest_element_call_assets(guest_dir: Path) -> None:
    """Materialize Element Call guest files required for docker bind mounts."""
    template = guest_dir / "nginx.conf.template"
    if not template.is_file():
        raise FileNotFoundError(f"Missing Element Call nginx template: {template}")

    nginx_dest = guest_dir / "nginx.conf"
    if nginx_dest.is_dir():
        shutil.rmtree(nginx_dest)
    shutil.copyfile(template, nginx_dest)

    css_src = guest_dir / "guest-call.css"
    if not css_src.is_file():
        raise FileNotFoundError(f"Missing Element Call guest CSS: {css_src}")


def build_element_call_guest_config(config: dict) -> dict:
    """Build Element Call SPA config (see element-call ConfigOptions.ts / config.sample.json).

    Element Web's config.json schema (web-docs.element.dev) does not apply here.
    Analytics (posthog/sentry/rageshake) are omitted so they stay disabled.
    """
    features = config.get("features", {}) if isinstance(config.get("features", {}), dict) else {}
    calls = get_calls_config(features)
    guest_values = resolve_guest_access_values(config)
    livekit_domain = calls.get("livekit_domain") or f"livekit.{extract_base_domain(config['matrix']['domain'])}"
    jwt_url = f"https://{livekit_domain}/livekit/jwt"

    return {
        "default_server_config": {
            "m.homeserver": {
                "base_url": f"https://{guest_values['GUEST_SERVER_NAME']}",
                "server_name": guest_values["GUEST_SERVER_NAME"],
            },
            "org.matrix.msc4143.rtc_foci": [
                {
                    "type": "livekit",
                    "livekit_service_url": jwt_url,
                }
            ],
        },
        "livekit": {
            "livekit_service_url": jwt_url,
        },
        "features": {
            "feature_use_device_session_member_events": True,
        },
        # Clear Element's default SSLA URL (v0.21 still renders the caption text; guest-call.css hides it).
        "ssla": "",
        "matrix_rtc_mode": "compatibility",
        "matrix_rtc_session": {
            "wait_for_key_rotation_ms": 3000,
            "membership_event_expiry_ms": 180000000,
            "delayed_leave_event_delay_ms": 18000,
            "delayed_leave_event_restart_ms": 4000,
            "network_error_retry_ms": 100,
        },
        "media_devices": {
            "enable_audio": True,
            "enable_video": False,
        },
    }


def get_auto_join_synapse_options(auto_join: dict) -> dict:
    synapse = auto_join.get("synapse", {}) if isinstance(auto_join.get("synapse", {}), dict) else {}
    return synapse


def validate_auto_join_config(auto_join: dict) -> None:
    if "rooms" in auto_join:
        rooms = auto_join.get("rooms")
        if not isinstance(rooms, list):
            raise ValueError("features.auto_join.rooms must be a list")
        for index, entry in enumerate(rooms):
            validate_auto_join_room_entry(entry, f"features.auto_join.rooms[{index}]")

    synapse = get_auto_join_synapse_options(auto_join)
    if synapse:
        if "rooms_for_guests" in synapse:
            _require_bool(synapse.get("rooms_for_guests"), "features.auto_join.synapse.rooms_for_guests")
    elif "synapse" in auto_join and auto_join["synapse"] is not None:
        raise ValueError("features.auto_join.synapse must be an object")

    unknown = set(auto_join) - {"rooms", "synapse"}
    if unknown:
        raise ValueError(f"features.auto_join has unknown keys: {', '.join(sorted(unknown))}")


def build_synapse_auto_join_section(auto_join: dict, server_name: str = "") -> str:
    rooms = auto_join.get("rooms") or []
    if not rooms:
        return ""

    if not server_name:
        aliases = [
            entry if isinstance(entry, str) else entry.get("alias", "")
            for entry in rooms
        ]
    else:
        aliases = auto_join_room_aliases(rooms, server_name)

    synapse_opts = get_auto_join_synapse_options(auto_join)

    lines = [
        "# Auto-join rooms for new registrations (rooms are provisioned via med-admin)",
        "auto_join_rooms:",
    ]
    for alias in aliases:
        lines.append(f"  - {yaml.safe_dump(alias).strip()}")

    lines.append("autocreate_auto_join_rooms: false")

    if "rooms_for_guests" in synapse_opts:
        guests = synapse_opts["rooms_for_guests"]
        lines.append(f"auto_join_rooms_for_guests: {'true' if guests else 'false'}")

    return "\n".join(lines)


def build_synapse_guest_federation_section(guest_enabled: bool) -> str:
    if not guest_enabled:
        return ""
    return (
        "\n"
        "# Guest Element Call — Synapse reaches the guest homeserver via Caddy on\n"
        "# caddy_net (see caddy/docker-compose.guest.yml network aliases).\n"
        "# Without this whitelist, Synapse drops the private bridge IP and key fetches fail.\n"
        "ip_range_whitelist:\n"
        '  - "172.16.0.0/12"\n'
    )


def build_tuwunel_auto_join_section(auto_join: dict, server_name: str = "") -> str:
    rooms = auto_join.get("rooms") or []
    if not rooms:
        return ""

    if not server_name:
        aliases = [
            entry if isinstance(entry, str) else entry.get("alias", "")
            for entry in rooms
        ]
    else:
        aliases = auto_join_room_aliases(rooms, server_name)

    quoted = ", ".join(yaml.safe_dump(alias).strip() for alias in aliases)
    return "\n".join(
        [
            "# Auto-join rooms for new registrations (rooms are provisioned via med-admin)",
            f"auto_join_rooms = [{quoted}]",
        ]
    )


def validate_config(config: dict) -> None:
    matrix = config.get("matrix")
    if not isinstance(matrix, dict):
        raise ValueError("Missing 'matrix' section in deploy.yaml")

    for key in ("domain", "admin_username"):
        value = matrix.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Missing or invalid matrix.{key} in deploy.yaml")

    if "server_implementation" in matrix:
        homeserver.normalize_implementation(matrix.get("server_implementation"))

    features = config.get("features", {})
    if features is not None and not isinstance(features, dict):
        raise ValueError("features must be an object when provided")

    modules = config.get("modules", {})
    if modules is not None and not isinstance(modules, dict):
        raise ValueError("modules must be an object when provided")

    backup = config.get("backup", {})
    if backup is not None and not isinstance(backup, dict):
        raise ValueError("backup must be an object when provided")

    embed = config.get("embed")
    if embed is not None:
        if not isinstance(embed, dict):
            raise ValueError("embed must be an object")
        validate_embed_config(embed)

    if isinstance(features, dict):
        for key in ("registration_enabled", "federation_enabled", "local_login_enabled"):
            if key in features and not isinstance(features[key], bool):
                raise ValueError(f"features.{key} must be true/false")

        for section in ("element", "calls", "sso", "auto_join"):
            if section in features and not isinstance(features.get(section), dict):
                raise ValueError(f"features.{section} must be an object")

        element = features.get("element", {}) if isinstance(features.get("element", {}), dict) else {}
        if element:
            validate_element_config(element)

        auto_join = get_auto_join_config(features)
        if auto_join:
            validate_auto_join_config(auto_join)

        calls = get_calls_config(features)
        if calls:
            validate_calls_guest_access(config)

        mas_config.validate_sso_config(config)

    if isinstance(modules, dict):
        for key, value in modules.items():
            if not isinstance(value, dict):
                raise ValueError(f"modules.{key} must be an object")
            if "enabled" in value and not isinstance(value.get("enabled"), bool):
                raise ValueError(f"modules.{key}.enabled must be true/false")

    proxy_mode(config)

    if isinstance(backup, dict):
        enabled = backup.get("enabled", False)
        if "enabled" in backup and not isinstance(enabled, bool):
            raise ValueError("backup.enabled must be true/false")

        schedule = backup.get("schedule", {}) if isinstance(backup.get("schedule", {}), dict) else backup.get("schedule")
        if schedule is not None and not isinstance(schedule, dict):
            raise ValueError("backup.schedule must be an object when provided")

        if isinstance(schedule, dict):
            schedule_enabled = schedule.get("enabled", False)
            if "enabled" in schedule and not isinstance(schedule_enabled, bool):
                raise ValueError("backup.schedule.enabled must be true/false")

            if schedule_enabled and not enabled:
                raise ValueError("backup.schedule.enabled requires backup.enabled=true")

            if schedule_enabled:
                calendar = schedule.get("calendar")
                if not isinstance(calendar, str) or not calendar.strip():
                    raise ValueError("backup.schedule.calendar must be a non-empty string when backup.schedule.enabled is true")

            if "persistent" in schedule and not isinstance(schedule.get("persistent"), bool):
                raise ValueError("backup.schedule.persistent must be true/false")

        retention = backup.get("retention", {}) if isinstance(backup.get("retention", {}), dict) else backup.get("retention")
        if retention is not None and not isinstance(retention, dict):
            raise ValueError("backup.retention must be an object when provided")

        if isinstance(retention, dict):
            for key in ("keep_daily", "keep_weekly", "keep_monthly", "keep_yearly"):
                if key not in retention:
                    continue
                value = retention.get(key)
                if not isinstance(value, int) or value < 0:
                    raise ValueError(f"backup.retention.{key} must be a non-negative integer")

        if enabled:
            repository = backup.get("repository")
            if not isinstance(repository, dict):
                raise ValueError("backup.repository must be an object when backup.enabled is true")

            repo_type = repository.get("type")
            if repo_type not in ("local", "sftp"):
                raise ValueError("backup.repository.type must be 'local' or 'sftp'")

            encryption = repository.get("encryption", "repokey")
            if encryption not in ("repokey", "none"):
                raise ValueError("backup.repository.encryption must be 'repokey' or 'none'")
            if "port" in repository and (
                isinstance(repository["port"], bool)
                or not isinstance(repository["port"], int)
                or repository["port"] < 0
            ):
                raise ValueError("backup.repository.port must be a non-negative integer")
            if "host_key_check" in repository and not isinstance(repository["host_key_check"], bool):
                raise ValueError("backup.repository.host_key_check must be true/false")

            if repo_type == "local":
                repo_path = repository.get("path")
                if not isinstance(repo_path, str) or not repo_path.strip():
                    raise ValueError("backup.repository.path must be a non-empty string when backup.enabled is true")
                if not repo_path.startswith("/"):
                    raise ValueError("backup.repository.path must be an absolute path")
            else:
                for key in ("host", "user", "path", "ssh_key_path"):
                    value = repository.get(key)
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(f"backup.repository.{key} is required for sftp repositories")
                ssh_key_path = Path(repository["ssh_key_path"]).expanduser()
                if not ssh_key_path.exists():
                    raise ValueError(f"backup.repository.ssh_key_path not found: {ssh_key_path}")


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
    hs_spec = homeserver.get_spec(config)

    matrix_domain = matrix["domain"]
    server_name = matrix.get("server_name") or extract_base_domain(matrix_domain)
    derived["SERVER_NAME"] = server_name
    derived["MAS_DOCKER_ASSETS_PATH"] = mas_config.MAS_DOCKER_ASSETS_PATH
    derived.update(homeserver.spec_env_overrides(hs_spec))
    derived["CADDY_SYNAPSE_ADMIN_BLOCK"] = homeserver.caddy_synapse_admin_block(hs_spec)

    fed_enabled = bool(features.get("federation_enabled", True))
    derived["FEDERATION_WHITELIST"] = "~" if fed_enabled else "[]"
    derived["ALLOW_PUBLIC_ROOMS_FEDERATION"] = "true" if fed_enabled else "false"
    derived["TUWUNEL_ALLOW_FEDERATION"] = "true" if fed_enabled else "false"
    derived["TUWUNEL_ALLOW_PUBLIC_ROOMS_FEDERATION"] = "true" if fed_enabled else "false"

    reg_enabled = bool(features.get("registration_enabled", False))
    derived["ENABLE_REGISTRATION"] = "true" if reg_enabled else "false"
    # Tuwunel requires allow_registration=true for token-based registration; the
    # registration_token secret gates who can register (not open public signup).
    derived["TUWUNEL_ALLOW_REGISTRATION"] = "true"

    mas = mas_config.resolve_mas_runtime_config(config)
    sso = mas_config.get_sso_config(features)
    mas_enabled = bool(mas.get("enabled", False)) and hs_spec.implementation == "synapse"
    mas_local_login = bool(mas.get("local_login_enabled", True))
    derived["MAS_ENABLED"] = "true" if mas_enabled else "false"
    derived["MAS_LOCAL_LOGIN_ENABLED"] = "true" if mas_local_login else "false"
    derived["LOCAL_LOGIN_ENABLED"] = "false" if mas_enabled else ("true" if mas_local_login else "false")
    derived["LOGIN_VIA_EXISTING_SESSION_ENABLED"] = "false" if mas_enabled else "true"

    providers = mas.get("upstream_providers", []) if isinstance(mas.get("upstream_providers", []), list) else []
    if bool(sso.get("enabled", False)):
        derived["ENABLE_SSO"] = "true"
        derived["OIDC_PROVIDER_COUNT"] = str(len(providers))
        derived["OIDC_PROVIDER_NAMES"] = ",".join(
            p.get("name", "") for p in providers if isinstance(p, dict)
        )
    else:
        derived["ENABLE_SSO"] = "false"
        derived["OIDC_PROVIDER_COUNT"] = "0"
        derived["OIDC_PROVIDER_NAMES"] = ""
    derived["OIDC_PROVIDERS_JSON"] = "[]"
    derived["MAS_UPSTREAM_PROVIDER_COUNT"] = derived["OIDC_PROVIDER_COUNT"]
    derived["MAS_UPSTREAM_PROVIDER_NAMES"] = derived["OIDC_PROVIDER_NAMES"]

    if mas_enabled:
        path_prefix = mas.get("path_prefix", mas_config.MAS_PATH_PREFIX)
        public_base = mas_config.mas_public_base(matrix_domain, path_prefix)
        derived["MAS_DOMAIN"] = matrix_domain
        derived["MAS_PATH_PREFIX"] = path_prefix
        derived["MAS_PUBLIC_BASE"] = public_base
        derived["CADDY_MAS_BLOCK"] = mas_config.caddy_mas_block(path_prefix)
        derived.update(
            mas_config.build_synapse_mas_sections(
                enabled=True,
                server_name=server_name,
                mas_public_base=public_base,
                secrets={},
            )
        )
    else:
        derived["MAS_DOMAIN"] = ""
        derived["MAS_PATH_PREFIX"] = ""
        derived["MAS_PUBLIC_BASE"] = ""
        derived["CADDY_MAS_BLOCK"] = ""
        derived.update(
            mas_config.build_synapse_mas_sections(
                enabled=False,
                server_name=server_name,
                mas_public_base="",
                secrets={},
            )
        )

    derived["SYNAPSE_AUTO_JOIN_SECTION"] = build_synapse_auto_join_section(
        get_auto_join_config(features),
        server_name=server_name,
    )
    derived["TUWUNEL_AUTO_JOIN_SECTION"] = build_tuwunel_auto_join_section(
        get_auto_join_config(features),
        server_name=server_name,
    )

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

    guest_calls_enabled = calls_enabled and guest_access_enabled(calls)
    derived["GUEST_ACCESS_ENABLED"] = "true" if guest_calls_enabled else "false"
    derived["SYNAPSE_GUEST_FEDERATION_SECTION"] = build_synapse_guest_federation_section(
        guest_calls_enabled
    )
    if guest_calls_enabled:
        guest_values = resolve_guest_access_values(config)
        derived.update(guest_values)
        derived["GUEST_CADDY_NETWORK_ALIASES"] = format_caddy_guest_network_aliases(
            guest_values["GUEST_SERVER_NAME"],
            server_name,
            matrix_domain,
        )
        derived["CADDY_GUEST_MATRIX_BLOCK"] = build_caddy_guest_matrix_block(
            guest_values["GUEST_SERVER_NAME"],
            main_homeserver_upstream=derived["HOMESERVER_UPSTREAM"],
        )
        derived["CADDY_ELEMENT_CALL_SITE_BLOCK"] = build_caddy_element_call_site_block(
            guest_values["GUEST_CALL_DOMAIN"]
        )
    else:
        derived["GUEST_SERVER_NAME"] = ""
        derived["GUEST_CALL_DOMAIN"] = ""
        derived["GUEST_FEDERATION_ALLOW_REGEX"] = ""
        derived["GUEST_CADDY_NETWORK_ALIASES"] = ""
        derived["CADDY_GUEST_MATRIX_BLOCK"] = ""
        derived["CADDY_ELEMENT_CALL_SITE_BLOCK"] = ""

    derived["LIVEKIT_FULL_ACCESS_HOMESERVERS"] = build_livekit_full_access_homeservers(
        server_name,
        guest_calls_enabled,
        derived.get("GUEST_SERVER_NAME", ""),
    )
    derived["LIVEKIT_CS_API_URL_OVERRIDES"] = build_livekit_cs_api_url_overrides(
        server_name,
        matrix_domain,
        guest_calls_enabled,
        derived.get("GUEST_SERVER_NAME", ""),
    )

    if guest_calls_enabled and hs_spec.implementation == "tuwunel":
        derived["GUEST_ACCESS_TUWUNEL_MAIN_WARNING"] = (
            "Guest call access is enabled but the main homeserver is Tuwunel; "
            "call reliability may be reduced (MSC4140 delayed events)."
        )
    else:
        derived["GUEST_ACCESS_TUWUNEL_MAIN_WARNING"] = ""

    hosts = [matrix_domain]
    if server_name != matrix_domain:
        hosts.append(server_name)
    derived["CADDY_MATRIX_HOSTS"] = ", ".join(hosts)

    extra_ancestors = extra_frame_ancestors(config)
    element_on_matrix_host = bool(
        element_enabled and derived.get("ELEMENT_DOMAIN", "") in set(hosts)
    )
    if extra_ancestors and element_on_matrix_host:
        derived["CADDY_MATRIX_FRAME_HEADERS"] = mas_config.caddy_clickjacking_header_lines(
            extra_ancestors
        )
    else:
        derived["CADDY_MATRIX_FRAME_HEADERS"] = "        X-Frame-Options SAMEORIGIN"

    derived.update(
        mas_config.build_caddy_element_routing(
            matrix_domain=matrix_domain,
            server_name=server_name,
            element_enabled=element_enabled,
            element_domain=derived.get("ELEMENT_DOMAIN", ""),
            frame_ancestors=extra_ancestors,
        )
    )

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


def create_or_update_secrets(
    ctx: ApplyContext,
    existing: dict,
    rotate: bool = False,
    *,
    mas_enabled: bool = False,
    backup_enabled: bool = False,
) -> dict:
    state = dict(existing)

    for key in DEFAULT_SECRET_KEYS:
        if rotate or not state.get(key):
            state[key] = generate_secret()

    # Borg repo passphrase: generated once when backups are enabled; rotating it
    # would lock out existing encrypted repositories, so it is never rotated.
    if backup_enabled and not state.get("BORG_PASSPHRASE"):
        state["BORG_PASSPHRASE"] = generate_secret()

    state = mas_config.ensure_mas_secrets(state, rotate=rotate, mas_enabled=mas_enabled)

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
        "PROXY_MODE": proxy_mode(config),
        "INTEGRATE_NETWORK": integrate_network_name(config),
    }

    modules = config.get("modules", {}) if isinstance(config.get("modules", {}), dict) else {}
    whatsapp = modules.get("whatsapp_bridge", {}) if isinstance(modules.get("whatsapp_bridge", {}), dict) else {}
    slack = modules.get("slack_bridge", {}) if isinstance(modules.get("slack_bridge", {}), dict) else {}

    wa_db_name = str(whatsapp.get("db_name", "mautrix_whatsapp"))
    wa_db_user = "mautrix_whatsapp"
    wa_db_password = state_secrets.get("WA_DB_PASSWORD", "")
    env_vars["WA_DB_NAME"] = wa_db_name
    env_vars["WA_DB_USER"] = wa_db_user
    env_vars["WA_DB_PASSWORD"] = wa_db_password
    env_vars["WA_DB_URI"] = (
        f"postgres://{wa_db_user}:{wa_db_password}@matrix_postgres/{wa_db_name}?sslmode=disable"
    )
    env_vars["WA_ADMIN_USERNAME"] = str(
        whatsapp.get("admin_username", config["matrix"].get("admin_username", "admin"))
    )

    sl_db_name = str(slack.get("db_name", "mautrix_slack"))
    sl_db_user = "mautrix_slack"
    sl_db_password = state_secrets.get("SL_DB_PASSWORD", "")
    env_vars["SL_DB_NAME"] = sl_db_name
    env_vars["SL_DB_USER"] = sl_db_user
    env_vars["SL_DB_PASSWORD"] = sl_db_password
    env_vars["SL_DB_URI"] = (
        f"postgres://{sl_db_user}:{sl_db_password}@matrix_postgres/{sl_db_name}?sslmode=disable"
    )
    env_vars["SL_ADMIN_USERNAME"] = str(
        slack.get("admin_username", config["matrix"].get("admin_username", "admin"))
    )

    mas_db_name = "mas"
    mas_db_user = "mas"
    mas_db_password = state_secrets.get("MAS_DB_PASSWORD", "")
    env_vars["MAS_DB_NAME"] = mas_db_name
    env_vars["MAS_DB_USER"] = mas_db_user
    env_vars["MAS_DB_PASSWORD"] = mas_db_password
    env_vars["MAS_DB_URI"] = (
        f"postgresql://{mas_db_user}:{mas_db_password}@matrix_postgres:5432/{mas_db_name}?sslmode=disable"
    )

    env_vars.update(derived)
    env_vars.update(state_secrets)

    if env_vars.get("MAS_ENABLED") == "true":
        mas = mas_config.resolve_mas_runtime_config(config)
        public_base = env_vars.get("MAS_PUBLIC_BASE", "")
        providers = mas.get("upstream_providers", []) if isinstance(mas.get("upstream_providers", []), list) else []
        env_vars["MAS_UPSTREAM_OAUTH2_YAML"] = mas_config.build_mas_upstream_oauth2_yaml(providers, public_base)
        env_vars["MAS_SIGNING_KEYS_YAML"] = mas_config.build_mas_signing_keys_yaml_from_state(state_secrets)
        mas_sections = mas_config.build_synapse_mas_sections(
            enabled=True,
            server_name=derived["SERVER_NAME"],
            mas_public_base=public_base,
            secrets=state_secrets,
        )
        env_vars.update(mas_sections)
    else:
        env_vars["MAS_UPSTREAM_OAUTH2_YAML"] = "upstream_oauth2:\n  providers: []\n"
        env_vars["MAS_SIGNING_KEYS_YAML"] = ""

    return env_vars


def deep_merge(dst: dict, src: dict) -> dict:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def apply_element_ui_features(target: dict, ui_features_cfg: dict) -> None:
    setting_defaults = target.setdefault("setting_defaults", {})
    for key, value in ui_features_cfg.items():
        mapped_key = ELEMENT_UI_FEATURES.get(key)
        if mapped_key:
            setting_defaults[mapped_key] = value


def build_default_element_config(config: dict) -> dict:
    matrix = config.get("matrix", {}) if isinstance(config.get("matrix", {}), dict) else {}
    features = config.get("features", {}) if isinstance(config.get("features", {}), dict) else {}

    matrix_domain = matrix.get("domain", "")
    server_name = matrix.get("server_name") or extract_base_domain(matrix_domain)

    return {
        "default_server_config": {
            "m.homeserver": {
                "base_url": f"https://{matrix_domain}",
                "server_name": server_name,
            },
            "m.identity_server": {
                "base_url": "https://vector.im",
            },
        },
        "disable_custom_urls": False,
        "disable_guests": True,
        "disable_login_language_selector": False,
        "disable_3pid_login": False,
        "brand": "Element",
        "integrations_ui_url": SCALAR_INTEGRATIONS_UI_URL,
        "integrations_rest_url": SCALAR_INTEGRATIONS_REST_URL,
        "integrations_widgets_urls": list(SCALAR_INTEGRATIONS_WIDGETS_URLS),
        "bug_report_endpoint_url": "https://element.io/bugreports/submit",
        "map_style_url": "https://api.maptiler.com/maps/streets/style.json?key=fU3vlMsMn4Jb6dnEIFsx",
        "show_labs_settings": False,
        "room_directory": {
            "servers": [server_name],
        },
        "default_federate": bool(features.get("federation_enabled", True)),
    }


def merge_element_customizations(base: dict, element_cfg: dict) -> dict:
    merged = deep_merge({}, base)

    for key in ELEMENT_STRING_KEYS | ELEMENT_BOOL_KEYS:
        if key in element_cfg:
            merged[key] = element_cfg[key]

    branding = element_cfg.get("branding") if isinstance(element_cfg.get("branding"), dict) else None
    if branding:
        branding_target = merged.setdefault("branding", {})
        for key in ("auth_header_logo_url", "welcome_background_url", "logo_link_url", "auth_footer_links"):
            if key in branding:
                branding_target[key] = branding[key]

    embedded_pages = element_cfg.get("embedded_pages") if isinstance(element_cfg.get("embedded_pages"), dict) else None
    if embedded_pages:
        embedded_target = merged.setdefault("embedded_pages", {})
        for key in ("home_url", "welcome_url", "login_for_welcome"):
            if key in embedded_pages:
                embedded_target[key] = embedded_pages[key]

    sso_redirect_options = element_cfg.get("sso_redirect_options")
    if isinstance(sso_redirect_options, dict) and sso_redirect_options:
        redirect_target = merged.setdefault("sso_redirect_options", {})
        for key in ("immediate", "on_welcome_page", "on_login_page"):
            if key in sso_redirect_options:
                redirect_target[key] = sso_redirect_options[key]

    integrations = element_cfg.get("integrations") if isinstance(element_cfg.get("integrations"), dict) else None
    if integrations:
        if integrations.get("enabled") is False:
            merged["integrations_ui_url"] = None
            merged["integrations_rest_url"] = None
            merged["integrations_widgets_urls"] = None
        else:
            if "ui_url" in integrations:
                merged["integrations_ui_url"] = integrations["ui_url"]
            if "rest_url" in integrations:
                merged["integrations_rest_url"] = integrations["rest_url"]
            if "widgets_urls" in integrations:
                merged["integrations_widgets_urls"] = integrations["widgets_urls"]

    room_directory = element_cfg.get("room_directory") if isinstance(element_cfg.get("room_directory"), dict) else None
    if room_directory and "servers" in room_directory:
        merged["room_directory"] = {"servers": room_directory["servers"]}

    labs = element_cfg.get("labs") if isinstance(element_cfg.get("labs"), dict) else None
    if labs:
        if "show_settings" in labs:
            merged["show_labs_settings"] = labs["show_settings"]
        if "features" in labs:
            merged["features"] = dict(labs["features"])

    ui_features = element_cfg.get("ui_features") if isinstance(element_cfg.get("ui_features"), dict) else None
    if ui_features:
        apply_element_ui_features(merged, ui_features)

    notice = element_cfg.get("notice") if isinstance(element_cfg.get("notice"), dict) else None
    if notice:
        merged["user_notice"] = dict(notice)

    terms_and_conditions = (
        element_cfg.get("terms_and_conditions")
        if isinstance(element_cfg.get("terms_and_conditions"), dict)
        else None
    )
    if terms_and_conditions and "links" in terms_and_conditions:
        merged["terms_and_conditions_links"] = list(terms_and_conditions["links"])

    report_event = element_cfg.get("report_event") if isinstance(element_cfg.get("report_event"), dict) else None
    if report_event:
        merged["report_event"] = dict(report_event)

    bug_report = element_cfg.get("bug_report") if isinstance(element_cfg.get("bug_report"), dict) else None
    if bug_report:
        if "endpoint_url" in bug_report:
            merged["bug_report_endpoint_url"] = bug_report["endpoint_url"]
        if "existing_issues_url" in bug_report:
            merged["existing_issues_url"] = bug_report["existing_issues_url"]
        if "new_issue_url" in bug_report:
            merged["new_issue_url"] = bug_report["new_issue_url"]
        if "sentry" in bug_report:
            merged["sentry"] = dict(bug_report["sentry"])

    extra_config = element_cfg.get("extra_config") if isinstance(element_cfg.get("extra_config"), dict) else None
    if extra_config:
        deep_merge(merged, extra_config)

    return merged


def livekit_jwt_service_url(config: dict) -> str:
    matrix = config.get("matrix", {}) if isinstance(config.get("matrix", {}), dict) else {}
    features = config.get("features", {}) if isinstance(config.get("features", {}), dict) else {}
    calls = features.get("calls", {}) if isinstance(features.get("calls", {}), dict) else {}
    matrix_domain = matrix.get("domain", "")
    livekit_domain = calls.get("livekit_domain") or f"livekit.{extract_base_domain(matrix_domain)}"
    return f"https://{livekit_domain}/livekit/jwt"


DEFAULT_ELEMENT_CALL_URL = "https://call.element.io"

# Labs flags Element Web needs to place MatrixRTC calls (legacy VoIP breaks Element X).
ELEMENT_CALL_FEATURE_FLAGS = {
    "feature_video_rooms": True,
    "feature_group_calls": True,
    "feature_element_call_video_rooms": True,
}


def apply_calls_element_config(target: dict, config: dict) -> None:
    """Configure Element Web for MatrixRTC when the calls stack is enabled."""
    features = config.get("features", {}) if isinstance(config.get("features", {}), dict) else {}
    calls = get_calls_config(features)
    if not calls.get("enabled", True):
        return

    rtc_foci = [
        {
            "type": "livekit",
            "livekit_service_url": livekit_jwt_service_url(config),
        }
    ]
    default_server = target.setdefault("default_server_config", {})
    default_server["org.matrix.msc4143.rtc_foci"] = rtc_foci

    guest_enabled = guest_access_enabled(calls)
    if guest_enabled:
        guest_values = resolve_guest_access_values(config)
        element_call_url = f"https://{guest_values['GUEST_CALL_DOMAIN']}"
    else:
        element_call_url = str(calls.get("element_call_url") or DEFAULT_ELEMENT_CALL_URL).strip()

    element_call_cfg: dict[str, Any] = {
        "url": element_call_url,
        "use_exclusively": True,
    }
    if guest_enabled:
        element_call_cfg["guest_spa_url"] = element_call_url

    target["element_call"] = element_call_cfg

    element_features = target.setdefault("features", {})
    for key, value in ELEMENT_CALL_FEATURE_FLAGS.items():
        element_features[key] = value
    if guest_enabled:
        element_features["feature_ask_to_join"] = True


def apply_sso_login_defaults(element_json: dict, config: dict) -> None:
    """Send Element users straight to Kanidm unless the operator asked for a chooser."""
    features = config.get("features") if isinstance(config.get("features"), dict) else {}
    element_cfg = features.get("element") if isinstance(features.get("element"), dict) else {}
    explicit = element_cfg.get("sso_redirect_options") if "sso_redirect_options" in element_cfg else None
    if explicit is False:
        element_json.pop("sso_redirect_options", None)
        return
    if isinstance(explicit, dict) and explicit:
        return
    if mas_config.resolve_sso_default_login(config) != mas_config.SSO_DEFAULT_LOGIN_SSO:
        return
    element_json["sso_redirect_options"] = {
        "on_welcome_page": True,
        "on_login_page": True,
    }


def build_element_config(config: dict) -> dict:
    features = config.get("features", {}) if isinstance(config.get("features", {}), dict) else {}
    element_cfg = features.get("element", {}) if isinstance(features.get("element"), dict) else {}
    merged = merge_element_customizations(build_default_element_config(config), element_cfg)
    apply_calls_element_config(merged, config)
    apply_sso_login_defaults(merged, config)
    return merged


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=4) + "\n")


def _env_file_scalar(value: Any) -> str | None:
    if isinstance(value, (dict, list)):
        return None
    text = str(value)
    if "\n" in text or "\r" in text:
        return None
    return text


def write_env_file(ctx: ApplyContext, env_vars: dict) -> None:
    existing = load_env_map(ctx.env_file)
    merged = dict(env_vars)
    for key in PRESERVED_ENV_KEYS:
        if existing.get(key) and key not in merged:
            merged[key] = existing[key]

    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# matrix-easy-deploy environment",
        f"# Generated by apply.py on {timestamp}",
        "# Keep this file private - it contains secrets.",
        "",
    ]
    for key in sorted(merged.keys()):
        if key in ENV_FILE_EXCLUDED_KEYS:
            continue
        value = _env_file_scalar(merged[key])
        if value is None:
            continue
        lines.append(f"{key}={value}")
    lines.append("")

    ctx.env_file.write_text("\n".join(lines))
    ctx.env_file.chmod(0o600)


def link_calls_compose_env(ctx: ApplyContext) -> None:
    """Symlink modules/calls/.env → repo .env so docker compose picks up variables."""
    calls_dir = ctx.project_root / "modules" / "calls"
    calls_dir.mkdir(parents=True, exist_ok=True)
    link = calls_dir / ".env"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to("../../.env")


def render_template(src: Path, dest: Path, values: dict) -> None:
    content = src.read_text()
    for key, value in values.items():
        content = content.replace("{{" + key + "}}", str(value))
    dest.write_text(content)


def strip_empty_caddy_site_blocks(content: str) -> str:
    lines = content.splitlines()
    kept: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped in {'"" {', '{'}:
            while kept and not kept[-1].strip():
                kept.pop()
            if kept and kept[-1].lstrip().startswith("#"):
                kept.pop()

            depth = line.count("{") - line.count("}")
            index += 1
            while index < len(lines) and depth > 0:
                depth += lines[index].count("{") - lines[index].count("}")
                index += 1
            continue

        kept.append(line)
        index += 1

    return "\n".join(kept) + ("\n" if content.endswith("\n") else "")


def parse_caddy_hosts_ordered(header_line: str) -> list[str]:
    hosts_part = header_line.rsplit("{", 1)[0].strip()
    if not hosts_part:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for host in hosts_part.split(","):
        host = host.strip()
        if host and host not in seen:
            seen.add(host)
            ordered.append(host)
    return ordered


def caddy_hosts_group_key(hosts_ordered: list[str]) -> tuple[str, ...]:
    return tuple(sorted(hosts_ordered))


def is_caddy_site_header(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("#"):
        return False
    if not stripped.endswith("{"):
        return False
    if line != line.lstrip():
        return False
    return bool(stripped[:-1].strip())


def merge_duplicate_caddy_site_blocks(content: str) -> str:
    lines = content.splitlines()
    leading_file: list[str] = []
    blocks: list[dict] = []
    pending_leading: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        if is_caddy_site_header(line):
            body: list[str] = []
            depth = line.count("{") - line.count("}")
            index += 1
            while index < len(lines) and depth > 0:
                if depth == 1 and lines[index].strip() == "}":
                    index += 1
                    depth -= 1
                    break
                body.append(lines[index])
                depth += lines[index].count("{") - lines[index].count("}")
                index += 1

            hosts_ordered = parse_caddy_hosts_ordered(line)
            blocks.append(
                {
                    "leading": pending_leading,
                    "header": line,
                    "body": body,
                    "hosts_ordered": hosts_ordered,
                    "hosts": caddy_hosts_group_key(hosts_ordered),
                }
            )
            pending_leading = []
            continue

        if not blocks:
            leading_file.append(line)
        else:
            pending_leading.append(line)
        index += 1

    trailing = pending_leading
    if not blocks:
        return content

    order: list[tuple[str, ...]] = []
    groups: dict[tuple[str, ...], list[dict]] = {}
    for block in blocks:
        hosts = block["hosts"]
        if hosts not in groups:
            order.append(hosts)
        groups.setdefault(hosts, []).append(block)

    output: list[str] = []
    output.extend(leading_file)

    for hosts in order:
        group = groups[hosts]
        if not hosts:
            for block in group:
                output.extend(block["leading"])
                output.append(block["header"])
                output.extend(block["body"])
                output.append("}")
            continue

        leading: list[str] = []
        for block in group:
            for candidate in block["leading"]:
                if candidate not in leading:
                    leading.append(candidate)
        output.extend(leading)
        if leading and leading[-1].strip():
            output.append("")

        output.append(f"{', '.join(group[0]['hosts_ordered'])} {{")

        merged_body: list[str] = []
        for block in group:
            if merged_body and merged_body[-1].strip() and block["body"] and block["body"][0].strip():
                merged_body.append("")
            merged_body.extend(block["body"])
        output.extend(merged_body)
        output.append("}")

    if trailing:
        if output and output[-1].strip():
            output.append("")
        output.extend(trailing)

    return "\n".join(output) + ("\n" if content.endswith("\n") else "")


def finalize_caddyfile_content(content: str) -> str:
    return merge_duplicate_caddy_site_blocks(strip_empty_caddy_site_blocks(content))


def finalize_caddyfile(path: Path) -> None:
    path.write_text(finalize_caddyfile_content(path.read_text()))


def fail_if_unresolved_placeholder(path: Path) -> None:
    content = path.read_text()
    if re.search(r"\{\{[A-Z_][A-Z0-9_]*\}\}", content):
        raise ValueError(f"{path} still contains unresolved template placeholders")


def render_templates(ctx: ApplyContext, config: dict, env_vars: dict) -> None:
    install_element = env_vars.get("INSTALL_ELEMENT", "true") == "true"
    hs_spec = homeserver.get_spec(config)
    caddy_template = (
        ctx.project_root / "caddy" / ("Caddyfile.template" if install_element else "Caddyfile-no-element.template")
    )
    caddy_dest = ctx.project_root / "caddy" / "Caddyfile"
    render_template(caddy_template, caddy_dest, env_vars)
    caddy_dest.write_text(finalize_caddyfile_content(caddy_dest.read_text()))
    fail_if_unresolved_placeholder(caddy_dest)

    if hs_spec.implementation == "synapse":
        hs_template = ctx.project_root / hs_spec.rendered_config_template_rel
        hs_dest = ctx.project_root / hs_spec.rendered_config_rel
        render_template(hs_template, hs_dest, env_vars)
        fail_if_unresolved_placeholder(hs_dest)
    elif hs_spec.implementation == "tuwunel":
        hs_template = ctx.project_root / hs_spec.rendered_config_template_rel
        hs_dest = ctx.project_root / hs_spec.rendered_config_rel
        render_template(hs_template, hs_dest, env_vars)
        fail_if_unresolved_placeholder(hs_dest)
        appservice_dir = ctx.project_root / hs_spec.appservice_data_rel
        appservice_dir.mkdir(parents=True, exist_ok=True)

    if install_element:
        element_dest = ctx.project_root / "modules" / "core" / "element" / "config.json"
        write_json(element_dest, build_element_config(config))

    coturn_template = ctx.project_root / "modules" / "calls" / "coturn" / "turnserver.conf.template"
    coturn_dest = ctx.project_root / "modules" / "calls" / "coturn" / "turnserver.conf"
    render_template(coturn_template, coturn_dest, env_vars)
    fail_if_unresolved_placeholder(coturn_dest)

    livekit_template = ctx.project_root / "modules" / "calls" / "livekit" / "livekit.yaml.template"
    livekit_dest = ctx.project_root / "modules" / "calls" / "livekit" / "livekit.yaml"
    render_template(livekit_template, livekit_dest, env_vars)
    fail_if_unresolved_placeholder(livekit_dest)

    if env_vars.get("GUEST_ACCESS_ENABLED") == "true":
        guest_dir = ctx.project_root / "modules" / "calls" / "guest"
        guest_dir.mkdir(parents=True, exist_ok=True)
        (guest_dir / "tuwunel_data").mkdir(parents=True, exist_ok=True)

        guest_tuwunel_template = guest_dir / "tuwunel.toml.template"
        guest_tuwunel_dest = guest_dir / "tuwunel.toml"
        render_template(guest_tuwunel_template, guest_tuwunel_dest, env_vars)
        fail_if_unresolved_placeholder(guest_tuwunel_dest)

        write_json(guest_dir / "element-call.config.json", build_element_call_guest_config(config))
        install_guest_element_call_assets(guest_dir)

        caddy_guest_compose_template = ctx.project_root / "caddy" / "docker-compose.guest.template"
        caddy_guest_compose_dest = ctx.project_root / "caddy" / "docker-compose.guest.yml"
        render_template(caddy_guest_compose_template, caddy_guest_compose_dest, env_vars)
        fail_if_unresolved_placeholder(caddy_guest_compose_dest)

        core_guest_compose_template = ctx.project_root / "modules" / "core" / "docker-compose.guest.template"
        core_guest_compose_dest = ctx.project_root / "modules" / "core" / "docker-compose.guest.yml"
        render_template(core_guest_compose_template, core_guest_compose_dest, env_vars)
        fail_if_unresolved_placeholder(core_guest_compose_dest)
    else:
        guest_compose_dest = ctx.project_root / "modules" / "calls" / "docker-compose.guest.yml"
        if guest_compose_dest.exists():
            guest_compose_dest.unlink()
        caddy_guest_compose_dest = ctx.project_root / "caddy" / "docker-compose.guest.yml"
        if caddy_guest_compose_dest.exists():
            caddy_guest_compose_dest.unlink()
        core_guest_compose_dest = ctx.project_root / "modules" / "core" / "docker-compose.guest.yml"
        if core_guest_compose_dest.exists():
            core_guest_compose_dest.unlink()

    if env_vars.get("MAS_ENABLED") == "true":
        mas_template = ctx.project_root / "modules" / "mas" / "config.yaml.template"
        mas_dest = ctx.project_root / "modules" / "mas" / "config.yaml"
        render_template(mas_template, mas_dest, env_vars)
        fail_if_unresolved_placeholder(mas_dest)


def load_module_manifest(ctx: ApplyContext, module_dir_name: str) -> dict:
    manifest_path = ctx.project_root / "modules" / module_dir_name / "module.yaml"
    if not manifest_path.exists():
        return {}
    with manifest_path.open() as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def module_required_files(ctx: ApplyContext, manifest: dict) -> list[Path]:
    paths: list[Path] = []
    generated = manifest.get("generated_files", []) if isinstance(manifest.get("generated_files", []), list) else []
    for rel in generated:
        if isinstance(rel, str) and rel.strip():
            paths.append(ctx.project_root / rel)

    runtime = manifest.get("runtime", {}) if isinstance(manifest.get("runtime", {}), dict) else {}
    config_exists = runtime.get("config_exists")
    if isinstance(config_exists, str) and config_exists.strip():
        config_path = ctx.project_root / config_exists
        if config_path not in paths:
            paths.append(config_path)

    return paths


def missing_module_files(ctx: ApplyContext, manifest: dict) -> list[Path]:
    required = module_required_files(ctx, manifest)
    if not required:
        return []
    return [path for path in required if not path.exists()]


def module_bootstrap_needs_postgres(ctx: ApplyContext, config: dict) -> bool:
    modules_cfg = config.get("modules", {}) if isinstance(config.get("modules", {}), dict) else {}
    for config_key in MODULES_NEEDING_POSTGRES:
        desired = modules_cfg.get(config_key, {}) if isinstance(modules_cfg.get(config_key, {}), dict) else {}
        if not bool(desired.get("enabled", False)):
            continue
        dir_name = MODULE_CONFIG_KEY_TO_DIR[config_key]
        manifest = load_module_manifest(ctx, dir_name)
        if missing_module_files(ctx, manifest):
            return True
    return False


def ensure_postgres_prerequisite(ctx: ApplyContext) -> None:
    env = dict(os.environ)
    env_map = load_env_map(ctx.env_file)
    postgres_password = env_map.get("POSTGRES_PASSWORD", "").strip()
    if postgres_password:
        env["POSTGRES_PASSWORD"] = postgres_password

    subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{ctx.project_root}/scripts/lib.sh" && '
                f'source "{ctx.project_root}/scripts/module_common.sh" && '
                f'ensure_postgres_prerequisite "{ctx.project_root}"'
            ),
        ],
        check=True,
        cwd=str(ctx.project_root),
        env=env,
    )


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
    converged: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for config_key, dir_name in MODULE_CONFIG_KEY_TO_DIR.items():
        desired = modules_cfg.get(config_key, {}) if isinstance(modules_cfg.get(config_key, {}), dict) else {}
        if not bool(desired.get("enabled", False)):
            skipped.append(config_key)
            continue

        manifest = load_module_manifest(ctx, dir_name)
        missing_before = missing_module_files(ctx, manifest)
        if not missing_before:
            converged.append(config_key)
            continue

        setup_script = ctx.project_root / "modules" / dir_name / "setup.sh"
        if not setup_script.exists():
            failed.append(f"{config_key}: missing setup script ({setup_script})")
            continue

        env = dict(os.environ)
        env["MED_NON_INTERACTIVE"] = "1"
        for key, value in module_env_overrides(config, config_key).items():
            env[key] = value

        try:
            subprocess.run(["bash", str(setup_script)], check=True, env=env)
        except subprocess.CalledProcessError as exc:
            failed.append(f"{config_key}: setup failed with exit code {exc.returncode}")
            continue

        missing_after = missing_module_files(ctx, manifest)
        if missing_after:
            missing_list = ", ".join(str(p.relative_to(ctx.project_root)) for p in missing_after)
            failed.append(f"{config_key}: missing generated files after setup ({missing_list})")
            continue

        converged.append(config_key)

    if converged:
        print("Module convergence complete for: " + ", ".join(sorted(converged)))
    if failed:
        print("Module convergence failures:", file=sys.stderr)
        for item in failed:
            print(f"  - {item}", file=sys.stderr)
        raise RuntimeError("One or more enabled modules failed to converge")


def _postgres_container_running() -> bool:
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.strip() == "matrix_postgres" for line in result.stdout.splitlines())


def reconcile_mas_bootstrap(ctx: ApplyContext, config: dict, env_vars: dict) -> None:
    if env_vars.get("MAS_ENABLED") != "true":
        return

    if os.environ.get("MED_SKIP_EXTERNAL_BOOTSTRAP") == "1":
        print("MAS bootstrap: skipped (MED_SKIP_EXTERNAL_BOOTSTRAP=1)")
        return

    if not _postgres_container_running():
        print(
            "MAS bootstrap: deferred (matrix_postgres is not running; "
            "will run automatically when services start)"
        )
        return

    mas_config_path = ctx.project_root / "modules" / "mas" / "config.yaml"
    if not mas_config_path.exists():
        raise RuntimeError("MAS is enabled but modules/mas/config.yaml was not rendered")

    setup_script = ctx.project_root / "modules" / "mas" / "setup.sh"
    if not setup_script.exists():
        raise RuntimeError(f"MAS setup script missing: {setup_script}")

    env = dict(os.environ)
    env["MED_NON_INTERACTIVE"] = "1"
    for key, value in env_vars.items():
        if key in env or "\n" in str(value):
            continue
        env[key] = str(value)

    try:
        subprocess.run(["bash", str(setup_script)], check=True, env=env)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"MAS database bootstrap failed with exit code {exc.returncode}") from exc

    print("MAS bootstrap: database ready")


def _appservice_data_is_writable(directory: Path) -> bool:
    """True when this process can create and delete a file in directory."""
    probe = directory / ".med-apply-write-test"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok")
        probe.unlink()
        return True
    except PermissionError:
        return False
    except OSError:
        return False


def _chmod_appservice_data_privileged(directory: Path) -> bool:
    """Open up a Synapse-owned data dir so apply can sync appservice files.

    start.sh later restores uid 991 and mode 750/640.
    """
    target = str(directory.resolve())
    docker = shutil.which("docker")
    if docker:
        result = subprocess.run(
            [
                docker,
                "run",
                "--rm",
                "-v",
                f"{target}:/data",
                "alpine:3",
                "sh",
                "-c",
                "chmod -R a+rwX /data",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
    sudo = shutil.which("sudo")
    if sudo:
        result = subprocess.run(
            [sudo, "-n", "chmod", "-R", "a+rwX", target],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
    return False


def ensure_appservice_data_accessible(directory: Path) -> None:
    """Make homeserver appservice data writable for this apply process.

    Synapse bind-mounts this dir as uid 991 with mode 750, so an unprivileged
    apply user cannot even stat leftover registration files.
    """
    if _appservice_data_is_writable(directory):
        return
    if _chmod_appservice_data_privileged(directory) and _appservice_data_is_writable(directory):
        return
    raise PermissionError(
        f"Cannot access {directory} (Synapse data is typically owned by uid 991). "
        f"Fix with: sudo chmod -R a+rwX {directory} "
        "then re-run bash apply.sh. start.sh restores Synapse ownership."
    )


def reconcile_bridge_appservices(ctx: ApplyContext, config: dict) -> None:
    modules_cfg = config.get("modules", {}) if isinstance(config.get("modules", {}), dict) else {}
    hs_spec = homeserver.get_spec(config)
    bridge_specs = bridge_appservice_specs(hs_spec)
    failures: list[str] = []
    changes: list[str] = []

    homeserver_config = ctx.project_root / hs_spec.rendered_config_rel
    if not homeserver_config.exists():
        return

    ensure_appservice_data_accessible(ctx.project_root / hs_spec.appservice_data_rel)

    for config_key, spec in bridge_specs.items():
        desired = modules_cfg.get(config_key, {}) if isinstance(modules_cfg.get(config_key, {}), dict) else {}
        enabled = bool(desired.get("enabled", False))

        reg_src = ctx.project_root / spec["registration_src"]
        reg_dest = ctx.project_root / spec["registration_dest"]
        reg_path = str(spec["registration_path"])

        if enabled:
            if not reg_src.exists():
                failures.append(
                    f"{config_key}: missing registration source ({reg_src.relative_to(ctx.project_root)})"
                )
                continue

            bridge_config_rel = BRIDGE_CONFIG_PATHS.get(config_key)
            if bridge_config_rel:
                bridge_config = ctx.project_root / bridge_config_rel
                if bridge_config.exists():
                    if bridge_config_patch.ensure_msc4190_for_e2ee(bridge_config):
                        changes.append(f"{config_key}: enabled encryption.msc4190 for MAS-compatible E2EE")
                    if bridge_registration_patch.patch_registration(bridge_config, reg_src):
                        changes.append(f"{config_key}: patched registration for bridge E2EE compatibility")

            reg_dest.parent.mkdir(parents=True, exist_ok=True)
            needs_copy = not reg_dest.exists() or reg_src.read_bytes() != reg_dest.read_bytes()
            if needs_copy:
                reg_dest.write_bytes(reg_src.read_bytes())
                changes.append(f"{config_key}: synced {reg_dest.relative_to(ctx.project_root)}")

            if hs_spec.supports_synapse_appservice_yaml:
                if synapse_appservice.ensure_appservice_registration(homeserver_config, reg_path):
                    changes.append(f"{config_key}: ensured {reg_path} in homeserver.yaml")
            elif hs_spec.supports_tuwunel_appservice_dir:
                if tuwunel_appservice.sync_appservice_registration(
                    ctx.project_root / hs_spec.appservice_data_rel,
                    reg_src,
                    spec["registration_filename"],
                ):
                    changes.append(
                        f"{config_key}: synced {spec['registration_filename']} into tuwunel appservice_dir"
                    )
        else:
            if reg_dest.exists():
                reg_dest.unlink()
                changes.append(f"{config_key}: removed {reg_dest.relative_to(ctx.project_root)}")

            if hs_spec.supports_synapse_appservice_yaml:
                if synapse_appservice.remove_appservice_registration(homeserver_config, reg_path):
                    changes.append(f"{config_key}: removed {reg_path} from homeserver.yaml")
            elif hs_spec.supports_tuwunel_appservice_dir:
                if tuwunel_appservice.remove_appservice_registration(
                    ctx.project_root / hs_spec.appservice_data_rel,
                    spec["registration_filename"],
                ):
                    changes.append(
                        f"{config_key}: removed {spec['registration_filename']} from tuwunel appservice_dir"
                    )

    if changes:
        print("Bridge appservice reconciliation changes:")
        for item in changes:
            print(f"  - {item}")

    if failures:
        print("Bridge appservice reconciliation failures:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        raise RuntimeError("One or more bridge modules failed appservice reconciliation")


def reconcile_hookshot_caddy(ctx: ApplyContext, config: dict, derived: dict) -> None:
    modules_cfg = config.get("modules", {}) if isinstance(config.get("modules", {}), dict) else {}
    hookshot_cfg = modules_cfg.get("hookshot", {}) if isinstance(modules_cfg.get("hookshot", {}), dict) else {}
    enabled = bool(hookshot_cfg.get("enabled", False))

    caddyfile = ctx.project_root / "caddy" / "Caddyfile"
    if not caddyfile.exists():
        return

    content = caddyfile.read_text()
    hookshot_domain = str(derived.get("HOOKSHOT_DOMAIN", "") or "")
    if not hookshot_domain:
        existing_env = load_env_map(ctx.env_file)
        hookshot_domain = str(existing_env.get("HOOKSHOT_DOMAIN", "") or "")

    if enabled:
        if not hookshot_domain:
            raise RuntimeError("hookshot is enabled but HOOKSHOT_DOMAIN could not be resolved")
        updated = hookshot_caddy.upsert_hookshot_block(content, hookshot_domain)
    else:
        updated = hookshot_caddy.remove_hookshot_block(content, hookshot_domain or None)

    if updated != content:
        caddyfile.write_text(updated)
        if enabled:
            print("Hookshot Caddy reconciliation: ensured managed block")
        else:
            print("Hookshot Caddy reconciliation: removed managed block")


def apply_configuration(
    ctx: ApplyContext,
    server_ip: str | None = None,
    rotate_secrets: bool = False,
    reconcile_modules: bool = True,
) -> None:
    config = load_config(ctx)
    if mas_config.ensure_sso_provider_ids(config):
        save_config(ctx, config)
        print("Assigned stable upstream SSO provider IDs in deploy.yaml")
    for line in prepare_sso_config(ctx, config):
        print(line)
    validate_config(config)
    derived = derive_values(config, server_ip=server_ip)
    mas_enabled = derived.get("MAS_ENABLED") == "true"
    existing = load_secrets(ctx)
    backup_cfg = config.get("backup") if isinstance(config.get("backup"), dict) else {}
    saved = create_or_update_secrets(
        ctx,
        existing,
        rotate=rotate_secrets,
        mas_enabled=mas_enabled,
        backup_enabled=bool(backup_cfg.get("enabled", False)),
    )
    env_vars = build_env_vars(config, derived, saved)
    write_env_file(ctx, env_vars)
    link_calls_compose_env(ctx)
    render_templates(ctx, config, env_vars)
    reconcile_module_state(ctx, config)
    if reconcile_modules:
        if module_bootstrap_needs_postgres(ctx, config):
            print("Starting PostgreSQL prerequisite for module bootstrap…")
            ensure_postgres_prerequisite(ctx)
        reconcile_module_bootstrap(ctx, config)
        reconcile_mas_bootstrap(ctx, config, env_vars)
    reconcile_bridge_appservices(ctx, config)
    reconcile_hookshot_caddy(ctx, config, derived)
    reconcile_proxy_integration(ctx, config, derived)
    schedule_status = backup_schedule.reconcile(
        ctx.project_root, ctx.config_file, "matrix-easy-deploy-backup"
    )
    if schedule_status:
        print(schedule_status)

    if env_vars.get("GUEST_ACCESS_ENABLED") == "true":
        guest_call_domain = env_vars.get("GUEST_CALL_DOMAIN", "").strip()
        server_name = env_vars.get("SERVER_NAME", "").strip()
        print(
            "Guest call access enabled. Element Web is configured with "
            f"element_call.url and guest_spa_url → https://{guest_call_domain}"
        )
        print(
            "Share guest meeting links with: bash scripts/call-link.sh ROOM_ID "
            "(not the matrix.to room share button)."
        )
        guest_server_name = env_vars.get("GUEST_SERVER_NAME", "").strip()
        if guest_server_name:
            print(
                "Guest federation: Matrix hostnames resolve to Caddy on caddy_net "
                f"(caddy/docker-compose.guest.yml). After apply, verify with:\n"
                f"  docker exec matrix_synapse getent hosts {guest_server_name}\n"
                "  docker exec matrix_synapse curl -fsS "
                f"https://{guest_server_name}/.well-known/matrix/server\n"
                "  docker exec matrix_synapse curl -fsS "
                f"https://{guest_server_name}/_matrix/key/v2/server"
            )
        if env_vars.get("GUEST_ACCESS_TUWUNEL_MAIN_WARNING"):
            print(env_vars["GUEST_ACCESS_TUWUNEL_MAIN_WARNING"])


def wait_for_homeserver(ctx: ApplyContext, *, after_restart: bool = False) -> None:
    env = load_env_map(ctx.env_file)
    matrix_domain = env.get("MATRIX_DOMAIN", "").strip()
    if not matrix_domain:
        print("Auto-join rooms: MATRIX_DOMAIN not set; skipping homeserver readiness wait.")
        return

    url = f"https://{matrix_domain}/_matrix/client/versions"
    max_attempts = 30 if after_restart else 6
    interval_sec = 5
    print("Auto-join rooms: waiting for homeserver to become ready…")
    mode = env.get("PROXY_MODE", "standalone").strip().lower()
    container = env.get("HOMESERVER_CONTAINER", "matrix_synapse").strip() or "matrix_synapse"
    if ctx.config_file.is_file():
        config = load_config(ctx)
        mode = proxy_mode(config)
        container = homeserver.get_spec(config).container_name
    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status < 500:
                    print("Auto-join rooms: homeserver is ready.")
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                print("Auto-join rooms: homeserver is ready.")
                return
        except Exception:
            pass

        if mode == "integrate":
            inspect = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
                capture_output=True,
                text=True,
            )
            if inspect.stdout.strip() == "healthy":
                print("Auto-join rooms: homeserver is ready (internal health; shared Caddy may still be starting).")
                return

        if attempt >= max_attempts:
            raise RuntimeError(
                f"Homeserver at {url} did not become ready after {max_attempts * interval_sec}s "
                "(502/connection errors usually mean Synapse/Tuwunel is still starting). "
                "Wait for the stack to finish booting, then re-run bash apply.sh."
            )
        time.sleep(interval_sec)


def reconcile_auto_join_rooms(ctx: ApplyContext, config: dict, *, after_restart: bool = False) -> None:
    auto_join = get_auto_join_config(config.get("features", {}) if isinstance(config.get("features"), dict) else {})
    if not auto_join.get("rooms"):
        return

    med_admin = ctx.project_root / "scripts" / "med-admin.sh"
    if not med_admin.exists():
        raise RuntimeError(f"Missing med-admin script: {med_admin}")

    wait_for_homeserver(ctx, after_restart=after_restart)
    print("Auto-join rooms: provisioning via med-admin…")
    result = subprocess.run(
        [
            "bash",
            str(med_admin),
            "setup-auto-join-rooms",
            "--yes",
            "--deploy-yaml",
            str(ctx.config_file),
        ],
        cwd=str(ctx.project_root),
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or "med-admin setup-auto-join-rooms failed"
        raise RuntimeError(
            "Auto-join room provisioning failed. Ensure the homeserver is running "
            "and local password login is enabled (or pass med-admin --access-token). "
            f"Details: {msg}"
        )
    print("Auto-join rooms: provisioning complete.")


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
    reconcile_group = parser.add_mutually_exclusive_group()
    reconcile_group.add_argument(
        "--reconcile-runtime",
        dest="reconcile_runtime",
        action="store_true",
        help="After apply, restart services (stop/start) to match desired runtime state (default)",
    )
    reconcile_group.add_argument(
        "--no-reconcile-runtime",
        dest="reconcile_runtime",
        action="store_false",
        help="Apply config without restarting services",
    )
    parser.set_defaults(reconcile_runtime=True)
    parser.add_argument(
        "--skip-module-bootstrap",
        action="store_true",
        help="Do not run module setup scripts for enabled modules missing required generated config",
    )
    parser.add_argument(
        "--skip-auto-join-provision",
        action="store_true",
        help="Do not run med-admin setup-auto-join-rooms after apply",
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
    restarted = False
    if args.reconcile_runtime:
        run_runtime_reconcile(ctx)
        restarted = True
    if not args.skip_auto_join_provision:
        config = load_config(ctx)
        reconcile_auto_join_rooms(ctx, config, after_restart=restarted)
    print("Configuration applied successfully.")
    print("Generated .env file and rendered templates.")
    if args.reconcile_runtime:
        print("Runtime reconciled via stop/start.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
