#!/usr/bin/env python3
"""Tuwunel admin commands via docker exec."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


class TuwunelAdminError(RuntimeError):
    pass


class TuwunelAdmin:
    def __init__(self, container: str = "matrix_tuwunel", binary: str = "tuwunel") -> None:
        self.container = container
        self.binary = binary

    def _running(self) -> bool:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return self.container in result.stdout.splitlines()

    def execute(self, command: str) -> str:
        if not self._running():
            raise TuwunelAdminError(
                f"Tuwunel ({self.container}) is not running. Start the core stack first."
            )
        result = subprocess.run(
            ["docker", "exec", self.container, self.binary, "--execute", command],
            capture_output=True,
            text=True,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            if "already exists" in output.lower():
                return output
            raise TuwunelAdminError(output.strip() or f"command failed: {command}")
        return output

    def create_user(self, username: str, password: str, grant_admin: bool = False) -> str:
        output = self.execute(f"users create_user {username} {password}")
        if grant_admin:
            for cmd in (
                f"users make-user-admin {username}",
                f"users make_user_admin {username}",
            ):
                try:
                    self.execute(cmd)
                    break
                except TuwunelAdminError:
                    continue
        return output

    def reset_password(self, username: str, password: str) -> str:
        localpart = username.lstrip("@").split(":", 1)[0]
        return self.execute(f"users reset-password {localpart} {password}")

    def list_users(self) -> list[str]:
        output = self.execute("users list")
        match = re.search(r"```\n(.*?)```", output, re.S)
        if not match:
            return []
        return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def load_tuwunel_admin(env_path: Path) -> TuwunelAdmin:
    container = "matrix_tuwunel"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("HOMESERVER_CONTAINER="):
                container = line.split("=", 1)[1].strip() or container
                break
    return TuwunelAdmin(container=container)
