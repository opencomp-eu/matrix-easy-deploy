#!/usr/bin/env python3
"""med-admin: Matrix/Synapse admin helper."""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
import string
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _ensure_repo_on_path() -> Path:
    repo_dir = Path(__file__).resolve().parent.parent
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))
    return repo_dir

def _load_tuwunel_admin_module():
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    import tuwunel_admin

    return tuwunel_admin


RED = "\033[0;31m"
YELLOW = "\033[1;33m"
GREEN = "\033[0;32m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
RESET = "\033[0m"


def info(msg: str) -> None:
    print(f"{CYAN}  -->{RESET} {msg}")


def success(msg: str) -> None:
    print(f"{GREEN}  [ok]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}  [!]{RESET}  {msg}")


def die(msg: str, exit_code: int = 1) -> None:
    print(f"{RED}  [ERR]{RESET} {msg}", file=sys.stderr)
    raise SystemExit(exit_code)


def is_tty_stdin() -> bool:
    return sys.stdin.isatty()


def ask(prompt: str, default: str | None = None) -> str:
    if default:
        raw = input(f"{BOLD}  {prompt}{RESET} {CYAN}[{default}]{RESET}: ")
        return raw or default
    return input(f"{BOLD}  {prompt}{RESET}: ")


def ask_secret(prompt: str) -> str:
    return getpass.getpass(f"{BOLD}  {prompt}{RESET}: ")


