#!/usr/bin/env python3
"""Matrix Authentication Service (MAS) config helpers for apply.py."""

from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

MAS_SYNAPSE_CLIENT_ID = "0000000000000000000SYNAPSE"
MAS_PATH_PREFIX = "/auth"
MAS_DOCKER_ASSETS_PATH = "/usr/local/share/mas-cli/assets/"
# Internal Docker DNS name for Synapse → MAS HTTP (no underscores; Synapse IDNA rejects them).
MAS_DOCKER_HOST = "matrix-mas"
MAS_DOCKER_ENDPOINT = f"http://{MAS_DOCKER_HOST}:8080/"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """Generate a Crockford-base32 ULID (26 characters)."""
    ms = int(time.time() * 1000)
    ts = ms.to_bytes(6, byteorder="big")
    rand = secrets.token_bytes(10)
    combined = ts + rand
    value = int.from_bytes(combined, byteorder="big")
    chars: list[str] = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def stable_provider_ulid(name: str, issuer: str) -> str:
    """Derive a stable ULID-like identifier for an upstream provider."""
    digest = hashlib.sha256(f"{name}\0{issuer}".encode()).digest()
    value = int.from_bytes(digest[:16], byteorder="big")
    chars: list[str] = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def mas_upstream_redirect_uri(mas_public_base: str, provider_id: str) -> str:
    """OAuth redirect URI registered with the upstream IdP."""
    base = mas_public_base.rstrip("/")
    return f"{base}/upstream/callback/{provider_id}"


KANIDM_SSO_NAME = "Kanidm"
DEFAULT_MATRIX_OIDC_CLIENT_ID = "matrix"
LOCAL_IDP_PROVIDERS = {"kanidm"}
# MAS defaults to RS256 (OIDC spec). Kanidm signs with ES256 unless legacy RSA is enabled.
KANIDM_ID_TOKEN_SIGNED_RESPONSE_ALG = "ES256"


def kanidm_issuer_url(kanidm_domain: str, client_id: str = DEFAULT_MATRIX_OIDC_CLIENT_ID) -> str:
    return f"https://{str(kanidm_domain).strip().rstrip('/')}/oauth2/openid/{client_id}"


def matrix_kanidm_provider_id(
    kanidm_domain: str, client_id: str = DEFAULT_MATRIX_OIDC_CLIENT_ID
) -> str:
    return stable_provider_ulid(KANIDM_SSO_NAME, kanidm_issuer_url(kanidm_domain, client_id))


def matrix_kanidm_redirect_uri(
    matrix_domain: str,
    kanidm_domain: str,
    client_id: str = DEFAULT_MATRIX_OIDC_CLIENT_ID,
) -> str:
    return mas_upstream_redirect_uri(
        mas_public_base(matrix_domain),
        matrix_kanidm_provider_id(kanidm_domain, client_id),
    )


def managed_is_false(section: dict | None) -> bool:
    value = (section or {}).get("managed")
    if value is False:
        return True
    return str(value or "").strip().lower() in {"false", "no", "0"}


def is_local_idp_sso_provider(provider: dict | None) -> bool:
    if not isinstance(provider, dict):
        return False
    name = str(provider.get("name") or "").strip().lower()
    client_id = str(provider.get("client_id") or "").strip().lower()
    issuer = str(provider.get("issuer") or "").strip().lower()
    if name in LOCAL_IDP_PROVIDERS:
        return True
    if client_id == DEFAULT_MATRIX_OIDC_CLIENT_ID or client_id.startswith("matrix-"):
        return True
    return "kanidm" in issuer or "/oauth2/openid/" in issuer


def is_kanidm_sso_provider(provider: dict | None) -> bool:
    return is_local_idp_sso_provider(provider)


SSO_DEFAULT_LOGIN_SSO = "sso"
SSO_DEFAULT_LOGIN_CHOOSER = "chooser"


def normalize_sso_default_login(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"sso", "idp", "kanidm"}:
        return SSO_DEFAULT_LOGIN_SSO
    if raw in {"chooser", "both", "local", "password"}:
        return SSO_DEFAULT_LOGIN_CHOOSER
    raise ValueError("features.sso.default_login must be 'sso' or 'chooser'")


