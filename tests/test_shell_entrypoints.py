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

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class ShellEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.apply_script = self.repo_root / "apply.sh"
        self.ensure_dependencies_script = self.repo_root / "ensure_dependencies.sh"
        self.med_admin_script = self.repo_root / "scripts/med-admin.sh"
        self.med_admin_py = self.repo_root / "scripts/med_admin.py"
        self.create_account_script = self.repo_root / "scripts/create-account.sh"
        self.lib_script = self.repo_root / "scripts/lib.sh"
        self.dependencies_script = self.repo_root / "scripts/setup/dependencies.sh"

    def _write_executable(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _copy_executable(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text())
        dest.chmod(0o755)

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
            lines = events.read_text().splitlines()
            self.assertEqual(lines[0], "ensure")
            self.assertIn("scripts/apply.py --project-root /srv/med --rotate-secrets", lines[1])
            self.assertNotIn("--ensure-dependencies", lines[1])

    def test_ensure_dependencies_uses_official_docker_script_and_starts_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"
            state_dir = root / "state"
            state_dir.mkdir(parents=True, exist_ok=True)

            self._copy_executable(self.ensure_dependencies_script, root / "ensure_dependencies.sh")
            self._copy_executable(self.lib_script, root / "scripts/lib.sh")
            self._copy_executable(self.dependencies_script, root / "scripts/setup/dependencies.sh")

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_executable(
                fake_bin / "dirname",
                "#!/bin/bash\n"
                "path=\"$1\"\n"
                "if [[ \"$path\" == */* ]]; then\n"
                "  printf '%s\\n' \"${path%/*}\"\n"
                "else\n"
                "  printf '.\\n'\n"
                "fi\n",
            )
            self._write_executable(
                fake_bin / "mktemp",
                "#!/bin/bash\n"
                "printf '%s\n' \"${TMPDIR:-/tmp}/get-docker.test.sh\"\n",
            )
            self._write_executable(
                fake_bin / "rm",
                "#!/bin/bash\n"
                "exec /bin/rm \"$@\"\n",
            )
            self._write_executable(
                fake_bin / "sudo",
                "#!/bin/bash\n"
                "if [[ \"${1:-}\" == \"sh\" ]]; then\n"
                "  shift\n"
                "  exec /bin/bash \"$@\"\n"
                "fi\n"
                "exec \"$@\"\n",
            )
            self._write_executable(
                fake_bin / "apt-get",
                "#!/bin/bash\n"
                "echo apt-get:$* >> \"$EVENTS\"\n"
                "if [[ \"${1:-}\" == \"install\" ]]; then\n"
                "  for package in \"$@\"; do\n"
                "    case \"$package\" in\n"
                "      borgbackup)\n"
                "        /bin/cat > \"$FAKE_BIN/borg\" <<'EOF'\n"
                "#!/bin/bash\n"
                "exit 0\n"
                "EOF\n"
                "        /bin/chmod +x \"$FAKE_BIN/borg\"\n"
                "        ;;\n"
                "      borgmatic)\n"
                "        /bin/cat > \"$FAKE_BIN/borgmatic\" <<'EOF'\n"
                "#!/bin/bash\n"
                "exit 0\n"
                "EOF\n"
                "        /bin/chmod +x \"$FAKE_BIN/borgmatic\"\n"
                "        ;;\n"
                "      age)\n"
                "        /bin/cat > \"$FAKE_BIN/age\" <<'EOF'\n"
                "#!/bin/bash\n"
                "exit 0\n"
                "EOF\n"
                "        /bin/chmod +x \"$FAKE_BIN/age\"\n"
                "        ;;\n"
                "    esac\n"
                "  done\n"
                "fi\n"
                "exit 0\n",
            )
            self._write_executable(
                fake_bin / "systemctl",
                "#!/bin/bash\n"
                "echo systemctl:$* >> \"$EVENTS\"\n"
                "if [[ \"${1:-}\" == \"enable\" && \"${2:-}\" == \"--now\" && \"${3:-}\" == \"docker\" ]]; then\n"
                "  /bin/touch \"$STATE/docker_running\"\n"
                "fi\n",
            )
            self._write_executable(
                fake_bin / "docker",
                "#!/bin/bash\n"
                "if [[ \"${1:-}\" == \"compose\" && \"${2:-}\" == \"version\" ]]; then\n"
                "  [[ -f \"$STATE/docker_compose\" ]] && exit 0\n"
                "  exit 1\n"
                "fi\n"
                "if [[ \"${1:-}\" == \"info\" ]]; then\n"
                "  [[ -f \"$STATE/docker_running\" ]] && exit 0\n"
                "  exit 1\n"
                "fi\n"
                "exit 0\n",
            )
            self._write_executable(fake_bin / "openssl", "#!/bin/bash\nexit 0\n")
            self._write_executable(
                fake_bin / "curl",
                "#!/bin/bash\n"
                "echo curl:$* >> \"$EVENTS\"\n"
                "if [[ \"${1:-}\" == \"-fsSL\" && \"${2:-}\" == \"https://get.docker.com\" && \"${3:-}\" == \"-o\" ]]; then\n"
                "  /bin/cat > \"${4}\" <<'EOF'\n"
                "#!/bin/sh\n"
                "echo docker-script:$* >> \"$EVENTS\"\n"
                "if [ -n \"${STATE:-}\" ]; then\n"
                "  /bin/touch \"$STATE/docker_compose\"\n"
                "fi\n"
                "EOF\n"
                "  /bin/chmod +x \"${4}\"\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )
            self._write_executable(fake_bin / "python3", "#!/bin/bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = str(fake_bin)
            env["EVENTS"] = str(events)
            env["STATE"] = str(state_dir)
            env["FAKE_BIN"] = str(fake_bin)

            result = subprocess.run(
                ["/bin/bash", "ensure_dependencies.sh"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertTrue(any(line == "apt-get:update" for line in lines))
            self.assertTrue(any(line == "apt-get:install -y borgbackup borgmatic age" for line in lines))
            self.assertTrue(any(line.startswith("curl:-fsSL https://get.docker.com -o ") for line in lines))
            self.assertTrue(any(line.startswith("docker-script:") for line in lines))
            self.assertIn("systemctl:enable --now docker", lines)
            self.assertIn("All dependencies satisfied.", result.stdout)

    def test_create_account_noninteractive_keeps_nonce_output_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"
            payload_file = root / "payload.json"

            self._copy_executable(self.create_account_script, root / "scripts/create-account.sh")
            self._copy_executable(self.lib_script, root / "scripts/lib.sh")
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "REGISTRATION_SHARED_SECRET=sharedsecret\n"
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