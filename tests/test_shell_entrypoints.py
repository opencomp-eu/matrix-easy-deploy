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


if __name__ == "__main__":
    unittest.main()