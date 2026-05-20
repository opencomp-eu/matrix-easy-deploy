import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


class ShellEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.apply_script = self.repo_root / "apply.sh"
        self.ensure_dependencies_script = self.repo_root / "ensure_dependencies.sh"
        self.create_user_script = self.repo_root / "scripts/create-user.sh"
        self.med_admin_script = self.repo_root / "scripts/med-admin.sh"
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
                "  exec /bin/sh \"$@\"\n"
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
                "        chmod +x \"$FAKE_BIN/borg\"\n"
                "        ;;\n"
                "      borgmatic)\n"
                "        /bin/cat > \"$FAKE_BIN/borgmatic\" <<'EOF'\n"
                "#!/bin/bash\n"
                "exit 0\n"
                "EOF\n"
                "        chmod +x \"$FAKE_BIN/borgmatic\"\n"
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
                "#!/bin/bash\n"
                "echo docker-script:$* >> \"$EVENTS\"\n"
                "if [[ -n \"${STATE:-}\" ]]; then\n"
                "  /bin/touch \"$STATE/docker_compose\"\n"
                "fi\n"
                "EOF\n"
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
            self.assertTrue(any(line == "apt-get:install -y borgbackup borgmatic" for line in lines))
            self.assertTrue(any(line.startswith("curl:-fsSL https://get.docker.com -o ") for line in lines))
            self.assertTrue(any(line.startswith("docker-script:") for line in lines))
            self.assertIn("systemctl:enable --now docker", lines)
            self.assertIn("All dependencies satisfied.", result.stdout)

    def test_create_user_supports_noninteractive_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._copy_executable(self.create_user_script, root / "scripts/create-user.sh")
            self._copy_executable(self.lib_script, root / "scripts/lib.sh")
            (root / ".env").write_text("SERVER_NAME=example.com\n")

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_executable(
                fake_bin / "docker",
                "#!/usr/bin/env bash\n"
                "echo docker:$* >> \"$EVENTS\"\n"
                "if [[ \"${1:-}\" == \"inspect\" ]]; then\n"
                "  if [[ \"${2:-}\" == \"--format={{.State.Running}}\" ]]; then\n"
                "    echo true\n"
                "    exit 0\n"
                "  fi\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"${1:-}\" == \"exec\" ]]; then\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )
            self._write_executable(fake_bin / "openssl", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)

            result = subprocess.run(
                [
                    "bash",
                    "scripts/create-user.sh",
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

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertIn("docker:inspect matrix_synapse", lines)
            self.assertIn("docker:inspect --format={{.State.Running}} matrix_synapse", lines)
            self.assertIn(
                "docker:exec -i matrix_synapse register_new_matrix_user -c /data/homeserver.yaml -u alice -p averylongsecret -a http://localhost:8008",
                lines,
            )
            self.assertIn("@alice:example.com", result.stdout)
            self.assertIn("User created successfully.", result.stdout)

    def test_med_admin_bootstrap_delegates_to_create_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._copy_executable(self.med_admin_script, root / "scripts/med-admin.sh")
            self._copy_executable(self.lib_script, root / "scripts/lib.sh")
            self._write_executable(
                root / "scripts/create-account.sh",
                "#!/usr/bin/env bash\n"
                "echo create-account:$* >> \"$EVENTS\"\n",
            )
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
                    "bootstrap",
                    "--username",
                    "med-admin",
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
            line = events.read_text().strip()
            self.assertIn("create-account:", line)
            self.assertIn("--username med-admin", line)
            self.assertIn("--admin", line)
            self.assertIn("--base-url https://matrix.example.com", line)
            self.assertIn("--shared-secret sharedsecret", line)
            self.assertIn("--password averylongsecret", line)
            self.assertIn("--yes", line)

    def test_med_admin_list_accounts_logs_in_and_queries_admin_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._copy_executable(self.med_admin_script, root / "scripts/med-admin.sh")
            self._copy_executable(self.lib_script, root / "scripts/lib.sh")
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "ADMIN_USERNAME=bootstrapadmin\n"
            )

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_executable(
                fake_bin / "curl",
                "#!/usr/bin/env bash\n"
                "echo curl:$* >> \"$EVENTS\"\n"
                "outfile=''\n"
                "url=''\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  case \"$1\" in\n"
                "    -o) outfile=\"$2\"; shift 2 ;;\n"
                "    http*://*|https*://*) url=\"$1\"; shift ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "if [[ \"$url\" == *\"/_matrix/client/v3/login\" ]]; then\n"
                "  cat >/dev/null\n"
                "  printf '{\"access_token\":\"tok123\"}'\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$url\" == *\"/_synapse/admin/v2/users?\"* ]]; then\n"
                "  printf '{\"users\":[{\"name\":\"@alice:example.com\",\"admin\":false,\"deactivated\":false,\"locked\":false,\"displayname\":\"Alice\"}],\"total\":1}' > \"$outfile\"\n"
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
                    "scripts/med-admin.sh",
                    "list-accounts",
                    "--filter",
                    "alice",
                    "--limit",
                    "25",
                    "--admin-password",
                    "averylongsecret",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertTrue(any("/_matrix/client/v3/login" in line for line in lines))
            self.assertTrue(any("/_synapse/admin/v2/users?limit=25&guests=false&name=alice" in line for line in lines))
            self.assertIn("USER_ID\tADMIN\tDEACTIVATED\tLOCKED\tDISPLAYNAME", result.stdout)
            self.assertIn("@alice:example.com\tFalse\tFalse\tFalse\tAlice", result.stdout)
            self.assertIn("TOTAL\t1", result.stdout)

    def test_med_admin_get_account_uses_encoded_mxid_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._copy_executable(self.med_admin_script, root / "scripts/med-admin.sh")
            self._copy_executable(self.lib_script, root / "scripts/lib.sh")
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "ADMIN_USERNAME=bootstrapadmin\n"
            )

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_executable(
                fake_bin / "curl",
                "#!/usr/bin/env bash\n"
                "echo curl:$* >> \"$EVENTS\"\n"
                "outfile=''\n"
                "url=''\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  case \"$1\" in\n"
                "    -o) outfile=\"$2\"; shift 2 ;;\n"
                "    http*://*|https*://*) url=\"$1\"; shift ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "if [[ \"$url\" == *\"/_matrix/client/v3/login\" ]]; then\n"
                "  cat >/dev/null\n"
                "  printf '{\"access_token\":\"tok123\"}'\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$url\" == *\"/%40alice%3Aexample.com\" ]]; then\n"
                "  printf '{\"name\":\"@alice:example.com\",\"admin\":false,\"deactivated\":false,\"locked\":false,\"is_guest\":false,\"displayname\":\"Alice\",\"avatar_url\":null,\"creation_ts\":1,\"last_seen_ts\":2}' > \"$outfile\"\n"
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
                    "scripts/med-admin.sh",
                    "get-account",
                    "alice",
                    "--admin-password",
                    "averylongsecret",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertTrue(any("/%40alice%3Aexample.com" in line for line in lines))
            self.assertIn("User ID:      @alice:example.com", result.stdout)
            self.assertIn("Display name: Alice", result.stdout)

    def test_med_admin_reset_password_posts_admin_api_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"

            self._copy_executable(self.med_admin_script, root / "scripts/med-admin.sh")
            self._copy_executable(self.lib_script, root / "scripts/lib.sh")
            (root / ".env").write_text(
                "SERVER_NAME=example.com\n"
                "MATRIX_DOMAIN=matrix.example.com\n"
                "ADMIN_USERNAME=bootstrapadmin\n"
            )

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            self._write_executable(
                fake_bin / "curl",
                "#!/usr/bin/env bash\n"
                "payload=$(cat)\n"
                "echo curl:$* payload:$payload >> \"$EVENTS\"\n"
                "outfile=''\n"
                "url=''\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  case \"$1\" in\n"
                "    -o) outfile=\"$2\"; shift 2 ;;\n"
                "    http*://*|https*://*) url=\"$1\"; shift ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "if [[ \"$url\" == *\"/_matrix/client/v3/login\" ]]; then\n"
                "  printf '{\"access_token\":\"tok123\"}'\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$url\" == *\"/_synapse/admin/v1/reset_password/%40alice%3Aexample.com\" ]]; then\n"
                "  printf '{}' > \"$outfile\"\n"
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
                    "scripts/med-admin.sh",
                    "reset-password",
                    "alice",
                    "--password",
                    "averylongsecret",
                    "--admin-password",
                    "adminpass12345",
                    "--yes",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertTrue(any("/_synapse/admin/v1/reset_password/%40alice%3Aexample.com" in line for line in lines))
            self.assertTrue(any('payload:{"new_password": "averylongsecret", "logout_devices": true}' in line for line in lines))
            self.assertIn("Password reset for '@alice:example.com'.", result.stdout)


if __name__ == "__main__":
    unittest.main()