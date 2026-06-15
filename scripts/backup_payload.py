#!/usr/bin/env python3
"""Backup payload planning and manifest helpers."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

MANIFEST_FORMAT = 2

BASE_PERSISTENT_PATHS = [
    "deploy.yaml",
    ".matrix-easy-deploy/secrets.yaml",
    ".matrix-easy-deploy/modules.yaml",
    "modules/core/synapse_data",
    "modules/hookshot/hookshot",
    "modules/whatsapp-bridge/whatsapp",
    "modules/slack-bridge/slack",
    "modules/draupnir/draupnir",
]

TUWUNEL_DATA_PATH = "modules/core/tuwunel_data"

CADDY_VOLUMES = ["caddy_data", "caddy_caddy_config"]

BRIDGE_DB_USERS = {
    "mautrix_whatsapp": "mautrix_whatsapp",
    "mautrix_slack": "mautrix_slack",
}


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


def resolve_persistent_paths(deploy: dict) -> list[str]:
    paths = list(BASE_PERSISTENT_PATHS)
    if resolve_server_implementation(deploy) == "tuwunel":
        if TUWUNEL_DATA_PATH not in paths:
            paths.append(TUWUNEL_DATA_PATH)
    return paths


def resolve_database_dumps(deploy: dict) -> list[dict]:
    dumps: list[dict] = []
    impl = resolve_server_implementation(deploy)

    if impl == "synapse":
        dumps.append(
            {
                "name": "synapse",
                "path": "database/synapse.dump",
                "db_name": "synapse",
                "db_user": "synapse",
                "exclude_table_data": ["e2e_one_time_keys_json"],
            }
        )

    modules = deploy.get("modules")
    if not isinstance(modules, dict):
        modules = {}

    if _module_enabled(deploy, "whatsapp_bridge"):
        wa = modules.get("whatsapp_bridge")
        db_name = "mautrix_whatsapp"
        if isinstance(wa, dict) and wa.get("db_name"):
            db_name = str(wa["db_name"])
        dumps.append(
            {
                "name": db_name,
                "path": f"database/{db_name}.dump",
                "db_name": db_name,
                "db_user": BRIDGE_DB_USERS["mautrix_whatsapp"],
            }
        )

    if _module_enabled(deploy, "slack_bridge"):
        sl = modules.get("slack_bridge")
        db_name = "mautrix_slack"
        if isinstance(sl, dict) and sl.get("db_name"):
            db_name = str(sl["db_name"])
        dumps.append(
            {
                "name": db_name,
                "path": f"database/{db_name}.dump",
                "db_name": db_name,
                "db_user": BRIDGE_DB_USERS["mautrix_slack"],
            }
        )

    return dumps


def resolve_backup_plan(deploy_yaml: Path) -> dict:
    deploy = _load_yaml(deploy_yaml)
    return {
        "server_implementation": resolve_server_implementation(deploy),
        "persistent_paths": resolve_persistent_paths(deploy),
        "database_dumps": resolve_database_dumps(deploy),
        "volumes": list(CADDY_VOLUMES),
    }


def write_manifest(
    *,
    manifest_path: Path,
    project_root: Path,
    repository_path: str | None,
    encrypted: bool,
) -> None:
    deploy_yaml = project_root / "deploy.yaml"
    plan = resolve_backup_plan(deploy_yaml)

    version = "unknown"
    version_file = project_root / "VERSION"
    if version_file.exists():
        version = version_file.read_text().strip() or version

    manifest = {
        "format": MANIFEST_FORMAT,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": version,
        "server_implementation": plan["server_implementation"],
        "database_dumps": [
            {
                "name": item["name"],
                "path": item["path"],
                **({"db_user": item["db_user"]} if item.get("db_user") else {}),
            }
            for item in plan["database_dumps"]
        ],
        "volumes": plan["volumes"],
        "encrypted": encrypted,
    }
    if repository_path is not None:
        manifest["repository_path"] = repository_path

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def load_manifest(manifest_path: Path) -> dict:
    if not manifest_path.exists():
        raise ValueError(f"Missing manifest: {manifest_path}")
    data = json.loads(manifest_path.read_text())
    if not isinstance(data, dict):
        raise ValueError("manifest.json root must be an object")
    return data


def normalize_database_dumps(manifest: dict) -> list[dict]:
    fmt = manifest.get("format", 1)
    if fmt >= 2:
        dumps = manifest.get("database_dumps")
        if isinstance(dumps, list) and dumps:
            normalized = []
            for item in dumps:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                path = str(item.get("path", "")).strip()
                if not name or not path:
                    continue
                entry = {"name": name, "path": path}
                if item.get("db_user"):
                    entry["db_user"] = str(item["db_user"])
                normalized.append(entry)
            if normalized:
                return normalized

    # format 1 fallback
    legacy = manifest.get("database_dumps")
    if isinstance(legacy, list):
        normalized = []
        for entry in legacy:
            if isinstance(entry, str):
                normalized.append({"name": "synapse", "path": entry, "db_user": "synapse"})
        if normalized:
            return normalized

    return [{"name": "synapse", "path": "database/synapse.dump", "db_user": "synapse"}]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backup payload planning helpers")
    parser.add_argument("--deploy-yaml", type=Path)
    parser.add_argument("--emit-plan-json", action="store_true")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--manifest-path", type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--repository-path", default="")
    parser.add_argument("--encrypted", action="store_true")
    parser.add_argument("--read-manifest", type=Path)
    parser.add_argument("--emit-restore-dumps-json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.emit_plan_json:
        if not args.deploy_yaml:
            print("error: --deploy-yaml is required with --emit-plan-json", file=sys.stderr)
            return 2
        print(json.dumps(resolve_backup_plan(args.deploy_yaml)))
        return 0

    if args.write_manifest:
        if not args.manifest_path or not args.project_root:
            print("error: --manifest-path and --project-root are required with --write-manifest", file=sys.stderr)
            return 2
        write_manifest(
            manifest_path=args.manifest_path,
            project_root=args.project_root,
            repository_path=args.repository_path or None,
            encrypted=args.encrypted,
        )
        return 0

    if args.emit_restore_dumps_json:
        if not args.read_manifest:
            print("error: --read-manifest is required with --emit-restore-dumps-json", file=sys.stderr)
            return 2
        manifest = load_manifest(args.read_manifest)
        print(json.dumps(normalize_database_dumps(manifest)))
        return 0

    print("error: no action requested", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