def resolve_sso_default_login(config: dict) -> str:
    """Element login: Kanidm defaults to SSO-first; other IdPs keep the chooser."""
    features = config.get("features") if isinstance(config.get("features"), dict) else {}
    sso = features.get("sso") if isinstance(features.get("sso"), dict) else {}
    if not bool(sso.get("enabled")):
        return SSO_DEFAULT_LOGIN_CHOOSER
    providers = sso.get("providers") if isinstance(sso.get("providers"), list) else []
    if "default_login" in sso and sso.get("default_login") not in (None, ""):
        return normalize_sso_default_login(sso.get("default_login"))
    provider = str(sso.get("provider") or "").strip().lower()
    if provider in LOCAL_IDP_PROVIDERS or any(is_local_idp_sso_provider(item) for item in providers):
        return SSO_DEFAULT_LOGIN_SSO
    return SSO_DEFAULT_LOGIN_CHOOSER


MATRIX_OIDC_SIDECAR_HEADER = (
    "# Generated for Kanidm OIDC. Secrets stay here, not in deploy.yaml.\n"
    "# Set features.sso.managed: false to ignore.\n"
)
KANIDM_CLIENT_SIDECAR_HEADER = (
    "# Generated for Matrix MAS. Re-apply Kanidm after this file changes.\n"
    "# Set oidc.managed: false to ignore kit/engine client sidecars.\n"
)


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return not value
    return False


def plaintext_oidc_secret(value: str) -> str:
    secret = str(value or "").strip()
    prefix = "$plaintext$"
    if secret.startswith(prefix):
        return secret[len(prefix) :]
    return secret


