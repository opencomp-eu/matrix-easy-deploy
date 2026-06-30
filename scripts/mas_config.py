#!/usr/bin/env python3
"""Matrix Authentication Service (MAS) config helpers for apply.py."""

from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import sys
import time
from typing import Any

import yaml

MAS_SYNAPSE_CLIENT_ID = "0000000000000000000SYNAPSE"
MAS_PATH_PREFIX = "/auth"
MAS_DOCKER_ASSETS_PATH = "/usr/local/share/mas-cli/assets/"
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
) -> dict[str, str]:
    """Place Element in the Matrix site block when it shares a hostname."""
    if not element_enabled or not element_domain:
        return {
            "CADDY_ELEMENT_MATRIX_FALLBACK": "",
            "CADDY_ELEMENT_SITE_BLOCK": "",
        }

    matrix_hosts = {matrix_domain}
    if server_name != matrix_domain:
        matrix_hosts.add(server_name)

    element_fallback = (
        "\n    # Element web client (same host as Matrix API)\n"
        "    handle {\n"
        "        reverse_proxy matrix_element:80\n"
        "    }\n"
    )

    if element_domain in matrix_hosts:
        return {
            "CADDY_ELEMENT_MATRIX_FALLBACK": element_fallback,
            "CADDY_ELEMENT_SITE_BLOCK": "",
        }

    element_site = (
        f"\n# Element web client — served on its own domain\n"
        f"{element_domain} {{\n"
        "    handle {\n"
        "        reverse_proxy matrix_element:80\n"
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
    return {
        "CADDY_ELEMENT_MATRIX_FALLBACK": "",
        "CADDY_ELEMENT_SITE_BLOCK": element_site,
    }


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
                "localpart": {"action": "require", "template": "{{ user.preferred_username }}"},
                "displayname": {"action": "suggest", "template": "{{ user.name }}"},
                "email": {"action": "suggest", "template": "{{ user.email }}"},
            },
            "redirect_uri": mas_upstream_redirect_uri(base, provider_id),
        }
        brand = provider.get("brand_name")
        if isinstance(brand, str) and brand.strip():
            entry["brand_name"] = brand.strip()
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
            "ghcr.io/element-hq/matrix-authentication-service:latest",
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
            "SYNAPSE_MAS_EXPERIMENTAL_SECTION": "",
            "SYNAPSE_MAS_WELL_KNOWN_SECTION": "",
            "SYNAPSE_OIDC_PROVIDERS": "[]",
        }

    client_secret = secrets.get("MAS_SYNAPSE_CLIENT_SECRET", "")
    admin_token = secrets.get("MAS_HOMESERVER_SECRET", "")
    account_base = mas_public_base.rstrip("/")
    experimental = "\n".join(
        [
            "  msc3861:",
            "    enabled: true",
            f"    issuer: https://{server_name}/",
            f"    client_id: {MAS_SYNAPSE_CLIENT_ID}",
            "    client_auth_method: client_secret_basic",
            f'    client_secret: "{client_secret}"',
            f'    admin_token: "{admin_token}"',
            f'    account_management_url: "{account_base}/account"',
            "  msc4108_enabled: true",
            "  msc4190_enabled: true",
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
        "SYNAPSE_MAS_EXPERIMENTAL_SECTION": experimental,
        "SYNAPSE_MAS_WELL_KNOWN_SECTION": well_known,
        "SYNAPSE_OIDC_PROVIDERS": "[]",
    }


def emit_migration_warnings(warnings: list[str]) -> None:
    for message in warnings:
        print(f"Warning: {message}", file=sys.stderr)
