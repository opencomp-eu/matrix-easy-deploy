import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


class UninstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.uninstall_script = self.repo_root / "uninstall.sh"
        self.lib_script = self.repo_root / "scripts/lib.sh"

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content)
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR)

    def test_uninstall_yes_cleans_generated_state_and_preserves_deploy_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # Minimal script dependencies
            (root / "scripts").mkdir(parents=True)
            (root / "modules/core/synapse").mkdir(parents=True)
            (root / "modules/core/element").mkdir(parents=True)
            (root / "modules/core/synapse_data").mkdir(parents=True)
            (root / "modules/calls/coturn").mkdir(parents=True)
            (root / "modules/calls/livekit").mkdir(parents=True)
            (root / "modules/hookshot/hookshot").mkdir(parents=True)
            (root / "modules/whatsapp-bridge/whatsapp").mkdir(parents=True)
            (root / "modules/slack-bridge/slack").mkdir(parents=True)
            (root / "modules/draupnir/draupnir").mkdir(parents=True)
            (root / "caddy").mkdir(parents=True)
            (root / ".matrix-easy-deploy").mkdir(parents=True)

            (root / "scripts/lib.sh").write_text(self.lib_script.read_text())
            (root / "uninstall.sh").write_text(self.uninstall_script.read_text())
            (root / "uninstall.sh").chmod(0o755)

            (root / "deploy.yaml").write_text("matrix:\n  domain: matrix.example.com\n")
            (root / ".env").write_text("MATRIX_DOMAIN=matrix.example.com\n")
            (root / "caddy/Caddyfile").write_text("generated\n")
            (root / "modules/core/synapse/homeserver.yaml").write_text("generated\n")
            (root / "modules/core/element/config.json").write_text("{}\n")
            (root / "modules/calls/coturn/turnserver.conf").write_text("generated\n")
            (root / "modules/calls/livekit/livekit.yaml").write_text("generated\n")

            stop_marker = root / "stop-called.txt"
            self._write_executable(
                root / "stop.sh",
                "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'called' > stop-called.txt\n",
            )

            docker_log = root / "docker.log"
            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True)
            self._write_executable(
                fake_bin / "docker",
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf '%s\n' \"$*\" >> \"$DOCKER_LOG\"\n"
                "if [[ \"${1:-}\" == \"container\" && \"${2:-}\" == \"inspect\" ]]; then\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"${1:-}\" == \"volume\" && \"${2:-}\" == \"inspect\" ]]; then\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"${1:-}\" == \"network\" && \"${2:-}\" == \"inspect\" ]]; then\n"
                "  exit 0\n"
                "fi\n"
                "exit 0\n",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["DOCKER_LOG"] = str(docker_log)

            result = subprocess.run(
                ["bash", "uninstall.sh", "--yes"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(stop_marker.exists())
            self.assertTrue((root / "deploy.yaml").exists())

            self.assertFalse((root / ".env").exists())
            self.assertFalse((root / ".matrix-easy-deploy").exists())
            self.assertFalse((root / "caddy/Caddyfile").exists())
            self.assertFalse((root / "modules/core/synapse/homeserver.yaml").exists())
            self.assertFalse((root / "modules/core/element/config.json").exists())
            self.assertFalse((root / "modules/core/synapse_data").exists())
            self.assertFalse((root / "modules/calls/coturn/turnserver.conf").exists())
            self.assertFalse((root / "modules/calls/livekit/livekit.yaml").exists())
            self.assertFalse((root / "modules/hookshot/hookshot").exists())
            self.assertFalse((root / "modules/whatsapp-bridge/whatsapp").exists())
            self.assertFalse((root / "modules/slack-bridge/slack").exists())

            docker_calls = docker_log.read_text()
            self.assertIn("rm -f matrix_synapse", docker_calls)
            self.assertIn("volume rm core_postgres_data", docker_calls)
            self.assertIn("network rm caddy_net", docker_calls)


if __name__ == "__main__":
    unittest.main()