def write_oidc_sidecar(path: Path, data: dict, *, header: str = MATRIX_OIDC_SIDECAR_HEADER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    path.write_text(header + body)


def build_mas_kanidm_provider(
    *,
    kanidm_domain: str,
    client_id: str,
    client_secret: str = "",
) -> dict[str, Any]:
    issuer = kanidm_issuer_url(kanidm_domain, client_id)
    provider = {
        "provider": "kanidm",
        "name": KANIDM_SSO_NAME,
        "issuer": issuer,
        "client_id": client_id,
        "id": matrix_kanidm_provider_id(kanidm_domain, client_id),
        "allow_registration": True,
        "scopes": ["openid", "profile", "email"],
    }
    if client_secret:
        provider["client_secret"] = client_secret
    return provider


def build_kanidm_matrix_client(
    *,
    matrix_domain: str,
    kanidm_domain: str,
    client_id: str,
    element_domain: str = "",
) -> dict[str, Any]:
    origin = f"https://{matrix_domain.rstrip('/')}"
    app_host = str(element_domain or matrix_domain).strip().rstrip("/")
    app = f"https://{app_host}"
    client = {
        "client_id": client_id,
        "client_name": "Matrix",
        "public": False,
        "prefer_short_username": True,
        "landing_url": app,
        "redirect_uris": [matrix_kanidm_redirect_uri(matrix_domain, kanidm_domain, client_id)],
        "scopes": ["openid", "profile", "email"],
    }
    if str(element_domain or "").strip():
        # Element's tab icon is ICO; Kanidm only accepts PNG/SVG/WebP/JPEG/GIF.
        client["image"] = f"{app}/vector-icons/144.png"
    return client


def apply_engine_oidc_sidecar(config: dict, sidecar_path: Path | None = None) -> None:
    """Merge Kanidm OIDC settings from a sidecar. Operator deploy.yaml wins when set."""
    if sidecar_path is None or not sidecar_path.is_file():
        return
    sidecar = yaml.safe_load(sidecar_path.read_text()) or {}
    if not isinstance(sidecar, dict):
        return
    features = config.setdefault("features", {})
    if not isinstance(features, dict):
        return
    sso = features.setdefault("sso", {})
    if not isinstance(sso, dict):
        return
    if managed_is_false(sso):
        return
    existing_provider = str(sso.get("provider") or "").strip().lower()
    if existing_provider and existing_provider not in LOCAL_IDP_PROVIDERS:
        return
    providers = sso.get("providers")
    if not isinstance(providers, list):
        providers = []
        sso["providers"] = providers
    if providers and not any(is_local_idp_sso_provider(item) for item in providers):
        if existing_provider not in LOCAL_IDP_PROVIDERS:
            return

    entry = {
        key: value
        for key, value in sidecar.items()
        if key not in {"provider", "managed"} and value not in (None, "")
    }
    if not entry.get("issuer") or not entry.get("client_id") or not entry.get("client_secret"):
        return

    match_index = None
    for index, provider in enumerate(providers):
        if is_local_idp_sso_provider(provider):
            match_index = index
            break
    if match_index is None:
        providers.append(entry)
    else:
        merged = dict(providers[match_index])
        for key, value in entry.items():
            if _blank(merged.get(key)):
                merged[key] = value
        providers[match_index] = merged

    sso["enabled"] = True
    sso.setdefault("provider", "kanidm")


def ensure_sso_provider_ids(config: dict) -> bool:
    """Assign stable ULIDs to SSO providers missing an id; persist via apply."""
    features = config.get("features")
    if not isinstance(features, dict):
        return False

    sso = features.get("sso")
    if not isinstance(sso, dict):
        return False

    providers = sso.get("providers")
    if not isinstance(providers, list):
        return False

    changed = False
    seen_ids: set[str] = set()
    for index, provider in enumerate(providers):
        if not isinstance(provider, dict):
            continue

        path = f"features.sso.providers[{index}]"
        raw_id = provider.get("id")
        if isinstance(raw_id, str) and raw_id.strip():
            provider_id = raw_id.strip()
            if len(provider_id) != 26:
                raise ValueError(f"{path}.id must be a 26-character ULID when set")
            if provider_id in seen_ids:
                raise ValueError(f"duplicate features.sso.providers id: {provider_id}")
            seen_ids.add(provider_id)
            if raw_id != provider_id:
                provider["id"] = provider_id
                changed = True
            continue

        name = str(provider.get("name", "OIDC"))
        issuer = str(provider.get("issuer", ""))
        provider_id = stable_provider_ulid(name, issuer)
        if provider_id in seen_ids:
            provider_id = stable_provider_ulid(f"{name}\0{index}", issuer)
        if provider_id in seen_ids:
            raise ValueError(f"could not assign a unique id for {path}")
        provider["id"] = provider_id
        seen_ids.add(provider_id)
        changed = True

    return changed


def mas_public_base(matrix_domain: str, path_prefix: str = MAS_PATH_PREFIX) -> str:
    """Absolute public URL base for MAS (includes path prefix when set)."""
    normalized = path_prefix if path_prefix.startswith("/") else f"/{path_prefix}"
    normalized = normalized.rstrip("/") or MAS_PATH_PREFIX
    if normalized == "/":
        return f"https://{matrix_domain}/"
    return f"https://{matrix_domain}{normalized}/"


def caddy_mas_block(path_prefix: str = MAS_PATH_PREFIX) -> str:
    """Caddy routes for MAS on the Matrix vhost.

    MAS advertises URLs under public_base (/auth/…) but serves routes at the
    listener root, so /auth/* requests are path-stripped before proxying.
    OIDC discovery stays at /.well-known/openid-configuration on SERVER_NAME.
    """
    prefix = path_prefix if path_prefix.startswith("/") else f"/{path_prefix}"
    prefix = prefix.rstrip("/") or MAS_PATH_PREFIX
    return (
        "\n    # Matrix Authentication Service (OIDC, QR login)\n"
        f"    @mas_compat path_regexp ^/_matrix/client/[^/]+/(login|logout|refresh)$\n"
        "    handle @mas_compat {\n"
        "        reverse_proxy matrix_mas:8080\n"
        "    }\n"
        "    handle /.well-known/openid-configuration {\n"
        "        reverse_proxy matrix_mas:8080\n"
        "    }\n"
        f"    handle_path {prefix}/* {{\n"
        "        reverse_proxy matrix_mas:8080\n"
        "    }\n"
    )


def build_caddy_element_routing(
    *,
    matrix_domain: str,
    server_name: str,
    element_enabled: bool,
    element_domain: str,
    frame_ancestors: list[str] | None = None,
) -> dict[str, str]:
    """Place Element in the Matrix site block when it shares a hostname."""
    ancestors = unique_https_origins(frame_ancestors)
    if not element_enabled or not element_domain:
        return {
            "CADDY_ELEMENT_MATRIX_FALLBACK": "",
            "CADDY_ELEMENT_SITE_BLOCK": "",
        }

    matrix_hosts = {matrix_domain}
    if server_name != matrix_domain:
        matrix_hosts.add(server_name)

    proxy = caddy_element_proxy_and_headers(ancestors)
    element_fallback = (
        "\n    # Element web client (same host as Matrix API)\n"
        "    handle /config.json {\n"
        '        header Cache-Control "no-cache"\n'
        f"{proxy}\n"
        "    }\n\n"
        "    handle {\n"
        f"{proxy}\n"
        "    }\n"
    )

    if element_domain in matrix_hosts:
        return {
            "CADDY_ELEMENT_MATRIX_FALLBACK": element_fallback,
            "CADDY_ELEMENT_SITE_BLOCK": "",
        }

    clickjacking = caddy_clickjacking_header_lines(ancestors)
    element_site = (
        f"\n# Element web client — served on its own domain\n"
        f"{element_domain} {{\n"
        "    handle /config.json {\n"
        '        header Cache-Control "no-cache"\n'
        f"{proxy}\n"
        "    }\n\n"
        "    handle {\n"
        f"{proxy}\n"
        "    }\n\n"
        "    header {\n"
        "        X-Content-Type-Options nosniff\n"
        f"{clickjacking}\n"
        "        Referrer-Policy strict-origin-when-cross-origin\n"
        '        Permissions-Policy "interest-cohort=()"\n'
        "        -Server\n"
        "    }\n\n"
        "    encode gzip\n"
        "    log\n"
        "}\n"
    )
    return {
        "CADDY_ELEMENT_MATRIX_FALLBACK": "",
        "CADDY_ELEMENT_SITE_BLOCK": element_site,
    }


def https_origin(value: Any) -> str:
    """Normalize a hostname or URL to `https://host` with no trailing slash."""
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" in text:
        scheme, rest = text.split("://", 1)
        if scheme.lower() not in {"http", "https"}:
            return ""
        host = rest.split("/")[0].split("?")[0].split("#")[0].strip().lower()
    else:
        host = text.split("/")[0].split("?")[0].split("#")[0].strip().lower()
    if not host or host in {"webmail.example.com", "example.com", "element.example.com"}:
        return ""
    return f"https://{host}"


def unique_https_origins(values: Any) -> list[str]:
    origins: list[str] = []
    seen: set[str] = set()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return origins
    for item in values:
        origin = https_origin(item)
        if origin and origin not in seen:
            seen.add(origin)
            origins.append(origin)
    return origins


def caddy_element_proxy_and_headers(frame_ancestors: list[str], indent: str = "        ") -> str:
    """reverse_proxy Element, replacing clickjacking headers when extra parents exist."""
    if not frame_ancestors:
        return f"{indent}reverse_proxy matrix_element:80"
    inner = indent + "    "
    origins = " ".join(["'self'", *frame_ancestors])
    return (
        f"{indent}reverse_proxy matrix_element:80 {{\n"
        f"{inner}header_down -Content-Security-Policy\n"
        f"{inner}header_down -X-Frame-Options\n"
        f"{indent}}}\n"
        f"{indent}header -X-Frame-Options\n"
        f'{indent}header Content-Security-Policy "frame-ancestors {origins}"'
    )


def caddy_clickjacking_header_lines(frame_ancestors: list[str], indent: str = "        ") -> str:
    if not frame_ancestors:
        return f"{indent}X-Frame-Options SAMEORIGIN"
    origins = " ".join(["'self'", *frame_ancestors])
    return (
        f"{indent}-X-Frame-Options\n"
        f'{indent}Content-Security-Policy "frame-ancestors {origins}"'
    )


def extract_base_domain(fqdn: str) -> str:
    parts = fqdn.split(".")
    if len(parts) >= 3:
        return ".".join(parts[1:])
    return fqdn


def get_sso_config(features: dict) -> dict[str, Any]:
    sso = features.get("sso", {}) if isinstance(features.get("sso", {}), dict) else {}
    return {
        "enabled": bool(sso.get("enabled", False)),
        "providers": list(sso.get("providers") or []) if isinstance(sso.get("providers", []), list) else [],
    }


def migrate_legacy_mas_features(features: dict) -> list[str]:
    """Map deprecated features.mas into features.sso for one release."""
    warnings: list[str] = []
    if not isinstance(features, dict):
        return warnings

    mas = features.get("mas")
    if mas is None:
        return warnings

    if not isinstance(mas, dict):
        features.pop("mas", None)
        return warnings

    if "sso" in features and features.get("sso") is not None:
        raise ValueError(
            "deploy.yaml contains both features.sso and features.mas; "
            "remove features.mas and use features.sso.providers"
        )

    warnings.append("features.mas is deprecated; use features.sso and features.local_login_enabled")
    sso = features.setdefault("sso", {})
    if not isinstance(sso, dict):
        sso = {}
        features["sso"] = sso

    upstream = mas.get("upstream_providers")
    if upstream and not sso.get("providers"):
        sso["providers"] = list(upstream)
    if bool(mas.get("enabled", False)) and upstream:
        sso["enabled"] = True

    if "local_login_enabled" in mas and "local_login_enabled" not in features:
        features["local_login_enabled"] = mas["local_login_enabled"]

    features.pop("mas", None)
    return warnings


def resolve_mas_runtime_config(config: dict) -> dict[str, Any]:
    """Derive internal MAS settings from the public deploy.yaml auth surface."""
    features = config.get("features", {}) if isinstance(config.get("features", {}), dict) else {}
    matrix = config.get("matrix", {}) if isinstance(config.get("matrix", {}), dict) else {}
    matrix_domain = matrix.get("domain", "matrix.example.com")
    server_name = matrix.get("server_name") or extract_base_domain(str(matrix_domain))

    from scripts import homeserver

    hs_impl = homeserver.normalize_implementation(matrix.get("server_implementation", "synapse"))
    sso = get_sso_config(features)
    local_login_enabled = bool(features.get("local_login_enabled", True))
    # Kanidm-first: hide MAS password login so it auto-redirects to the only upstream IdP.
    if resolve_sso_default_login(config) == SSO_DEFAULT_LOGIN_SSO:
        local_login_enabled = False

    enabled = hs_impl == "synapse"
    return {
        "enabled": enabled,
        "domain": str(matrix_domain),
        "path_prefix": MAS_PATH_PREFIX,
        "local_login_enabled": local_login_enabled,
        "upstream_providers": list(sso["providers"]) if sso["enabled"] else [],
    }


def validate_sso_config(config: dict) -> None:
    from scripts import homeserver

    features = config.get("features", {}) if isinstance(config.get("features", {}), dict) else {}
    matrix = config.get("matrix", {}) if isinstance(config.get("matrix", {}), dict) else {}
    hs_impl = homeserver.normalize_implementation(matrix.get("server_implementation", "synapse"))

    if "local_login_enabled" in features and not isinstance(features.get("local_login_enabled"), bool):
        raise ValueError("features.local_login_enabled must be true/false")

    sso = features.get("sso", {}) if isinstance(features.get("sso", {}), dict) else {}
    if "sso" in features and sso is not None and not isinstance(sso, dict):
        raise ValueError("features.sso must be an object")

    if sso:
        if "enabled" in sso and not isinstance(sso.get("enabled"), bool):
            raise ValueError("features.sso.enabled must be true/false")
        if "providers" in sso and not isinstance(sso.get("providers"), list):
            raise ValueError("features.sso.providers must be a list")
        if sso.get("default_login") not in (None, ""):
            normalize_sso_default_login(sso.get("default_login"))

    if "mas" in features:
        raise ValueError("features.mas is no longer supported; use features.sso instead")

    local_login_enabled = bool(features.get("local_login_enabled", True))
    sso_enabled = bool(sso.get("enabled", False))
    providers = sso.get("providers", []) if isinstance(sso.get("providers", []), list) else []

    if not local_login_enabled:
        if not sso_enabled:
            raise ValueError("features.local_login_enabled=false requires features.sso.enabled=true")
        if not providers:
            raise ValueError(
                "features.local_login_enabled=false requires at least one features.sso.providers entry"
            )

    if hs_impl != "synapse" and sso_enabled and providers:
        raise ValueError("features.sso is only supported with matrix.server_implementation=synapse")

    seen_ids: set[str] = set()
    for index, provider in enumerate(providers):
        path = f"features.sso.providers[{index}]"
        if not isinstance(provider, dict):
            raise ValueError(f"{path} must be an object")
        for key in ("name", "issuer", "client_id", "client_secret"):
            value = provider.get(key)
            if sso_enabled and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{path}.{key} must be a non-empty string when features.sso.enabled=true")
        if "allow_registration" in provider and not isinstance(provider.get("allow_registration"), bool):
            raise ValueError(f"{path}.allow_registration must be true/false")
        if "id" in provider and provider.get("id") not in (None, ""):
            pid = provider.get("id")
            if not isinstance(pid, str) or len(pid.strip()) != 26:
                raise ValueError(f"{path}.id must be a 26-character ULID when set")
            pid = pid.strip()
            if pid in seen_ids:
                raise ValueError(f"duplicate features.sso.providers id: {pid}")
            seen_ids.add(pid)
        elif sso_enabled:
            raise ValueError(f"{path}.id is required when features.sso.enabled=true; run apply.sh to assign provider IDs")


def _oauth_scope_string(scopes: Any) -> str:
    """MAS expects scope as a space-separated string, not a YAML sequence."""
    default = "openid profile email"
    if isinstance(scopes, str):
        return scopes.strip() or default
    if isinstance(scopes, (list, tuple)):
        parts = [str(item).strip() for item in scopes if str(item).strip()]
        return " ".join(parts) if parts else default
    return default


def build_mas_upstream_oauth2_yaml(providers: list, mas_public_base: str) -> str:
    if not providers:
        return "upstream_oauth2:\n  providers: []\n"

    base = mas_public_base.rstrip("/")
    entries: list[dict[str, Any]] = []
    for index, provider in enumerate(providers):
        if not isinstance(provider, dict):
            continue
        name = provider.get("name", "OIDC")
        issuer = provider.get("issuer", "")
        provider_id = provider.get("id")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError(
                f"features.sso.providers[{index}] is missing id; run apply.sh to assign stable provider IDs"
            )
        provider_id = provider_id.strip()
        entry: dict[str, Any] = {
            "id": provider_id,
            "issuer": issuer,
            "human_name": name,
            "client_id": provider.get("client_id", ""),
            "client_secret": provider.get("client_secret", ""),
            "token_endpoint_auth_method": "client_secret_basic",
            "scope": _oauth_scope_string(provider.get("scopes", ["openid", "profile", "email"])),
            "claims_imports": {
                "localpart": {"action": "ignore"},
                "displayname": {"action": "suggest", "template": "{{ user.name }}"},
                "email": {"action": "suggest", "template": "{{ user.email }}"},
                "account_name": {"template": "{{ user.email }}"},
            },
            "redirect_uri": mas_upstream_redirect_uri(base, provider_id),
        }
        brand = provider.get("brand_name")
        if isinstance(brand, str) and brand.strip():
            entry["brand_name"] = brand.strip()
        alg = str(provider.get("id_token_signed_response_alg") or "").strip()
        if not alg and is_kanidm_sso_provider(provider):
            alg = KANIDM_ID_TOKEN_SIGNED_RESPONSE_ALG
        if alg:
            entry["id_token_signed_response_alg"] = alg
        entries.append(entry)

    payload = {"upstream_oauth2": {"providers": entries}}
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)


