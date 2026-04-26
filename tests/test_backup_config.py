import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import backup_config


class BackupConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.deploy_yaml = self.root / "deploy.yaml"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, content: dict):
        self.deploy_yaml.write_text(yaml.safe_dump(content, sort_keys=False))

    def test_load_backup_settings_defaults_when_section_missing(self):
        self._write(
            {
                "matrix": {
                    "domain": "matrix.example.com",
                    "server_name": "example.com",
                    "admin_username": "admin",
                }
            }
        )

        cfg = backup_config.load_backup_settings(self.deploy_yaml)
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["repository"]["type"], "local")
        self.assertEqual(cfg["repository"]["path"], "/var/backups/med-kit")

    def test_load_backup_settings_requires_absolute_path_when_enabled(self):
        self._write(
            {
                "matrix": {
                    "domain": "matrix.example.com",
                    "server_name": "example.com",
                    "admin_username": "admin",
                },
                "backup": {
                    "enabled": True,
                    "repository": {"type": "local", "path": "relative/path"},
                },
            }
        )

        with self.assertRaises(backup_config.BackupConfigError):
            backup_config.load_backup_settings(self.deploy_yaml)

    def test_emit_shell_includes_retention_values(self):
        self._write(
            {
                "matrix": {
                    "domain": "matrix.example.com",
                    "server_name": "example.com",
                    "admin_username": "admin",
                },
                "backup": {
                    "enabled": True,
                    "repository": {"type": "local", "path": "/srv/backups"},
                    "retention": {
                        "keep_daily": 10,
                        "keep_weekly": 8,
                        "keep_monthly": 12,
                        "keep_yearly": 1,
                    },
                },
            }
        )

        settings = backup_config.load_backup_settings(self.deploy_yaml)
        shell = backup_config._emit_shell(settings)

        self.assertIn("BACKUP_ENABLED=true", shell)
        self.assertIn("BACKUP_REPOSITORY_PATH=/srv/backups", shell)
        self.assertIn("BACKUP_KEEP_DAILY=10", shell)
        self.assertIn("BACKUP_KEEP_WEEKLY=8", shell)
        self.assertIn("BACKUP_KEEP_MONTHLY=12", shell)
        self.assertIn("BACKUP_KEEP_YEARLY=1", shell)

    def test_load_backup_settings_includes_schedule_defaults(self):
        self._write(
            {
                "matrix": {
                    "domain": "matrix.example.com",
                    "server_name": "example.com",
                    "admin_username": "admin",
                },
                "backup": {
                    "enabled": True,
                    "repository": {"type": "local", "path": "/srv/backups"},
                },
            }
        )

        cfg = backup_config.load_backup_settings(self.deploy_yaml)
        self.assertFalse(cfg["schedule"]["enabled"])
        self.assertEqual(cfg["schedule"]["calendar"], "*-*-* 03:00:00")
        self.assertTrue(cfg["schedule"]["persistent"])

    def test_load_backup_settings_rejects_empty_schedule_calendar_when_enabled(self):
        self._write(
            {
                "matrix": {
                    "domain": "matrix.example.com",
                    "server_name": "example.com",
                    "admin_username": "admin",
                },
                "backup": {
                    "enabled": True,
                    "repository": {"type": "local", "path": "/srv/backups"},
                    "schedule": {"enabled": True, "calendar": ""},
                },
            }
        )

        with self.assertRaises(backup_config.BackupConfigError):
            backup_config.load_backup_settings(self.deploy_yaml)


if __name__ == "__main__":
    unittest.main()