def ask_yn(prompt: str, default: str = "n") -> bool:
    hint = "Y/n" if default.lower() == "y" else "y/N"
    raw = input(f"{BOLD}  {prompt}{RESET} {CYAN}[{hint}]{RESET}: ").strip().lower()
    if not raw:
        raw = default.lower()
    return raw in {"y", "yes"}


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def upsert_env_value(path: Path, key: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        die(f"Value for {key} cannot contain newlines.")

    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    new_line = f"{key}={value}\n"
    for idx, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[idx] = new_line
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(new_line)

    path.write_text("".join(lines), encoding="utf-8")


def to_user_id(raw: str, server_name: str) -> str:
    if raw.startswith("@") and ":" in raw:
        return raw
    local = raw[1:] if raw.startswith("@") else raw
    return f"@{local}:{server_name}"


def ensure_min_password_len(password: str, length: int = 12) -> None:
    if len(password) < length:
        die(f"Password must be at least {length} characters.")


def generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@dataclass
class Context:
    repo_dir: Path
    script_dir: Path
    env_path: Path
    base_url: str = ""
    server_name: str = ""
    shared_secret: str = ""
    auth_username: str = ""
    auth_password: str = ""
    access_token: str = ""
    med_admin_username: str = ""
    med_admin_password: str = ""
    server_implementation: str = "synapse"

    def read_deploy_env(self) -> None:
        env = load_env_file(self.env_path)
        self.server_implementation = env.get("SERVER_IMPLEMENTATION", "synapse").strip().lower() or "synapse"
        if not self.server_name:
            self.server_name = env.get("SERVER_NAME", "")
        if not self.base_url and env.get("MATRIX_DOMAIN"):
            self.base_url = f"https://{env['MATRIX_DOMAIN']}"
        if not self.shared_secret:
            self.shared_secret = env.get("REGISTRATION_SHARED_SECRET", "")
        if not self.med_admin_username:
            self.med_admin_username = env.get("MED_ADMIN_USERNAME", "")
        if not self.med_admin_password:
            self.med_admin_password = env.get("MED_ADMIN_PASSWORD", "")
        if not self.auth_username:
            self.auth_username = self.med_admin_username or env.get("ADMIN_USERNAME", "")
        if not self.auth_password:
            self.auth_password = self.med_admin_password

    def ensure_base_url(self) -> None:
        if not self.base_url:
            die(
                "Could not determine Synapse base URL. Pass --base-url or ensure MATRIX_DOMAIN exists in .env."
            )

    def ensure_server_name(self) -> None:
        if not self.server_name:
            die("Could not determine SERVER_NAME. Ensure it exists in .env.")

    def ensure_auth_username(self) -> None:
        if not self.auth_username:
            die(
                "Could not determine an admin username. Pass --admin-username or ensure ADMIN_USERNAME exists in .env."
            )

    def ensure_bootstrap_config(self) -> None:
        if self.server_implementation == "tuwunel":
            return
        if not self.shared_secret:
            die(
                "Could not determine REGISTRATION_SHARED_SECRET. Pass --shared-secret or ensure it exists in .env."
            )

    def is_tuwunel(self) -> bool:
        return self.server_implementation == "tuwunel"

    def require_synapse_admin_api(self, command: str) -> None:
        if self.is_tuwunel():
            die(
                f"Command '{command}' is not available for Tuwunel via Synapse HTTP admin API. "
                "Tuwunel administration uses docker exec admin commands."
            )

    def prompt_for_auth_password(self) -> None:
        if self.auth_password:
            return
        self.ensure_auth_username()
        info("Password login is required to obtain an admin token.")
        self.auth_password = ask_secret(f"Password for {self.auth_username}")
        if not self.auth_password:
            die("Admin password is required unless --access-token is provided.")

    def get_admin_token(self) -> str:
        if self.access_token:
            return self.access_token
        self.ensure_base_url()
        self.ensure_auth_username()
        self.prompt_for_auth_password()
        token = self._login_for_token(self.auth_username, self.auth_password)
        self.access_token = token
        return token

    def _login_for_token(self, username: str, password: str) -> str:
        payload = {
            "type": "m.login.password",
            "user": username,
            "password": password,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/_matrix/client/v3/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            data = {}
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                pass
            code = data.get("errcode", "")
            msg = data.get("error", "")
            die(
                f"Could not obtain an admin access token via password login"
                f"{f' ({code}: {msg})' if code else ''}. "
                "If local password login is disabled, pass --access-token."
            )
        except Exception as e:
            die(f"Could not obtain an admin access token: {e}")

        token = data.get("access_token", "")
        if not token:
            die("Could not obtain an admin access token (missing access_token in response).")
        return token

    def verify_password_login(self, username: str, password: str) -> bool:
        self.ensure_base_url()
        payload = {
            "type": "m.login.password",
            "user": username,
            "password": password,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/_matrix/client/v3/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
        except Exception:
            return False
        return bool(data.get("access_token"))

    def _request_json(
        self, *, method: str, endpoint: str, payload: dict[str, Any] | None, api_prefix: str
    ) -> dict[str, Any]:
        self.ensure_base_url()
        token = self.get_admin_token()
        data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.base_url}/{api_prefix}/{endpoint}",
            data=data_bytes,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            parsed: dict[str, Any] = {}
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                pass
            code = parsed.get("errcode")
            msg = parsed.get("error")
            if code or msg:
                die(f"API request failed (HTTP {e.code}, {code}: {msg}).")
            die(f"API request failed (HTTP {e.code}). Response: {raw}")
        except Exception as e:
            die(f"API request failed: {e}")
        return {}

    def admin_api(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request_json(method=method, endpoint=endpoint, payload=payload, api_prefix="_synapse/admin")

    def client_api(
        self, method: str, endpoint: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._request_json(method=method, endpoint=endpoint, payload=payload, api_prefix="_matrix/client/v3")

    def client_api_status(
        self, method: str, endpoint: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        self.ensure_base_url()
        token = self.get_admin_token()
        data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.base_url}/_matrix/client/v3/{endpoint}",
            data=data_bytes,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            parsed: dict[str, Any] = {}
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                pass
            return e.code, parsed
        except Exception as e:
            die(f"API request failed: {e}")
        return 0, {}


def resolve_bootstrap_username(ctx: Context, username: str = "") -> str:
    if username.strip():
        return username.strip()
    env = load_env_file(ctx.env_path)
    return env.get("ADMIN_USERNAME", "").strip() or "admin"


def warn_bootstrap_username_mismatch(ctx: Context) -> None:
    env = load_env_file(ctx.env_path)
    med_user = env.get("MED_ADMIN_USERNAME", "").strip()
    admin_user = env.get("ADMIN_USERNAME", "").strip()
    if not med_user or not admin_user or med_user == admin_user:
        return
    warn(
        f"MED_ADMIN_USERNAME ({med_user}) differs from ADMIN_USERNAME ({admin_user}). "
        f"Run 'bash scripts/med-admin.sh bootstrap --username {admin_user} --password <password>' "
        "to realign."
    )


def is_bootstrapped(ctx: Context) -> bool:
    env = load_env_file(ctx.env_path)
    username = env.get("MED_ADMIN_USERNAME", "").strip()
    password = env.get("MED_ADMIN_PASSWORD", "").strip()
    if not (username and password):
        return False
    admin_username = env.get("ADMIN_USERNAME", "").strip()
    if admin_username and username != admin_username:
        return False
    return True


def _reset_existing_user_password(ctx: Context, username: str, password: str) -> bool:
    ctx.ensure_server_name()
    if ctx.is_tuwunel():
        tuwunel_admin = _load_tuwunel_admin_module()
        admin = tuwunel_admin.load_tuwunel_admin(ctx.env_path, project_root=ctx.repo_dir)
        try:
            admin.reset_password(username, password)
        except tuwunel_admin.TuwunelAdminError:
            return False
        return ctx.verify_password_login(username, password)

    temp_localpart = f"med-bootstrap-{secrets.token_hex(4)}"
    temp_password = generate_password(24)
    create_cmd = [
        "bash",
        str(ctx.script_dir / "create-account.sh"),
        "--username",
        temp_localpart,
        "--password",
        temp_password,
        "--admin",
        "--yes",
    ]
    if ctx.base_url:
        create_cmd += ["--base-url", ctx.base_url]
    if ctx.shared_secret:
        create_cmd += ["--shared-secret", ctx.shared_secret]

    result = subprocess.run(create_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False

    saved_token = ctx.access_token
    saved_username = ctx.auth_username
    saved_password = ctx.auth_password
    try:
        ctx.access_token = ""
        ctx.auth_username = temp_localpart
        ctx.auth_password = temp_password
        ctx._login_for_token(temp_localpart, temp_password)
        target_id = to_user_id(username, ctx.server_name)
        temp_id = to_user_id(temp_localpart, ctx.server_name)
        ctx.admin_api(
            "POST",
            f"v1/reset_password/{urllib.parse.quote(target_id, safe='')}",
            {"new_password": password, "logout_devices": True},
        )
        ctx.admin_api(
            "POST",
            f"v1/deactivate/{urllib.parse.quote(temp_id, safe='')}",
            {"erase": True},
        )
    except SystemExit:
        return False
    finally:
        ctx.access_token = saved_token
        ctx.auth_username = saved_username
        ctx.auth_password = saved_password

    return ctx.verify_password_login(username, password)


def run_bootstrap(ctx: Context, *, username: str | None = None, password: str | None = None) -> None:
    ctx.read_deploy_env()
    ctx.ensure_bootstrap_config()
    username = resolve_bootstrap_username(ctx, username or "")
    password = password or generate_password()

    if ctx.is_tuwunel():
        tuwunel_admin = _load_tuwunel_admin_module()
        admin = tuwunel_admin.load_tuwunel_admin(ctx.env_path, project_root=ctx.repo_dir)
        info(f"Creating admin account '{username}' via Tuwunel registration API…")
        try:
            admin.create_user(username, password, grant_admin=True)
        except tuwunel_admin.TuwunelAdminError as exc:
            die(str(exc))
    else:
        create_cmd = [
            "bash",
            str(ctx.script_dir / "create-account.sh"),
            "--username",
            username,
            "--password",
            password,
            "--admin",
            "--yes",
        ]
        if ctx.base_url:
            create_cmd += ["--base-url", ctx.base_url]
        if ctx.shared_secret:
            create_cmd += ["--shared-secret", ctx.shared_secret]

        info(f"Creating admin account '{username}' via shared-secret registration…")
        result = subprocess.run(create_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip() or "create-account failed."
            die(msg)

    ctx.ensure_base_url()
    if not ctx.verify_password_login(username, password):
        info(
            f"Password login failed for '{username}' "
            "(account may already exist); attempting password reset…"
        )
        if not _reset_existing_user_password(ctx, username, password):
            die(
                f"Account '{username}' is not usable with the generated password "
                "(it may already exist with a different password, or local password login may be disabled). "
                f"Run 'bash scripts/med-admin.sh bootstrap --username {username} --password <known-password>' "
                "or pass --access-token."
            )

    upsert_env_value(ctx.env_path, "MED_ADMIN_USERNAME", username)
    upsert_env_value(ctx.env_path, "MED_ADMIN_PASSWORD", password)
    ctx.med_admin_username = username
    ctx.med_admin_password = password
    ctx.auth_username = username
    ctx.auth_password = password
    success(f"Admin account '{username}' ready for admin operations.")
    success("Credentials stored in .env for automatic use by admin commands.")


def ensure_bootstrapped(ctx: Context) -> None:
    if is_bootstrapped(ctx):
        return
    warn_bootstrap_username_mismatch(ctx)
    default_username = resolve_bootstrap_username(ctx)
    info(
        f"med-admin credentials not stored yet; ensuring server admin '{default_username}' "
        "is ready for automation…"
    )
    run_bootstrap(ctx)


def cmd_bootstrap(ctx: Context, args: argparse.Namespace) -> None:
    ctx.read_deploy_env()
    ctx.ensure_bootstrap_config()

    if args.generate_password and args.password:
        die("Use either --password or --generate-password, not both.")

    password = args.password or (generate_password() if args.generate_password else None)
    username = resolve_bootstrap_username(ctx, args.username)
    run_bootstrap(ctx, username=username, password=password)


def _parse_int(name: str, raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        die(f"{name} must be an integer.")


def _print_table_rows(headers: list[str], rows: list[list[Any]]) -> None:
    print("\t".join(headers))
    for row in rows:
        print("\t".join(str(v) for v in row))


def cmd_list_accounts(ctx: Context, args: argparse.Namespace, admins_only: bool) -> None:
    ctx.read_deploy_env()

    if ctx.is_tuwunel():
        if admins_only:
            warn("Tuwunel does not expose Synapse-style admin flags; listing all local users.")
        tuwunel_admin = _load_tuwunel_admin_module()
        admin = tuwunel_admin.load_tuwunel_admin(ctx.env_path, project_root=ctx.repo_dir)
        users = []
        try:
            users = admin.list_users()
        except tuwunel_admin.TuwunelAdminError as exc:
            die(str(exc))
        if args.filter:
            needle = args.filter.lower()
            users = [u for u in users if needle in u.lower()]
        rows = [[u, "unknown", False, False, ""] for u in users]
        _print_table_rows(["USER_ID", "ADMIN", "DEACTIVATED", "LOCKED", "DISPLAYNAME"], rows)
        return

    ctx.require_synapse_admin_api("list-accounts")
    ctx.ensure_base_url()

    limit = _parse_int("--limit", args.limit)
    query = f"v2/users?limit={limit}&guests=false"
    if args.from_token:
        _ = _parse_int("--from", args.from_token)
        query += f"&from={urllib.parse.quote(args.from_token, safe='')}"
    if args.filter:
        query += f"&name={urllib.parse.quote(args.filter, safe='')}"
    if admins_only:
        query += "&admins=true"

    data = ctx.admin_api("GET", query)
    users = data.get("users", [])
    rows = [
        [
            user.get("name", ""),
            user.get("admin", False),
            user.get("deactivated", False),
            user.get("locked", False),
            user.get("displayname") or "",
        ]
        for user in users
    ]
    _print_table_rows(["USER_ID", "ADMIN", "DEACTIVATED", "LOCKED", "DISPLAYNAME"], rows)
    if "next_token" in data:
        print(f"NEXT_TOKEN\t{data.get('next_token')}")
    if "total" in data:
        print(f"TOTAL\t{data.get('total')}")


def cmd_get_account(ctx: Context, args: argparse.Namespace) -> None:
    ctx.read_deploy_env()
    ctx.ensure_server_name()
    user_id = to_user_id(args.target, ctx.server_name)

    if ctx.is_tuwunel():
        tuwunel_admin = _load_tuwunel_admin_module()
        admin = tuwunel_admin.load_tuwunel_admin(ctx.env_path, project_root=ctx.repo_dir)
        users = []
        try:
            users = admin.list_users()
        except tuwunel_admin.TuwunelAdminError as exc:
            die(str(exc))
        if user_id not in users:
            die(f"User not found: {user_id}")
        print(f"User ID:      {user_id}")
        print("Admin:        unknown")
        print("Deactivated:  False")
        print("Locked:       False")
        return

    ctx.require_synapse_admin_api("get-account")
    ctx.ensure_base_url()
    encoded = urllib.parse.quote(user_id, safe="")
    data = ctx.admin_api("GET", f"v2/users/{encoded}")
    print(f"User ID:      {data.get('name', '')}")
    print(f"Admin:        {data.get('admin', False)}")
    print(f"Deactivated:  {data.get('deactivated', False)}")
    print(f"Locked:       {data.get('locked', False)}")
    print(f"Guest:        {data.get('is_guest', False)}")
    print(f"Display name: {data.get('displayname') or ''}")
    print(f"Avatar URL:   {data.get('avatar_url') or ''}")
    print(f"Creation ts:  {data.get('creation_ts', '')}")
    print(f"Last seen ts: {data.get('last_seen_ts', '')}")


def _resolve_reset_password(args: argparse.Namespace) -> str:
    if args.password:
        ensure_min_password_len(args.password)
        return args.password
    while True:
        a = ask_secret("New password")
        if len(a) < 12:
            warn("Password must be at least 12 characters.")
            continue
        b = ask_secret("Confirm new password")
        if a != b:
            warn("Passwords do not match. Try again.")
            continue
        return a


def cmd_reset_password(ctx: Context, args: argparse.Namespace) -> None:
    ctx.read_deploy_env()
    ctx.ensure_server_name()
    user_id = to_user_id(args.target, ctx.server_name)
    new_pw = _resolve_reset_password(args)

    if ctx.is_tuwunel():
        if not args.yes:
            print()
            print(f"{BOLD}Reset password{RESET}")
            print(f"  User ID:  {CYAN}{user_id}{RESET}")
            if not ask_yn("Reset this password now?", "n"):
                die("Aborted.")
        tuwunel_admin = _load_tuwunel_admin_module()
        admin = tuwunel_admin.load_tuwunel_admin(ctx.env_path, project_root=ctx.repo_dir)
        try:
            admin.reset_password(user_id, new_pw)
        except tuwunel_admin.TuwunelAdminError as exc:
            die(str(exc))
        success(f"Password reset for '{user_id}'.")
        return

    ctx.require_synapse_admin_api("reset-password")
    ctx.ensure_base_url()
    encoded = urllib.parse.quote(user_id, safe="")
    if not args.yes:
        print()
        print(f"{BOLD}Reset password{RESET}")
        print(f"  User ID:  {CYAN}{user_id}{RESET}")
        if not ask_yn("Reset this password now?", "n"):
            die("Aborted.")

    ctx.admin_api(
        "POST",
        f"v1/reset_password/{encoded}",
        {"new_password": new_pw, "logout_devices": True},
    )
    success(f"Password reset for '{user_id}'.")


def _parse_invites(raw_invites: list[str], server_name: str) -> list[str]:
    users: list[str] = []
    for entry in raw_invites:
        for part in entry.split(","):
            cleaned = part.strip()
            if cleaned:
                users.append(to_user_id(cleaned, server_name))
    return users


def _encode_room_alias(alias: str) -> str:
    return urllib.parse.quote(alias, safe="")


def _resolve_room_id(ctx: Context, alias: str) -> str | None:
    status, data = ctx.client_api_status("GET", f"directory/room/{_encode_room_alias(alias)}")
    if status == 404:
        return None
    if status >= 400:
        code = data.get("errcode", "")
        msg = data.get("error", "")
        die(f"Could not resolve alias {alias} (HTTP {status}, {code}: {msg}).")
    room_id = data.get("room_id")
    return room_id if isinstance(room_id, str) and room_id else None


def _room_has_messages(ctx: Context, room_id: str) -> bool:
    encoded_room = urllib.parse.quote(room_id, safe="")
    status, data = ctx.client_api_status("GET", f"rooms/{encoded_room}/messages?dir=b&limit=20")
    if status >= 400:
        return False
    for event in data.get("chunk", []):
        if event.get("type") == "m.room.message":
            return True
    return False


def _send_text_message(ctx: Context, room_id: str, body: str) -> str:
    encoded_room = urllib.parse.quote(room_id, safe="")
    txn_id = secrets.token_hex(16)
    response = ctx.client_api(
        "PUT",
        f"rooms/{encoded_room}/send/m.room.message/{txn_id}",
        {"msgtype": "m.text", "body": body},
    )
    return response.get("event_id", "")


def _set_room_name(ctx: Context, room_id: str, name: str) -> None:
    encoded_room = urllib.parse.quote(room_id, safe="")
    ctx.client_api("PUT", f"rooms/{encoded_room}/state/m.room.name", {"name": name})


def _set_room_topic(ctx: Context, room_id: str, topic: str) -> None:
    encoded_room = urllib.parse.quote(room_id, safe="")
    ctx.client_api("PUT", f"rooms/{encoded_room}/state/m.room.topic", {"topic": topic})


def _ensure_in_room(ctx: Context, room_id_or_alias: str) -> None:
    encoded = urllib.parse.quote(room_id_or_alias, safe="")
    status, data = ctx.client_api_status("POST", f"join/{encoded}", {})
    if status < 400:
        return
    errcode = data.get("errcode", "")
    if errcode in {"M_FORBIDDEN", "M_UNKNOWN"} and "already" in str(data.get("error", "")).lower():
        return
    if status == 403 and errcode == "M_FORBIDDEN":
        # Already joined or cannot join; handover/name updates may still work.
        return


def _apply_handover(ctx: Context, room_id: str, handover: list[str], server_name: str) -> None:
    if not handover:
        return
    user_ids = [to_user_id(entry, server_name) for entry in handover]
    encoded_room = urllib.parse.quote(room_id, safe="")

    for user_id in user_ids:
        ctx.client_api_status("POST", f"rooms/{encoded_room}/invite", {"user_id": user_id})

    status, power_levels = ctx.client_api_status("GET", f"rooms/{encoded_room}/state/m.room.power_levels")
    if status >= 400:
        die(f"Could not read power levels for {room_id} (HTTP {status}).")
    users = power_levels.get("users")
    if not isinstance(users, dict):
        users = {}
    for user_id in user_ids:
        users[user_id] = 100
    power_levels["users"] = users
    ctx.client_api("PUT", f"rooms/{encoded_room}/state/m.room.power_levels", power_levels)


def _create_room_from_spec(ctx: Context, spec: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "visibility": "public",
        "preset": "public_chat",
        "creation_content": {"m.federate": bool(spec.get("federated", False))},
    }
    alias_localpart = spec["alias"].lstrip("#").split(":", 1)[0]
    payload["room_alias_name"] = alias_localpart
    if spec.get("name"):
        payload["name"] = spec["name"]
    if spec.get("topic"):
        payload["topic"] = spec["topic"]
    handover = spec.get("handover") or []
    if handover:
        ctx.ensure_server_name()
        ctx.ensure_auth_username()
        creator_id = to_user_id(ctx.auth_username, ctx.server_name)
        invite_ids = [to_user_id(entry, ctx.server_name) for entry in handover]
        payload["invite"] = invite_ids
        power_users = {creator_id: 100}
        for user_id in invite_ids:
            power_users[user_id] = 100
        payload["power_level_content_override"] = {"users": power_users}
    response = ctx.client_api("POST", "createRoom", payload)
    room_id = response.get("room_id", "")
    if not room_id:
        die(f"createRoom did not return a room_id for {spec['alias']}.")
    return room_id


def _load_auto_join_config(deploy_yaml: Path) -> dict[str, Any]:
    if not deploy_yaml.exists():
        die(f"Config file not found: {deploy_yaml}")
    data = yaml.safe_load(deploy_yaml.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        die(f"{deploy_yaml} must contain a YAML object at the root.")
    features = data.get("features", {})
    if not isinstance(features, dict):
        die(f"{deploy_yaml} is missing a features section.")
    auto_join = features.get("auto_join", {})
    if not isinstance(auto_join, dict):
        die(f"{deploy_yaml} is missing features.auto_join.")
    return auto_join


def _provision_auto_join_room(
    ctx: Context,
    spec: dict[str, Any],
    *,
    force_message: bool,
) -> None:
    alias = spec["alias"]
    room_id = _resolve_room_id(ctx, alias)
    if room_id:
        info(f"Room already exists for {alias} ({room_id}).")
        _ensure_in_room(ctx, room_id)
        if spec.get("name"):
            _set_room_name(ctx, room_id, spec["name"])
        if spec.get("topic"):
            _set_room_topic(ctx, room_id, spec["topic"])
        ctx.ensure_server_name()
        _apply_handover(ctx, room_id, spec.get("handover") or [], ctx.server_name)
    else:
        info(f"Creating room {alias}…")
        room_id = _create_room_from_spec(ctx, spec)
        success(f"Created {alias} ({room_id}).")

    if spec.get("message"):
        if force_message or not _room_has_messages(ctx, room_id):
            _send_text_message(ctx, room_id, spec["message"])
            success(f"Posted welcome message to {alias}.")
        else:
            info(f"Skipping message for {alias} (room already has messages).")


def cmd_setup_auto_join_rooms(ctx: Context, args: argparse.Namespace) -> None:
    _ensure_repo_on_path()
    from scripts import apply

    ctx.read_deploy_env()
    ctx.ensure_base_url()
    ctx.ensure_server_name()

    deploy_yaml = Path(args.deploy_yaml) if args.deploy_yaml else ctx.repo_dir / "deploy.yaml"
    auto_join = _load_auto_join_config(deploy_yaml)
    apply.validate_auto_join_config(auto_join)

    rooms = auto_join.get("rooms") or []
    if not rooms:
        info("No auto-join rooms configured; nothing to do.")
        return

    specs = [apply.parse_auto_join_room_entry(entry, ctx.server_name) for entry in rooms]

    if not args.yes:
        print()
        print(f"{BOLD}Setup auto-join rooms{RESET}")
        for spec in specs:
            print(f"  {CYAN}{spec['alias']}{RESET}")
            if spec.get("name"):
                print(f"    name:      {spec['name']}")
            if spec.get("topic"):
                print(f"    topic:     {spec['topic']}")
            if spec.get("handover"):
                print(f"    handover:  {', '.join(spec['handover'])}")
            if spec.get("message"):
                preview = spec["message"].replace("\n", " ")[:60]
                suffix = "…" if len(spec["message"]) > 60 else ""
                print(f"    message:   {preview}{suffix}")
            federated = spec.get("federated", False)
            print(f"    federated: {federated}")
        if not ask_yn("Provision these rooms now?", "y"):
            die("Aborted.")

    for spec in specs:
        _provision_auto_join_room(
            ctx,
            spec,
            force_message=args.force_message,
        )

    success("Auto-join room setup complete.")


def cmd_create_room(ctx: Context, args: argparse.Namespace) -> None:
    ctx.read_deploy_env()
    ctx.ensure_base_url()

    name = args.name or ""
    visibility = args.visibility
    if not visibility and is_tty_stdin():
        visibility = "public" if ask_yn("Public room?", "n") else "private"
    visibility = visibility or "private"

    alias_localpart = ""
    canonical_alias = ""
    if args.alias:
        alias_localpart = args.alias.lstrip("#").split(":", 1)[0]
        if alias_localpart:
            ctx.ensure_server_name()
            canonical_alias = f"#{alias_localpart}:{ctx.server_name}"

    invite_ids: list[str] = []
    if args.invite:
        ctx.ensure_server_name()
        invite_ids = _parse_invites(args.invite, ctx.server_name)

    if not args.yes:
        print()
        print(f"{BOLD}Create room{RESET}")
        print(f"  Name:        {CYAN}{name or '<none>'}{RESET}")
        print(f"  Visibility:  {CYAN}{visibility}{RESET}")
        print(f"  Alias:       {CYAN}{canonical_alias or '<none>'}{RESET}")
        print(f"  Invites:     {CYAN}{', '.join(invite_ids) if invite_ids else '<none>'}{RESET}")
        print(f"  Direct chat: {CYAN}{args.direct}{RESET}")
        if not ask_yn("Create this room now?", "y"):
            die("Aborted.")

    payload: dict[str, Any] = {
        "is_direct": args.direct,
        "visibility": visibility,
        "preset": "public_chat" if visibility == "public" else "private_chat",
    }
    if name:
        payload["name"] = name
    if args.topic:
        payload["topic"] = args.topic
    if alias_localpart:
        payload["room_alias_name"] = alias_localpart
    if invite_ids:
        payload["invite"] = invite_ids

    response = ctx.client_api("POST", "createRoom", payload)
    success("Room created.")
    if response.get("room_id"):
        print(f"  room_id: {response['room_id']}")
    if response.get("room_alias"):
        print(f"  alias:   {response['room_alias']}")
    elif canonical_alias:
        print(f"  alias:   {canonical_alias}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bash scripts/med-admin.sh",
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "Usage:\n"
            "  bash scripts/med-admin.sh bootstrap [--username ADMIN_USERNAME] [--password 'long-secret'] [--yes]\n"
            "  bash scripts/med-admin.sh list-accounts [--filter alice] [--limit 100] [--from 0]\n"
            "  bash scripts/med-admin.sh list-admins [--filter alice] [--limit 100] [--from 0]\n"
            "  bash scripts/med-admin.sh get-account USERNAME_OR_MXID\n"
            "  bash scripts/med-admin.sh reset-password USERNAME_OR_MXID [--password 'new-long-secret'] [--yes]\n"
            "  bash scripts/med-admin.sh create-room [--name 'Care Team'] [--alias care-team] [--topic 'Clinical coordination'] [--public|--private] [--invite USER_OR_MXID]... [--direct] [--yes]\n"
            "  bash scripts/med-admin.sh setup-auto-join-rooms [--deploy-yaml deploy.yaml] [--force-message] [--yes]"
        ),
    )
    parser.add_argument("--base-url", default="", help="Override Synapse base URL.")
    parser.add_argument("--access-token", default="", help="Use an existing Synapse admin access token.")
    parser.add_argument("--admin-username", default="", help="Admin username to obtain a token.")
    parser.add_argument("--admin-password", default="", help="Admin password to obtain a token.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompts where applicable.")

    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("bootstrap")
    b.add_argument(
        "--username",
        default="",
        help="Admin username (defaults to ADMIN_USERNAME from .env).",
    )
    b.add_argument("--password", default="")
    b.add_argument("--generate-password", action="store_true")
    b.add_argument("--shared-secret", default="")

    lacc = sub.add_parser("list-accounts")
    lacc.add_argument("--filter", default="")
    lacc.add_argument("--limit", default="100")
    lacc.add_argument("--from", dest="from_token", default="")

    la = sub.add_parser("list-admins")
    la.add_argument("--filter", default="")
    la.add_argument("--limit", default="100")
    la.add_argument("--from", dest="from_token", default="")

    g = sub.add_parser("get-account")
    g.add_argument("target")

    r = sub.add_parser("reset-password")
    r.add_argument("target")
    r.add_argument("--password", default="")

    c = sub.add_parser("create-room")
    c.add_argument("--name", default="")
    c.add_argument("--alias", default="")
    c.add_argument("--topic", default="")
    vis = c.add_mutually_exclusive_group()
    vis.add_argument("--public", action="store_true")
    vis.add_argument("--private", action="store_true")
    c.add_argument("--invite", action="append", default=[])
    c.add_argument("--direct", action="store_true")

    s = sub.add_parser("setup-auto-join-rooms")
    s.add_argument("--deploy-yaml", default="")
    s.add_argument(
        "--force-message",
        action="store_true",
        help="Post the configured welcome message even when the room already has messages.",
    )

    return parser


def _reorder_argv_for_argparse(argv: list[str]) -> list[str]:
    """Move global flags before the subcommand (legacy med-admin.sh accepted them anywhere)."""
    value_flags = {
        "--base-url",
        "--access-token",
        "--admin-username",
        "--admin-password",
    }
    bool_flags = {"--yes", "-h", "--help"}
    commands = {
        "bootstrap",
        "list-accounts",
        "list-admins",
        "get-account",
        "reset-password",
        "create-room",
        "setup-auto-join-rooms",
    }

    global_args: list[str] = []
    command_args: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in bool_flags:
            global_args.append(arg)
            i += 1
            continue
        if arg in value_flags:
            if i + 1 >= len(argv):
                die(f"Missing value for {arg}")
            global_args.extend([arg, argv[i + 1]])
            i += 2
            continue
        command_args.append(arg)
        i += 1

    if not command_args:
        return global_args

    command = command_args[0]
    if command not in commands:
        return argv

    return global_args + command_args


def should_auto_bootstrap(args: argparse.Namespace) -> bool:
    if args.command == "bootstrap":
        return False
    if args.access_token:
        return False
    if args.admin_username or args.admin_password:
        return False
    return True


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(_reorder_argv_for_argparse(argv))

    script_dir = Path(__file__).resolve().parent
    repo_dir = script_dir.parent
    ctx = Context(repo_dir=repo_dir, script_dir=script_dir, env_path=repo_dir / ".env")
    ctx.base_url = args.base_url or ""
    ctx.auth_username = args.admin_username or ""
    ctx.auth_password = args.admin_password or ""
    ctx.access_token = args.access_token or ""
    ctx.read_deploy_env()

    # align per-command --yes support from legacy script
    if not hasattr(args, "yes"):
        setattr(args, "yes", False)

    if should_auto_bootstrap(args):
        ensure_bootstrapped(ctx)
        ctx.read_deploy_env()

    if args.command == "bootstrap":
        if args.shared_secret:
            ctx.shared_secret = args.shared_secret
        cmd_bootstrap(ctx, args)
    elif args.command == "list-accounts":
        cmd_list_accounts(ctx, args, admins_only=False)
    elif args.command == "list-admins":
        cmd_list_accounts(ctx, args, admins_only=True)
    elif args.command == "get-account":
        cmd_get_account(ctx, args)
    elif args.command == "reset-password":
        cmd_reset_password(ctx, args)
    elif args.command == "create-room":
        args.visibility = "public" if args.public else "private" if args.private else ""
        cmd_create_room(ctx, args)
    elif args.command == "setup-auto-join-rooms":
        cmd_setup_auto_join_rooms(ctx, args)
    else:
        die(f"Unknown command: {args.command}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