def build_mas_signing_keys_yaml(keys: list[dict[str, str]]) -> str:
    lines = ["secrets:", f"  encryption: {keys[0]['encryption']}", "  keys:"]
    for item in keys[0]["signing_keys"]:
        lines.append(f"    - kid: \"{item['kid']}\"")
        lines.append("      key: |")
        for key_line in item["key"].strip().splitlines():
            lines.append(f"        {key_line}")
    return "\n".join(lines) + "\n"


def _parse_generated_mas_config(raw: str | None) -> dict[str, Any]:
    if not raw or not str(raw).strip():
        raise ValueError("mas-cli config generate returned empty output")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("mas-cli config generate returned invalid YAML")
    return data


def _normalize_pem_private_key(key_material: str) -> str:
    """Keep only private-key PEM blocks (drop EC PARAMETERS wrappers from openssl ecparam)."""
    text = str(key_material).strip()
    if "-----BEGIN" not in text:
        return text

    blocks: list[str] = []
    current: list[str] = []
    in_block = False
    for line in text.splitlines():
        if line.startswith("-----BEGIN"):
            in_block = True
            current = [line]
            continue
        if line.startswith("-----END"):
            if in_block:
                current.append(line)
                block = "\n".join(current)
                if "PRIVATE KEY" in block:
                    blocks.append(block)
            in_block = False
            current = []
            continue
        if in_block:
            current.append(line)

    if blocks:
        return "\n".join(blocks)
    return text


