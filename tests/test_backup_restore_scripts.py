import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


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

    def _copy_support_scripts(self, root: Path) -> None:
        for rel in (
            "scripts/lib.sh",
            "scripts/backup_config.py",
            "scripts/backup_payload.py",
            "scripts/backup_payload.sh",
            "scripts/backup_crypto.sh",
            "scripts/restore_payload.sh",
            "scripts/module_common.sh",
        ):
            src = self.repo_root / rel
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src.read_text())
            if rel.endswith(".sh"):
                dest.chmod(0o755)

    def _create_min_repo(self, root: Path) -> None:
        (root / "scripts").mkdir(parents=True)
        (root / ".matrix-easy-deploy").mkdir(parents=True)
        (root / "modules/core/synapse_data").mkdir(parents=True)
        (root / "modules/core").mkdir(parents=True, exist_ok=True)
        (root / "modules/core/docker-compose.yml").write_text("services: {}\n")
        (root / "modules/hookshot/hookshot").mkdir(parents=True)
        (root / "modules/whatsapp-bridge/whatsapp").mkdir(parents=True)
        (root / "modules/slack-bridge/slack").mkdir(parents=True)
        (root / "modules/draupnir/draupnir").mkdir(parents=True)

        self._copy_support_scripts(root)
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

    def test_backup_manifest_v2_in_export_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_min_repo(root)

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True)
            self._write_executable(
                fake_bin / "docker",
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == \"ps\" ]]; then echo matrix_postgres; exit 0; fi\n"
                "if [[ \"${1:-}\" == \"volume\" && \"${2:-}\" == \"inspect\" ]]; then exit 1; fi\n"
                "if [[ \"${1:-}\" == \"exec\" ]]; then echo dump-bytes; exit 0; fi\n"
                "exit 0\n",
            )

            export_path = root / "portable.tar.gz"
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

            result = subprocess.run(
                ["bash", "backup.sh", "--export-only", str(export_path)],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(export_path.exists())
            list_result = subprocess.run(
                ["tar", "-tzf", str(export_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(list_result.returncode, 0)
            self.assertIn("payload/manifest.json", list_result.stdout)
            self.assertIn("payload/deploy.yaml", list_result.stdout)

            manifest_result = subprocess.run(
                ["tar", "-xOzf", str(export_path), "payload/manifest.json"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(manifest_result.returncode, 0)
            manifest = json.loads(manifest_result.stdout)
            self.assertEqual(manifest["format"], 2)

    def test_restore_from_portable_file_without_backup_enabled(self):
        import tarfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_min_repo(root)

            (root / "deploy.yaml").write_text(
                "matrix:\n"
                "  domain: matrix.example.com\n"
                "  server_name: example.com\n"
                "  admin_username: admin\n"
                "backup:\n"
                "  enabled: false\n"
            )

            archive_path = root / "portable.tar.gz"
            payload_root = root / "payload-build"
            payload_root.mkdir()
            (payload_root / "deploy.yaml").write_text((root / "deploy.yaml").read_text())
            (payload_root / ".matrix-easy-deploy").mkdir()
            (payload_root / ".matrix-easy-deploy/secrets.yaml").write_text("POSTGRES_PASSWORD: restored\n")
            (payload_root / ".matrix-easy-deploy/modules.yaml").write_text("{}\n")
            (payload_root / "database").mkdir()
            (payload_root / "database/synapse.dump").write_text("dump\n")
            (payload_root / "manifest.json").write_text(
                '{"format":2,"database_dumps":[{"name":"synapse","path":"database/synapse.dump","db_user":"synapse"}]}\n'
            )

            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(payload_root, arcname="payload")

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

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)

            result = subprocess.run(
                ["bash", "restore.sh", "--file", str(archive_path), "--yes"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            self.assertEqual(lines[0], "stop")
            self.assertEqual(lines[1], "apply")
            self.assertTrue(any("pg_restore" in line for line in lines))
            self.assertEqual(lines[-2], "apply")
            self.assertEqual(lines[-1], "start")

    def test_encrypted_export_round_trip_with_openssl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_min_repo(root)

            fake_bin = root / "bin"
            fake_bin.mkdir(parents=True)
            self._write_executable(
                fake_bin / "docker",
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == \"ps\" ]]; then echo matrix_postgres; exit 0; fi\n"
                "if [[ \"${1:-}\" == \"volume\" && \"${2:-}\" == \"inspect\" ]]; then exit 1; fi\n"
                "if [[ \"${1:-}\" == \"exec\" ]]; then echo dump-bytes; exit 0; fi\n"
                "exit 0\n",
            )

            export_path = root / "portable.tar.gz.age"
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["MED_BACKUP_PASSPHRASE"] = "test-passphrase"

            result = subprocess.run(
                ["bash", "backup.sh", "--export-only", str(export_path), "--encrypt"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(export_path.exists())

            extract_dir = root / "extract"
            extract_dir.mkdir()
            decrypt = subprocess.run(
                [
                    "bash",
                    "-lc",
                    f"source scripts/lib.sh && source scripts/backup_crypto.sh && med_backup_decrypt_stream '{export_path}' | tar -xf - -C '{extract_dir}'",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(decrypt.returncode, 0, msg=decrypt.stderr)
            self.assertTrue((extract_dir / "payload/manifest.json").exists())

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
            combined = result.stderr + result.stdout
            self.assertIn("Provide --archive", combined)

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
