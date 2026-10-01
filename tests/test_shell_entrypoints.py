import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tests.helpers.lib_tree import copy_executable_script, require_easydeploy_lib, stage_product_lib_scripts

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class ShellEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        require_easydeploy_lib(self.repo_root)
        self.apply_script = self.repo_root / "apply.sh"
        self.ensure_dependencies_script = self.repo_root / "ensure_dependencies.sh"
        self.med_admin_script = self.repo_root / "scripts/med-admin.sh"
        self.med_admin_py = self.repo_root / "scripts/med_admin.py"
        self.create_account_script = self.repo_root / "scripts/create-account.sh"

    def _copy_lib_scripts(self, root: Path) -> None:
        stage_product_lib_scripts(self.repo_root, root)

    def _copy_executable(self, src: Path, dest: Path) -> None:
        copy_executable_script(src, dest)

    def _write_executable(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _write_fake_docker_mas(self, path: Path, *, exec_body: str = "") -> None:
        """Fake docker for MAS create-account tests (health probe + optional exec logic)."""
        script = (
            "#!/usr/bin/env bash\n"
            'echo docker:$* >> "$EVENTS"\n'
            'if [[ "$1" == inspect ]]; then\n'
            '  if [[ "${2:-}" == --format=* ]]; then\n'
            '    echo healthy\n'
            "  fi\n"
            "  exit 0\n"
            "fi\n"
            'if [[ "$1" == exec && "$3" == python3 ]]; then\n'
            "  exit 0\n"
            "fi\n"
            f"{exec_body}"
            "exit 1\n"
        )
        self._write_executable(path, script)

    def _install_med_admin(self, root: Path) -> None:
        self._copy_executable(self.med_admin_script, root / "scripts/med-admin.sh")
        self._copy_executable(self.med_admin_py, root / "scripts/med_admin.py")

    def _start_mock_synapse_server(self, events: Path) -> tuple[str, HTTPServer]:
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text("")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args) -> None:
                return

            def _log(self, line: str) -> None:
                with events.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")

            def _read_body(self) -> bytes:
                length = int(self.headers.get("Content-Length", 0))
                return self.rfile.read(length) if length else b""

            def _send_json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                body = self._read_body()
                self._log(f"POST {self.path} payload:{body.decode('utf-8')}")
                if self.path.endswith("/_matrix/client/v3/login"):
                    self._send_json(200, {"access_token": "tok123"})
                    return
                if "/_synapse/admin/v1/reset_password/" in self.path:
                    self._send_json(200, {})
                    return
                self.send_error(404)

            def do_GET(self) -> None:
                self._log(f"GET {self.path}")
                if self.path.startswith("/_synapse/admin/v2/users?"):
                    self._send_json(
                        200,
                        {
                            "users": [
                                {
                                    "name": "@alice:example.com",
                                    "admin": False,
                                    "deactivated": False,
                                    "locked": False,
                                    "displayname": "Alice",
                                }
                            ],
                            "total": 1,
                        },
                    )
                    return
                if self.path.startswith("/_synapse/admin/v2/users/%40alice%3Aexample.com"):
                    self._send_json(
                        200,
                        {
                            "name": "@alice:example.com",
                            "admin": False,
                            "deactivated": False,
                            "locked": False,
                            "is_guest": False,
                            "displayname": "Alice",
                            "avatar_url": None,
                            "creation_ts": 1,
                            "last_seen_ts": 2,
                        },
                    )
                    return
                self.send_error(404)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address

        def _stop() -> None:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.addCleanup(_stop)
        return f"http://{host}:{port}", server

    def test_apply_ensure_dependencies_runs_installer_before_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._copy_executable(self.apply_script, root / "apply.sh")
            self._write_executable(
                root / "ensure_dependencies.sh",
                "#!/usr/bin/env bash\n"
                "echo ensure >> \"$EVENTS\"\n",
            )
            (root / "scripts").mkdir(parents=True, exist_ok=True)
            (root / "scripts/apply.py").write_text("print('stub')\n")

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_executable(
                fake_bin / "python3",
                "#!/usr/bin/env bash\n"
                "echo python3:$* >> \"$EVENTS\"\n",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)

            result = subprocess.run(
                ["bash", "apply.sh", "--ensure-dependencies", "--project-root", "/srv/med", "--rotate-secrets"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = [line for line in events.read_text().splitlines() if "import yaml" not in line]
            self.assertEqual(lines[0], "ensure")
            self.assertIn("scripts/apply.py --project-root /srv/med --rotate-secrets", lines[1])
            self.assertNotIn("--ensure-dependencies", lines[1])

    def test_apply_uses_uv_when_system_python_lacks_pyyaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._copy_executable(self.apply_script, root / "apply.sh")
            (root / "scripts").mkdir(parents=True, exist_ok=True)
            (root / "scripts/apply.py").write_text("print('stub')\n")

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_executable(
                fake_bin / "python3",
                "#!/usr/bin/env bash\n"
                "if [[ \"$1\" == \"-c\" ]]; then exit 1; fi\n"
                "echo python3:$* >> \"$EVENTS\"\n",
            )
            self._write_executable(
                fake_bin / "uv",
                "#!/usr/bin/env bash\n"
                "echo uv:$* >> \"$EVENTS\"\n",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)

            result = subprocess.run(
                ["bash", "apply.sh", "--rotate-secrets"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("uv:run --project "), lines[0])
            self.assertIn("scripts/apply.py --rotate-secrets", lines[0])

    def test_create_account_noninteractive_keeps_nonce_output_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"
            payload_file = root / "payload.json"

            self._copy_executable(self.create_account_script, root / "scripts/create-account.sh")
            self._copy_lib_scripts(root)
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "REGISTRATION_SHARED_SECRET=sharedsecret\n"
                "MAS_ENABLED=false\n"
            )

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_executable(
                fake_bin / "curl",
                "#!/usr/bin/env bash\n"
                "echo curl:$* >> \"$EVENTS\"\n"
                "outfile=''\n"
                "write_status='false'\n"
                "url=''\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  case \"$1\" in\n"
                "    -o) outfile=\"$2\"; shift 2 ;;\n"
                "    -w) write_status='true'; shift 2 ;;\n"
                "    http*://*|https*://*) url=\"$1\"; shift ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "if [[ \"$url\" == *\"/_synapse/admin/v1/register\" && \"$write_status\" == 'false' ]]; then\n"
                "  printf '{\"nonce\":\"abc123\"}'\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$url\" == *\"/_synapse/admin/v1/register\" && \"$write_status\" == 'true' ]]; then\n"
                "  cat > \"$PAYLOAD_FILE\"\n"
                "  printf '{}' > \"$outfile\"\n"
                "  printf '200'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)
            env["PAYLOAD_FILE"] = str(payload_file)

            result = subprocess.run(
                [
                    "bash",
                    "scripts/create-account.sh",
                    "--username",
                    "test",
                    "--password",
                    "averylongsecret",
                    "--yes",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Account '@test:example.com' created successfully.", result.stdout)
            self.assertIn("Fetching registration nonce from Synapse", result.stderr)
            payload = payload_file.read_text()
            self.assertIn('"nonce": "abc123"', payload)
            self.assertNotIn("Fetching registration nonce", payload)

    def test_create_account_mas_path_registers_and_promotes_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._copy_executable(self.create_account_script, root / "scripts/create-account.sh")
            self._copy_lib_scripts(root)
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "MAS_ENABLED=true\n"
                "MAS_LOCAL_LOGIN_ENABLED=true\n"
                "MAS_HOMESERVER_SECRET=mas-admin-token\n"
            )

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_fake_docker_mas(
                fake_bin / "docker",
                exec_body=(
                    'if [[ "$1" == exec && "$3" == mas-cli ]]; then\n'
                    "  exit 0\n"
                    "fi\n"
                ),
            )
            self._write_executable(
                fake_bin / "curl",
                "#!/usr/bin/env bash\n"
                "echo curl:$* >> \"$EVENTS\"\n"
                "outfile=''\n"
                "write_status='false'\n"
                "url=''\n"
                "method=''\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  case \"$1\" in\n"
                "    -o) outfile=\"$2\"; shift 2 ;;\n"
                "    -w) write_status='true'; shift 2 ;;\n"
                "    -X) method=\"$2\"; shift 2 ;;\n"
                "    http*://*|https*://*) url=\"$1\"; shift ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "if [[ \"$method\" == PUT && \"$url\" == *\"/_synapse/admin/v2/users/\"* ]]; then\n"
                "  printf '200' > \"$outfile\"\n"
                "  printf '200'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)

            result = subprocess.run(
                [
                    "bash",
                    "scripts/create-account.sh",
                    "--username",
                    "alice",
                    "--password",
                    "averylongsecret",
                    "--admin",
                    "--yes",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("Account '@alice:example.com' created successfully.", result.stdout)
            events_text = events.read_text()
            self.assertIn("manage register-user", events_text)
            self.assertIn("/_synapse/admin/v2/users/", events_text)
            self.assertNotIn("Fetching registration nonce", result.stderr)

    def test_create_account_mas_existing_user_retries_admin_grant_on_user_in_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"
            put_attempts = root / "put_attempts.txt"
            put_attempts.write_text("0")

            self._copy_executable(self.create_account_script, root / "scripts/create-account.sh")
            self._copy_lib_scripts(root)
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "MAS_ENABLED=true\n"
                "MAS_LOCAL_LOGIN_ENABLED=true\n"
                "MAS_HOMESERVER_SECRET=mas-admin-token\n"
            )

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_fake_docker_mas(
                fake_bin / "docker",
                exec_body=(
                    'if [[ "$1" == exec && "$3" == mas-cli ]]; then\n'
                    '  if [[ "$*" == *register-user* ]]; then\n'
                    "    echo User already exists >&2\n"
                    "    exit 1\n"
                    "  fi\n"
                    '  if [[ "$*" == *set-password* ]]; then\n'
                    "    exit 0\n"
                    "  fi\n"
                    "fi\n"
                ),
            )
            self._write_executable(
                fake_bin / "curl",
                "#!/usr/bin/env bash\n"
                "echo curl:$* >> \"$EVENTS\"\n"
                "outfile=''\n"
                "write_status='false'\n"
                "url=''\n"
                "method=''\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  case \"$1\" in\n"
                "    -o) outfile=\"$2\"; shift 2 ;;\n"
                "    -w) write_status='true'; shift 2 ;;\n"
                "    -X) method=\"$2\"; shift 2 ;;\n"
                "    http*://*|https*://*) url=\"$1\"; shift ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "if [[ \"$method\" == PUT && \"$url\" == *\"/_synapse/admin/v2/users/\"* ]]; then\n"
                "  attempt=$(cat \"$PUT_ATTEMPTS\")\n"
                "  attempt=$((attempt + 1))\n"
                "  printf '%s' \"$attempt\" > \"$PUT_ATTEMPTS\"\n"
                "  if [[ \"$attempt\" == 1 ]]; then\n"
                "    printf '{\"errcode\":\"M_USER_IN_USE\",\"error\":\"User ID already taken.\"}' > \"$outfile\"\n"
                "    printf '400'\n"
                "    exit 0\n"
                "  fi\n"
                "  printf '200' > \"$outfile\"\n"
                "  printf '200'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)
            env["PUT_ATTEMPTS"] = str(put_attempts)

            result = subprocess.run(
                [
                    "bash",
                    "scripts/create-account.sh",
                    "--username",
                    "med-admin",
                    "--password",
                    "averylongsecret",
                    "--admin",
                    "--yes",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            combined = result.stdout + result.stderr
            self.assertIn("Retrying admin grant as update", combined)
            self.assertIn("Account '@med-admin:example.com' created successfully.", result.stdout)
            self.assertEqual(put_attempts.read_text(), "2")

    def test_create_account_mas_existing_user_updates_password_only_when_local_login_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._copy_executable(self.create_account_script, root / "scripts/create-account.sh")
            self._copy_lib_scripts(root)
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "MAS_ENABLED=true\n"
                "MAS_LOCAL_LOGIN_ENABLED=true\n"
            )

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_fake_docker_mas(
                fake_bin / "docker",
                exec_body=(
                    'if [[ "$1" == exec && "$3" == mas-cli ]]; then\n'
                    '  if [[ "$*" == *register-user* ]]; then\n'
                    "    echo User already exists >&2\n"
                    "    exit 1\n"
                    "  fi\n"
                    '  if [[ "$*" == *set-password* ]]; then\n'
                    "    exit 0\n"
                    "  fi\n"
                    "fi\n"
                ),
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)

            result = subprocess.run(
                [
                    "bash",
                    "scripts/create-account.sh",
                    "--username",
                    "alice",
                    "--password",
                    "averylongsecret",
                    "--yes",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            combined = result.stdout + result.stderr
            self.assertIn("Updating password (local login is enabled)", combined)
            events_text = events.read_text()
            self.assertIn("manage register-user", events_text)
            self.assertIn("manage set-password", events_text)

    def test_create_account_mas_rejects_sso_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._copy_executable(self.create_account_script, root / "scripts/create-account.sh")
            self._copy_lib_scripts(root)
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "MAS_ENABLED=true\n"
                "MAS_LOCAL_LOGIN_ENABLED=false\n"
            )

            result = subprocess.run(
                [
                    "bash",
                    "scripts/create-account.sh",
                    "--username",
                    "alice",
                    "--password",
                    "averylongsecret",
                    "--yes",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("local_login_enabled=true", result.stderr)

    def test_med_admin_bootstrap_delegates_to_create_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._install_med_admin(root)
            self._write_executable(
                root / "scripts/create-account.sh",
                "#!/usr/bin/env bash\n"
                "echo create-account:$* >> \"$EVENTS\"\n",
            )
            base_url, _server = self._start_mock_synapse_server(events)
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "REGISTRATION_SHARED_SECRET=sharedsecret\n"
                "ADMIN_USERNAME=admin\n"
            )

            env = os.environ.copy()
            env["EVENTS"] = str(events)

            result = subprocess.run(
                [
                    "bash",
                    "scripts/med-admin.sh",
                    "--base-url",
                    base_url,
                    "bootstrap",
                    "--username",
                    "med-admin",
                    "--password",
                    "averylongsecret123",
                    "--yes",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            line = events.read_text().strip()
            self.assertIn("create-account:", line)
            self.assertIn("--username med-admin", line)
            self.assertIn("--admin", line)
            self.assertIn(f"--base-url {base_url}", line)
            self.assertIn("--shared-secret sharedsecret", line)
            self.assertIn("--password averylongsecret123", line)
            self.assertIn("--yes", line)
            env_text = (root / ".env").read_text()
            self.assertIn("MED_ADMIN_USERNAME=med-admin", env_text)
            self.assertIn("MED_ADMIN_PASSWORD=averylongsecret123", env_text)

    def test_med_admin_list_accounts_logs_in_and_queries_admin_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._install_med_admin(root)
            base_url, _server = self._start_mock_synapse_server(events)
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "ADMIN_USERNAME=bootstrapadmin\n"
            )

            result = subprocess.run(
                [
                    "bash",
                    "scripts/med-admin.sh",
                    "--base-url",
                    base_url,
                    "list-accounts",
                    "--filter",
                    "alice",
                    "--limit",
                    "25",
                    "--admin-password",
                    "averylongsecret",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertTrue(any("POST /_matrix/client/v3/login" in line for line in lines))
            self.assertTrue(
                any(
                    "GET /_synapse/admin/v2/users?limit=25&guests=false&name=alice" in line
                    for line in lines
                )
            )
            self.assertIn("USER_ID\tADMIN\tDEACTIVATED\tLOCKED\tDISPLAYNAME", result.stdout)
            self.assertIn("@alice:example.com\tFalse\tFalse\tFalse\tAlice", result.stdout)
            self.assertIn("TOTAL\t1", result.stdout)

    def test_med_admin_get_account_uses_encoded_mxid_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._install_med_admin(root)
            base_url, _server = self._start_mock_synapse_server(events)
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "ADMIN_USERNAME=bootstrapadmin\n"
            )

            result = subprocess.run(
                [
                    "bash",
                    "scripts/med-admin.sh",
                    "--base-url",
                    base_url,
                    "get-account",
                    "alice",
                    "--admin-password",
                    "averylongsecret",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertTrue(
                any("GET /_synapse/admin/v2/users/%40alice%3Aexample.com" in line for line in lines)
            )
            self.assertIn("User ID:      @alice:example.com", result.stdout)
            self.assertIn("Display name: Alice", result.stdout)

    def test_med_admin_reset_password_posts_admin_api_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._install_med_admin(root)
            base_url, _server = self._start_mock_synapse_server(events)
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "ADMIN_USERNAME=bootstrapadmin\n"
            )

            result = subprocess.run(
                [
                    "bash",
                    "scripts/med-admin.sh",
                    "--base-url",
                    base_url,
                    "reset-password",
                    "alice",
                    "--password",
                    "averylongsecret",
                    "--admin-password",
                    "adminpass12345",
                    "--yes",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertTrue(
                any(
                    "POST /_synapse/admin/v1/reset_password/%40alice%3Aexample.com" in line
                    for line in lines
                )
            )
            self.assertTrue(
                any(
                    '"new_password": "averylongsecret", "logout_devices": true' in line
                    for line in lines
                )
            )
            self.assertIn("Password reset for '@alice:example.com'.", result.stdout)


if __name__ == "__main__":
    unittest.main()