def _normalize_signing_keys(keys: list) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(keys):
        if not isinstance(item, dict):
            continue
        key_material = item.get("key") or item.get("private_key")
        if not key_material:
            continue
        normalized.append(
            {
                "kid": str(item.get("kid") or f"key{index + 1}"),
                "key": _normalize_pem_private_key(str(key_material)),
            }
        )
    return normalized


def _mas_signing_keys_usable(keys: list) -> bool:
    normalized = _normalize_signing_keys(keys)
    if not normalized:
        return False

    has_signing_key = False
    for item in normalized:
        key = item.get("key", "")
        if not key or "placeholder-test-key" in key:
            return False
        if "-----BEGIN EC PARAMETERS-----" in key:
            return False
        if "PRIVATE KEY" not in key:
            return False
        has_signing_key = True
    return has_signing_key


def _generate_mas_signing_material_stub() -> dict[str, Any]:
    return {
        "MAS_ENCRYPTION_SECRET": secrets.token_hex(32),
        "MAS_SIGNING_KEYS": [
            {
                "kid": "rsa1",
                "key": (
                    "-----BEGIN RSA PRIVATE KEY-----\n"
                    "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PRYMXC0xxF6KXP2R7YHkqxv\n"
                    "x/placeholder-test-key-not-for-production-use-only\n"
                    "-----END RSA PRIVATE KEY-----"
                ),
            },
            {
                "kid": "ec1",
                "key": (
                    "-----BEGIN EC PRIVATE KEY-----\n"
                    "MHcCAQEEIE8yeUh111Npqu2e5wXxjC/GA5lbGe0j0KVXqZP12vqioAcGBSuBBAAK\n"
                    "oUQDQgAESKfUtKaLqCfhK+p3z870W59yOYvd+kjGWe+tK16SmWzZJbRCgdHakHE5\n"
                    "MC6tJRnvedsYoKTrYoDv/XZIBI9zlA==\n"
                    "-----END EC PRIVATE KEY-----"
                ),
            },
        ],
    }


