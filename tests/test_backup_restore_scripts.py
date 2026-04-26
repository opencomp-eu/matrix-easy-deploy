import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


class BackupRestoreScriptTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.lib_script = self.repo_root / "scripts/lib.sh"
        self.backup_config_script = self.repo_root / "scripts/backup_config.py"
        self.backup_script = self.repo_root / "backup.sh"
        self.restore_script = self.repo_root / "restore.sh"

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content)
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR)

    def _create_min_repo(self, root: Path) -> None:
        (root / "scripts").mkdir(parents=True)
        (root / ".matrix-easy-deploy").mkdir(parents=True)
        (root / "modules/core/synapse_data").mkdir(parents=True)
        (root / "modules/hookshot/hookshot").mkdir(parents=True)
        (root / "modules/whatsapp-bridge/whatsapp").mkdir(parents=True)
        (root / "modules/slack-bridge/slack").mkdir(parents=True)
        (root / "modules/draupnir/draupnir").mkdir(parents=True)

        (root / "scripts/lib.sh").write_text(self.lib_script.read_text())
        (root / "scripts/backup_config.py").write_text(self.backup_config_script.read_text())
        (root / "backup.sh").write_text(self.backup_script.read_text())
        (root / "restore.sh").write_text(self.restore_script.read_text())
        (root / "backup.sh").chmod(0o755)
        (root / "restore.sh").chmod(0o755)

        (root / "VERSION").write_text("1.2.3\n")
        (root / ".env").write_text("POSTGRES_PASSWORD=secret\n")
        (root / ".matrix-easy-deploy/secrets.yaml").write_text("POSTGRES_PASSWORD: secret\n")
        (root / ".matrix-easy-deploy/modules.yaml").write_text("{}\n")

        (root / "deploy.yaml").write_text(
            "matrix:\n"
            "  domain: matrix.example.com\n"
            "  server_name: example.com\n"
            "  admin_username: admin\n"
            "backup:\n"
            "  enabled: true\n"
            "  repository:\n"
            "    type: local\n"
            f"    path: {root}/backups\n"
            "  retention:\n"
            "    keep_daily: 7\n"
            "    keep_weekly: 4\n"
            "    keep_monthly: 6\n"
            "    keep_yearly: 0\n"
        )

    def test_backup_uses_live_dump_and_runs_borgmatic_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_min_repo(root)

            events = root / "events.log"
            self._write_executable(root / "stop.sh", "#!/usr/bin/env bash\necho stop >> \"$EVENTS\"\n")
            self._write_executable(root / "start.sh", "#!/usr/bin/env bash\necho start >> \"$EVENTS\"\n")

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True)
            self._write_executable(
                fake_bin / "docker",
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == \"ps\" ]]; then echo matrix_postgres; exit 0; fi\n"
                "if [[ \"${1:-}\" == \"volume\" && \"${2:-}\" == \"inspect\" ]]; then exit 1; fi\n"
                "if [[ \"${1:-}\" == \"exec\" ]]; then echo docker:$* >> \"$EVENTS\"; echo dump-bytes; exit 0; fi\n"
                "exit 0\n",
            )
            self._write_executable(
                fake_bin / "borg",
                "#!/usr/bin/env bash\nexit 0\n",
            )
            self._write_executable(
                fake_bin / "borgmatic",
                "#!/usr/bin/env bash\n"
                "echo borgmatic:$* >> \"$EVENTS\"\n"
                "if [[ \"$*\" == *\" list\"* ]]; then echo archive-1; fi\n"
                "exit 0\n",
            )

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)

            result = subprocess.run(
                ["bash", "backup.sh"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertTrue(any(line.startswith("docker:exec ") and "pg_dump" in line for line in lines))
            self.assertIn("borgmatic:--config", lines[1])
            self.assertTrue(any(" repo-create " in line for line in lines))
            self.assertTrue(any(" create " in line for line in lines))
            self.assertTrue(any(" prune" in line for line in lines))
            self.assertTrue(any(" check" in line for line in lines))
            self.assertNotIn("stop", lines)
            self.assertNotIn("start", lines)

            borgmatic_config = (root / ".matrix-easy-deploy/backup/borgmatic.yaml").read_text()
            self.assertIn("archive_name_format: 'MED_Backup_{now:%Y-%m-%dT%H:%M:%S}'", borgmatic_config)
            self.assertIn("keep_daily: 7", borgmatic_config)
            self.assertNotIn("retention:", borgmatic_config)

    def test_restore_requires_archive_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_min_repo(root)

            self._write_executable(root / "stop.sh", "#!/usr/bin/env bash\nexit 0\n")
            self._write_executable(root / "start.sh", "#!/usr/bin/env bash\nexit 0\n")
            self._write_executable(root / "apply.sh", "#!/usr/bin/env bash\nexit 0\n")

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True)
            self._write_executable(fake_bin / "docker", "#!/usr/bin/env bash\nexit 0\n")
            self._write_executable(fake_bin / "borg", "#!/usr/bin/env bash\nexit 0\n")
            self._write_executable(fake_bin / "borgmatic", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

            result = subprocess.run(
                ["bash", "restore.sh", "--yes"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Provide --archive", result.stderr)

    def test_restore_runs_expected_order_for_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_min_repo(root)

            events = root / "events.log"
            self._write_executable(root / "stop.sh", "#!/usr/bin/env bash\necho stop >> \"$EVENTS\"\n")
            self._write_executable(root / "start.sh", "#!/usr/bin/env bash\necho start >> \"$EVENTS\"\n")
            self._write_executable(root / "apply.sh", "#!/usr/bin/env bash\necho apply >> \"$EVENTS\"\n")

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True)
            self._write_executable(
                fake_bin / "docker",
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == \"compose\" && \"${2:-}\" == \"version\" ]]; then exit 0; fi\n"
                "if [[ \"${1:-}\" == \"ps\" ]]; then echo matrix_postgres; exit 0; fi\n"
                "if [[ \"${1:-}\" == \"inspect\" ]]; then echo healthy; exit 0; fi\n"
                "if [[ \"${1:-}\" == \"volume\" && \"${2:-}\" == \"inspect\" ]]; then exit 1; fi\n"
                "if [[ \"${1:-}\" == \"network\" && \"${2:-}\" == \"inspect\" ]]; then exit 0; fi\n"
                "if [[ \"${1:-}\" == \"compose\" && \"${2:-}\" == \"up\" ]]; then echo docker:$* >> \"$EVENTS\"; exit 0; fi\n"
                "if [[ \"${1:-}\" == \"exec\" ]]; then echo docker:$* >> \"$EVENTS\"; if [[ \"$*\" == *\" pg_restore \"* ]]; then cat >/dev/null; fi; exit 0; fi\n"
                "exit 0\n",
            )
            self._write_executable(
                fake_bin / "borg",
                "#!/usr/bin/env bash\n"
                "echo borg:$* >> \"$EVENTS\"\n"
                "if [[ \"${1:-}\" == \"list\" ]]; then\n"
                "  echo 'debian-s-1vcpu-2gb-fra1-01-2026-04-25T09:30:59.487313 Sat, 2026-04-25 09:30:59 [74125a60c0a4a76a8b984e799311b8838605430da359ccc054c4ec51e6c41900]'\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"${1:-}\" == \"extract\" ]]; then\n"
                "  mkdir -p payload/.matrix-easy-deploy payload/modules/core/synapse_data payload/database\n"
                "  cat > payload/deploy.yaml <<'EOF'\n"
                "matrix:\n"
                "  domain: matrix.example.com\n"
                "  server_name: example.com\n"
                "  admin_username: admin\n"
                "backup:\n"
                "  enabled: true\n"
                "  repository:\n"
                "    type: local\n"
                "    path: /tmp/restore-backups\n"
                "  retention:\n"
                "    keep_daily: 7\n"
                "    keep_weekly: 4\n"
                "    keep_monthly: 6\n"
                "    keep_yearly: 0\n"
                "EOF\n"
                "  echo '{}' > payload/.matrix-easy-deploy/modules.yaml\n"
                "  echo 'POSTGRES_PASSWORD: restored' > payload/.matrix-easy-deploy/secrets.yaml\n"
                "  echo dump > payload/database/synapse.dump\n"
                "fi\n"
                "exit 0\n",
            )
            self._write_executable(fake_bin / "borgmatic", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)

            result = subprocess.run(
                ["bash", "restore.sh", "--archive", "74125a60c0a4a76a", "--yes"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertIn("borg:list", lines[0])
            self.assertEqual(lines[1], "stop")
            self.assertIn("borg:extract /tmp", lines[2])
            self.assertIn("::debian-s-1vcpu-2gb-fra1-01-2026-04-25T09:30:59.487313", lines[2])
            self.assertEqual(lines[3], "apply")
            self.assertTrue(any(line.startswith("docker:compose up -d postgres") for line in lines))
            self.assertTrue(any(line.startswith("docker:exec ") and "DROP DATABASE IF EXISTS synapse" in line for line in lines))
            self.assertTrue(any(line.startswith("docker:exec ") and "CREATE DATABASE synapse" in line for line in lines))
            self.assertTrue(any(line.startswith("docker:exec ") and "pg_restore" in line for line in lines))
            self.assertEqual(lines[-2], "apply")
            self.assertEqual(lines[-1], "start")

            borgmatic_config = (root / ".matrix-easy-deploy/backup/borgmatic.yaml").read_text()
            self.assertIn("archive_name_format: 'MED_Backup_{now:%Y-%m-%dT%H:%M:%S}'", borgmatic_config)
            self.assertIn("keep_daily: 7", borgmatic_config)
            self.assertNotIn("retention:", borgmatic_config)


if __name__ == "__main__":
    unittest.main()
