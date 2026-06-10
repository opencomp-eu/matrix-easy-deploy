#!/usr/bin/env python3
"""Tuwunel admin helpers.

Account creation uses the Matrix registration API (safe while the server is running).
List/reset use a brief offline admin CLI invocation because docker exec would
contend for the RocksDB lock at /data/LOCK.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


class TuwunelAdminError(RuntimeError):
    pass


class TuwunelAdmin:
    def __init__(
        self,
        container: str = "matrix_tuwunel",
        binary: str = "tuwunel",
        config_path: str = "/tuwunel.toml",
        base_url: str = "",
        registration_token: str = "",
        project_root: Path | None = None,
        image: str = "ghcr.io/matrix-construct/tuwunel:latest",
    ) -> None:
        self.container = container
        self.binary = binary
        self.config_path = config_path
        self.base_url = base_url.rstrip("/")
        self.registration_token = registration_token
        self.project_root = project_root
        self.image = image

    def _running(self) -> bool:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return self.container in result.stdout.splitlines()

    def create_user(self, username: str, password: str, grant_admin: bool = False) -> str:
        if not self.base_url:
            raise TuwunelAdminError("Tuwunel base URL is not configured.")
        if not self.registration_token:
            raise TuwunelAdminError("REGISTRATION_SHARED_SECRET is not configured.")

        payload = {
            "username": username,
            "password": password,
            "auth": {
                "type": "m.login.registration_token",
                "token": self.registration_token,
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/_matrix/client/v3/register",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                if grant_admin:
                    return raw
                return raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            parsed: dict = {}
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                pass
            if e.code == 400 and parsed.get("errcode") == "M_USER_IN_USE":
                return raw
            code = parsed.get("errcode", "")
            msg = parsed.get("error", "")
            raise TuwunelAdminError(
                f"Registration failed (HTTP {e.code}{f', {code}: {msg}' if code or msg else ''})."
            ) from e
        except Exception as e:
            raise TuwunelAdminError(f"Registration failed: {e}") from e

    def _execute_offline(self, command: str) -> str:
        if not self.project_root:
            raise TuwunelAdminError("Project root is required for offline Tuwunel admin commands.")
        data_dir = self.project_root / "modules/core/tuwunel_data"
        config = self.project_root / "modules/core/tuwunel/tuwunel.toml"
        if not config.exists():
            raise TuwunelAdminError(f"Tuwunel config not found: {config}")

        was_running = self._running()
        if was_running:
            subprocess.run(["docker", "stop", self.container], capture_output=True, text=True, check=False)

        try:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{data_dir}:/data",
                    "-v",
                    f"{config}:{self.config_path}:ro",
                    self.image,
                    "--config",
                    self.config_path,
                    "--execute",
                    command,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        finally:
            if was_running:
                subprocess.run(["docker", "start", self.container], capture_output=True, text=True, check=False)

        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 and "already exists" not in output.lower():
            raise TuwunelAdminError(output.strip() or f"offline command failed: {command}")
        return output

    def reset_password(self, username: str, password: str) -> str:
        localpart = username.lstrip("@").split(":", 1)[0]
        return self._execute_offline(f"users reset-password {localpart} {password}")

    def list_users(self) -> list[str]:
        output = self._execute_offline("users list")
        match = re.search(r"```\n(.*?)```", output, re.S)
        if not match:
            return []
        return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def load_tuwunel_admin(env_path: Path, project_root: Path | None = None) -> TuwunelAdmin:
    container = "matrix_tuwunel"
    base_url = ""
    registration_token = ""
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("HOMESERVER_CONTAINER="):
                container = line.split("=", 1)[1].strip() or container
            elif line.startswith("MATRIX_DOMAIN="):
                domain = line.split("=", 1)[1].strip()
                if domain:
                    base_url = f"https://{domain}"
            elif line.startswith("REGISTRATION_SHARED_SECRET="):
                registration_token = line.split("=", 1)[1].strip()
    root = project_root or env_path.parent
    return TuwunelAdmin(
        container=container,
        base_url=base_url,
        registration_token=registration_token,
        project_root=root,
    )
