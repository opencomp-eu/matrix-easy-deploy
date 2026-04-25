#!/usr/bin/env python3
"""Systemd timer reconciliation for automatic backups."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


SERVICE_NAME = "matrix-easy-deploy-backup.service"
TIMER_NAME = "matrix-easy-deploy-backup.timer"


def systemd_unit_dir() -> Path:
    return Path(os.environ.get("MED_SYSTEMD_UNIT_DIR", "/etc/systemd/system"))


def schedule_settings(config: dict) -> dict:
    backup = config.get("backup", {}) if isinstance(config.get("backup", {}), dict) else {}
    schedule = backup.get("schedule", {}) if isinstance(backup.get("schedule", {}), dict) else {}
    return {
        "backup_enabled": bool(backup.get("enabled", False)),
        "enabled": bool(schedule.get("enabled", False)),
        "calendar": str(schedule.get("calendar", "*-*-* 03:00:00")),
        "persistent": bool(schedule.get("persistent", True)),
    }


def render_service(project_root: Path) -> str:
    backup_script = project_root / "backup.sh"
    return "\n".join(
        [
            "[Unit]",
            "Description=matrix-easy-deploy automatic backup",
            "Wants=network-online.target docker.service",
            "After=network-online.target docker.service",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={project_root}",
            f"ExecStart=/usr/bin/env bash {backup_script}",
            "",
        ]
    )


def render_timer(calendar: str, persistent: bool) -> str:
    persistent_value = "true" if persistent else "false"
    return "\n".join(
        [
            "[Unit]",
            "Description=Run matrix-easy-deploy backups automatically",
            "",
            "[Timer]",
            f"OnCalendar={calendar}",
            f"Persistent={persistent_value}",
            f"Unit={SERVICE_NAME}",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )


def _write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text() == content:
        return False
    path.write_text(content)
    return True


def systemd_available(run_command=subprocess.run) -> bool:
    if not Path("/run/systemd/system").exists():
        return False
    result = run_command(["systemctl", "--version"], capture_output=True, text=True, check=False)
    return result.returncode == 0


def reconcile(project_root: Path, config: dict, run_command=subprocess.run, unit_dir: Path | None = None) -> str:
    settings = schedule_settings(config)
    unit_dir = unit_dir or systemd_unit_dir()
    service_path = unit_dir / SERVICE_NAME
    timer_path = unit_dir / TIMER_NAME

    if settings["enabled"] and not settings["backup_enabled"]:
        raise RuntimeError("backup.schedule.enabled requires backup.enabled=true")

    if settings["enabled"]:
        if not systemd_available(run_command=run_command):
            raise RuntimeError("Automatic backup scheduling requires systemd on this host")

        try:
            unit_dir.mkdir(parents=True, exist_ok=True)
            service_changed = _write_if_changed(service_path, render_service(project_root))
            timer_changed = _write_if_changed(timer_path, render_timer(settings["calendar"], settings["persistent"]))

            run_command(["systemctl", "daemon-reload"], check=True)
            run_command(["systemctl", "enable", "--now", TIMER_NAME], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Failed to install automatic backup timer: {exc}") from exc

        if service_changed or timer_changed:
            return "Automatic backup timer installed or updated."
        return "Automatic backup timer already up to date."

    if service_path.exists() or timer_path.exists():
        if not systemd_available(run_command=run_command):
            raise RuntimeError("Cannot remove automatic backup timer because systemd is unavailable")

        try:
            run_command(["systemctl", "disable", "--now", TIMER_NAME], check=False)
            if timer_path.exists():
                timer_path.unlink()
            if service_path.exists():
                service_path.unlink()
            run_command(["systemctl", "daemon-reload"], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Failed to remove automatic backup timer: {exc}") from exc
        return "Automatic backup timer removed."

    return "Automatic backup timer not configured."