def _generate_mas_signing_material_openssl() -> dict[str, Any]:
    rsa = subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    if not rsa.stdout.strip():
        raise RuntimeError("openssl produced no RSA private key output")
    return {
        "MAS_ENCRYPTION_SECRET": secrets.token_hex(32),
        "MAS_SIGNING_KEYS": [
            {"kid": "rsa1", "key": _normalize_pem_private_key(rsa.stdout)},
        ],
    }


def _generate_mas_signing_material_docker() -> dict[str, Any]:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "ghcr.io/element-hq/matrix-authentication-service:1.26.0@sha256:e089f1048a1d4a9a492ed17b9fe759100f1bd619407b001f5927928d88b780c4",
            "config",
            "generate",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    generated = _parse_generated_mas_config(result.stdout)
    secrets_block = generated.get("secrets", {})
    if not isinstance(secrets_block, dict):
        raise ValueError("generated config missing secrets block")
    encryption = secrets_block.get("encryption")
    keys = _normalize_signing_keys(secrets_block.get("keys", []))
    if not encryption or not keys:
        raise ValueError("generated config missing encryption or signing keys")
    return {
        "MAS_ENCRYPTION_SECRET": str(encryption),
        "MAS_SIGNING_KEYS": keys,
    }


def generate_mas_signing_material() -> dict[str, Any]:
    """Generate MAS encryption secret and signing keys via mas-cli or openssl."""
    if os.environ.get("MED_ALLOW_INSECURE_MAS_KEYS", "").strip() == "1":
        return _generate_mas_signing_material_stub()

    if os.environ.get("MED_MAS_USE_DOCKER_GENERATE", "").strip() != "0":
        try:
            return _generate_mas_signing_material_docker()
        except (subprocess.SubprocessError, ValueError, OSError, yaml.YAMLError):
            pass

    try:
        return _generate_mas_signing_material_openssl()
    except (subprocess.SubprocessError, OSError, RuntimeError) as exc:
        raise RuntimeError(
            "Failed to generate MAS signing keys. Ensure openssl is installed "
            "or Docker is available to run mas-cli config generate."
        ) from exc


