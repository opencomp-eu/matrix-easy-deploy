#!/usr/bin/env python3
"""Backup configuration loader/validator for deploy.yaml."""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

import yaml


DEFAULTS = {
    "enabled": False,
    "repository": {
        "type": "local",
        "path": "/var/backups/med-kit",
    },
    "retention": {
        "keep_daily": 7,
        "keep_weekly": 4,
        "keep_monthly": 6,
        "keep_yearly": 0,
    },
}


class BackupConfigError(ValueError):
    pass


def _load_deploy_yaml(path: Path) -> dict:
    if not path.exists():
        raise BackupConfigError(f"Missing deploy.yaml: {path}")

    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise BackupConfigError("deploy.yaml root must be an object")
    return data


def _non_negative_int(name: str, value: object) -> int:
    if not isinstance(value, int) or value < 0:
        raise BackupConfigError(f"{name} must be a non-negative integer")
    return value


def load_backup_settings(path: Path) -> dict:
    data = _load_deploy_yaml(path)
    backup = data.get("backup", {})

    if backup is None:
        backup = {}
    if not isinstance(backup, dict):
        raise BackupConfigError("backup must be an object when provided")

    enabled = backup.get("enabled", DEFAULTS["enabled"])
    if not isinstance(enabled, bool):
        raise BackupConfigError("backup.enabled must be true/false")

    repository = backup.get("repository", {})
    if repository is None:
        repository = {}
    if not isinstance(repository, dict):
        raise BackupConfigError("backup.repository must be an object when provided")

    retention = backup.get("retention", {})
    if retention is None:
        retention = {}
    if not isinstance(retention, dict):
        raise BackupConfigError("backup.retention must be an object when provided")

    repo_type = repository.get("type", DEFAULTS["repository"]["type"])
    repo_path = repository.get("path", DEFAULTS["repository"]["path"])

    if enabled:
        if repo_type != "local":
            raise BackupConfigError("backup.repository.type must be 'local' in phase 1")
        if not isinstance(repo_path, str) or not repo_path.strip():
            raise BackupConfigError("backup.repository.path must be a non-empty string when backup.enabled is true")
        if not repo_path.startswith("/"):
            raise BackupConfigError("backup.repository.path must be an absolute path")

    keep_daily = _non_negative_int(
        "backup.retention.keep_daily", retention.get("keep_daily", DEFAULTS["retention"]["keep_daily"])
    )
    keep_weekly = _non_negative_int(
        "backup.retention.keep_weekly", retention.get("keep_weekly", DEFAULTS["retention"]["keep_weekly"])
    )
    keep_monthly = _non_negative_int(
        "backup.retention.keep_monthly", retention.get("keep_monthly", DEFAULTS["retention"]["keep_monthly"])
    )
    keep_yearly = _non_negative_int(
        "backup.retention.keep_yearly", retention.get("keep_yearly", DEFAULTS["retention"]["keep_yearly"])
    )

    return {
        "enabled": enabled,
        "repository": {
            "type": str(repo_type),
            "path": str(repo_path),
        },
        "retention": {
            "keep_daily": keep_daily,
            "keep_weekly": keep_weekly,
            "keep_monthly": keep_monthly,
            "keep_yearly": keep_yearly,
        },
    }


def _emit_shell(settings: dict) -> str:
    retention = settings["retention"]
    repository = settings["repository"]
    values = {
        "BACKUP_ENABLED": "true" if settings["enabled"] else "false",
        "BACKUP_REPOSITORY_TYPE": repository["type"],
        "BACKUP_REPOSITORY_PATH": repository["path"],
        "BACKUP_KEEP_DAILY": str(retention["keep_daily"]),
        "BACKUP_KEEP_WEEKLY": str(retention["keep_weekly"]),
        "BACKUP_KEEP_MONTHLY": str(retention["keep_monthly"]),
        "BACKUP_KEEP_YEARLY": str(retention["keep_yearly"]),
    }

    return "\n".join(f"{k}={shlex.quote(v)}" for k, v in values.items())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read/validate backup settings from deploy.yaml")
    parser.add_argument("--deploy-yaml", required=True)
    parser.add_argument("--emit-shell", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_backup_settings(Path(args.deploy_yaml))
    if args.emit_shell:
        print(_emit_shell(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
