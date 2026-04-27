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

    def test_ensure_dependencies_uses_apt_and_starts_docker(self):
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
                fake_bin / "sudo",
                "#!/bin/bash\n"
                "exec \"$@\"\n",
            )
            self._write_executable(
                fake_bin / "apt-get",
                "#!/bin/bash\n"
                "echo apt-get:$* >> \"$EVENTS\"\n"
                "if [[ \"${1:-}\" == \"install\" ]]; then\n"
                "  /bin/touch \"$STATE/docker_compose\"\n"
                "fi\n",
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
            self._write_executable(fake_bin / "curl", "#!/bin/bash\nexit 0\n")
            self._write_executable(fake_bin / "python3", "#!/bin/bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = str(fake_bin)
            env["EVENTS"] = str(events)
            env["STATE"] = str(state_dir)

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
            self.assertIn("apt-get:update", lines)
            self.assertIn("apt-get:install -y docker-compose-plugin", lines)
            self.assertIn("systemctl:enable --now docker", lines)
            self.assertIn("All dependencies satisfied.", result.stdout)


if __name__ == "__main__":
    unittest.main()