def ensure_mas_secrets(state: dict, *, rotate: bool = False, mas_enabled: bool = False) -> dict:
    updated = dict(state)
    for key in ("MAS_DB_PASSWORD", "MAS_HOMESERVER_SECRET", "MAS_SYNAPSE_CLIENT_SECRET"):
        if rotate or not updated.get(key):
            updated[key] = secrets.token_hex(32)

    needs_keys = rotate or not updated.get("MAS_ENCRYPTION_SECRET") or not updated.get("MAS_SIGNING_KEYS")
    if mas_enabled and not needs_keys and not _mas_signing_keys_usable(updated.get("MAS_SIGNING_KEYS", [])):
        needs_keys = True
    if mas_enabled and needs_keys:
        material = generate_mas_signing_material()
        updated["MAS_ENCRYPTION_SECRET"] = material["MAS_ENCRYPTION_SECRET"]
        updated["MAS_SIGNING_KEYS"] = material["MAS_SIGNING_KEYS"]
    return updated


def build_mas_signing_keys_yaml_from_state(state: dict) -> str:
    keys = state.get("MAS_SIGNING_KEYS")
    encryption = state.get("MAS_ENCRYPTION_SECRET", "")
    if not isinstance(keys, list) or not encryption:
        return "secrets:\n  encryption: \"\"\n  keys: []\n"
    payload = {
        "encryption": encryption,
        "signing_keys": _normalize_signing_keys(keys),
    }
    return build_mas_signing_keys_yaml([payload])


