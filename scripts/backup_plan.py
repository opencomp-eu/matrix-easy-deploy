#!/usr/bin/env python3
"""Dynamic backup plan for matrix-easy-deploy (shared easydeploy-lib schema).

Loaded by easydeploy-lib/python/backup_plan.py::load_plan, which calls
resolve_plan(project_root) and normalizes/validates the result. Compatibility:
existing Borg archives (MED_Backup_*) and portable files (manifest format 1/2)
keep restoring because legacy_persistent_paths mirrors the flat payload layout
the pre-rework pipeline produced.
"""

from __future__ import annotations

from pathlib import Path

import yaml

SERVICE = "matrix"
ARCHIVE_PREFIX = "MED_Backup"
TIMER_NAME = "matrix-easy-deploy-backup"
STATE_DIR = ".matrix-easy-deploy"
SECRETS_FILE = ".matrix-easy-deploy/secrets.yaml"

HOOKS = {
    "apply": "apply.sh",
    "stop": "stop.sh",
    "start": "start.sh",
    "postgres_start": "scripts/postgres_prerequisite.sh",
}

BASE_PERSISTENT_PATHS = [
    "deploy.yaml",
    ".matrix-easy-deploy/secrets.yaml",
    ".matrix-easy-deploy/modules.yaml",
]

MODULE_PERSISTENT_PATHS = {
    "hookshot": "modules/hookshot/hookshot",
    "whatsapp_bridge": "modules/whatsapp-bridge/whatsapp",
    "slack_bridge": "modules/slack-bridge/slack",
    "draupnir": "modules/draupnir/draupnir",
}

TUWUNEL_DATA_PATH = "modules/core/tuwunel_data"
GUEST_TUWUNEL_DATA_PATH = "modules/calls/guest/tuwunel_data"

CADDY_VOLUMES = ["caddy_data", "caddy_caddy_config"]

# Flat payload layout produced by the pre-rework pipeline (manifest format <= 2).
# Keep in sync with what old MED_Backup archives contain; format 3 restores use
# the manifest "files" list instead.
LEGACY_PERSISTENT_PATHS = [
    "deploy.yaml",
    ".matrix-easy-deploy/secrets.yaml",
    ".matrix-easy-deploy/modules.yaml",
    "modules/core/synapse_data",
    "modules/core/tuwunel_data",
    "modules/hookshot/hookshot",
    "modules/whatsapp-bridge/whatsapp",
    "modules/slack-bridge/slack",
    "modules/draupnir/draupnir",
]

POSTGRES_CONTAINER = "matrix_postgres"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {}


def _module_enabled(deploy: dict, key: str) -> bool:
    modules = deploy.get("modules")
    if not isinstance(modules, dict):
        return False
    cfg = modules.get(key)
    if not isinstance(cfg, dict):
        return False
    enabled = cfg.get("enabled")
    return enabled is True


def resolve_server_implementation(deploy: dict) -> str:
    matrix = deploy.get("matrix")
    if not isinstance(matrix, dict):
        return "synapse"
    impl = str(matrix.get("server_implementation", "synapse")).strip().lower() or "synapse"
    return impl if impl in {"synapse", "tuwunel"} else "synapse"


def _guest_access_enabled(deploy: dict) -> bool:
    features = deploy.get("features")
    if not isinstance(features, dict):
        return False
    calls = features.get("calls")
    if not isinstance(calls, dict) or not calls.get("enabled", True):
        return False
    guest = calls.get("guest_access")
    if not isinstance(guest, dict):
        return False
    return guest.get("enabled") is True


def resolve_persistent_paths(deploy: dict) -> list[dict]:
    paths = list(BASE_PERSISTENT_PATHS)
    if resolve_server_implementation(deploy) == "tuwunel":
        paths.append(TUWUNEL_DATA_PATH)
    else:
        paths.append("modules/core/synapse_data")
    for module_key, path in MODULE_PERSISTENT_PATHS.items():
        if _module_enabled(deploy, module_key):
            paths.append(path)
    if _guest_access_enabled(deploy):
        paths.append(GUEST_TUWUNEL_DATA_PATH)
    return [{"path": rel, "as": rel} for rel in paths]


def resolve_databases(deploy: dict) -> list[dict]:
    databases: list[dict] = []
    impl = resolve_server_implementation(deploy)

    if impl == "synapse":
        databases.append(
            {
                "container": POSTGRES_CONTAINER,
                "name": "synapse",
                "db_name": "synapse",
                "db_user": "synapse",
                "admin_user": "synapse",
                "admin_password_secret": "POSTGRES_PASSWORD",
                "role_password_secret": "POSTGRES_PASSWORD",
                "exclude_table_data": ["e2e_one_time_keys_json"],
            }
        )

    modules = deploy.get("modules") if isinstance(deploy.get("modules"), dict) else {}
    bridges = (
        ("whatsapp_bridge", "mautrix_whatsapp", "WA_DB_PASSWORD"),
        ("slack_bridge", "mautrix_slack", "SL_DB_PASSWORD"),
    )
    for module_key, default_db_name, role_secret in bridges:
        if not _module_enabled(deploy, module_key):
            continue
        cfg = modules.get(module_key)
        db_name = default_db_name
        if isinstance(cfg, dict) and cfg.get("db_name"):
            db_name = str(cfg["db_name"])
        databases.append(
            {
                "container": POSTGRES_CONTAINER,
                "name": db_name,
                "db_name": db_name,
                "db_user": default_db_name,
                "admin_user": "synapse",
                "admin_password_secret": "POSTGRES_PASSWORD",
                "role_password_secret": role_secret,
            }
        )

    return databases


def resolve_plan(project_root: Path) -> dict:
    deploy = _load_yaml(Path(project_root) / "deploy.yaml")
    return {
        "service": SERVICE,
        "archive_prefix": ARCHIVE_PREFIX,
        "timer_name": TIMER_NAME,
        "state_dir": STATE_DIR,
        "secrets_file": SECRETS_FILE,
        "hooks": dict(HOOKS),
        "persistent_paths": resolve_persistent_paths(deploy),
        "docker_volumes": list(CADDY_VOLUMES),
        "databases": resolve_databases(deploy),
        "legacy_persistent_paths": list(LEGACY_PERSISTENT_PATHS),
    }