def build_synapse_mas_sections(*, enabled: bool, server_name: str, mas_public_base: str, secrets: dict) -> dict[str, str]:
    if not enabled:
        return {
            "SYNAPSE_MAS_AUTH_SERVICE_SECTION": "",
            "SYNAPSE_MAS_EXPERIMENTAL_SECTION": "",
            "SYNAPSE_MAS_WELL_KNOWN_SECTION": "",
            "SYNAPSE_OIDC_PROVIDERS": "[]",
        }

    admin_token = secrets.get("MAS_HOMESERVER_SECRET", "")
    account_base = mas_public_base.rstrip("/")
    auth_service = "\n".join(
        [
            "matrix_authentication_service:",
            "  enabled: true",
            '  endpoint: "' + MAS_DOCKER_ENDPOINT + '"',
            f'  secret: "{admin_token}"',
        ]
    )
    experimental = "\n".join(
        [
            "  msc4108_enabled: true",
        ]
    )
    well_known = "\n".join(
        [
            "  org.matrix.msc2965.authentication:",
            f"    issuer: https://{server_name}/",
            f"    account: {account_base}/account",
        ]
    )
    return {
        "SYNAPSE_MAS_AUTH_SERVICE_SECTION": auth_service,
        "SYNAPSE_MAS_EXPERIMENTAL_SECTION": experimental,
        "SYNAPSE_MAS_WELL_KNOWN_SECTION": well_known,
        "SYNAPSE_OIDC_PROVIDERS": "[]",
    }


def emit_migration_warnings(warnings: list[str]) -> None:
    for message in warnings:
        print(f"Warning: {message}", file=sys.stderr)
