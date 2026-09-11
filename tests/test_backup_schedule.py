import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "easydeploy-lib" / "python"))
import backup_schedule  # noqa: E402


class BackupScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.unit_dir = self.root / "units"
        self.deploy_yaml = self.root / "deploy.yaml"
        self.deploy_yaml.write_text(
            "backup:\n"
            "  enabled: true\n"
            "  repository:\n"
            "    path: /var/backups/kit\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_service_uses_repo_root_and_backup_script(self):
        content = backup_schedule.render_service("kit-backup", self.root, "kit backups")
        self.assertIn(f"WorkingDirectory={self.root}", content)
        self.assertIn(f"ExecStart=/usr/bin/env bash {self.root / 'backup.sh'}", content)

    def test_render_timer_uses_calendar_and_persistent(self):
        content = backup_schedule.render_timer("kit-backup", "daily", True)
        self.assertIn("OnCalendar=daily", content)
        self.assertIn("Persistent=true", content)

    def test_reconcile_installs_timer_when_enabled(self):
        self.deploy_yaml.write_text(
            "backup:\n"
            "  enabled: true\n"
            "  repository:\n"
            "    path: /var/backups/kit\n"
            "  schedule:\n"
            "    enabled: true\n"
            "    calendar: daily\n"
            "    persistent: false\n"
        )
        run_command = Mock()
        run_command.side_effect = [Mock(returncode=0), Mock(returncode=0), Mock(returncode=0)]

        with patch("backup_schedule.systemd_available", return_value=True):
            status = backup_schedule.reconcile(
                self.root, self.deploy_yaml, "kit-backup", run_command=run_command, unit_dir=self.unit_dir
            )

        self.assertEqual(status, "Automatic backup timer installed or updated.")
        self.assertTrue((self.unit_dir / "kit-backup.service").exists())
        self.assertTrue((self.unit_dir / "kit-backup.timer").exists())
        self.assertEqual(run_command.call_args_list[0].args[0], ["systemctl", "daemon-reload"])
        self.assertEqual(run_command.call_args_list[1].args[0], ["systemctl", "enable", "--now", "kit-backup.timer"])

    def test_reconcile_removes_timer_when_disabled(self):
        self.deploy_yaml.write_text(
            "backup:\n"
            "  enabled: false\n"
            "  schedule:\n"
            "    enabled: false\n"
        )
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        (self.unit_dir / "kit-backup.service").write_text("service")
        (self.unit_dir / "kit-backup.timer").write_text("timer")
        run_command = Mock()
        run_command.side_effect = [Mock(returncode=0), Mock(returncode=0), Mock(returncode=0)]

        with patch("backup_schedule.systemd_available", return_value=True):
            status = backup_schedule.reconcile(
                self.root, self.deploy_yaml, "kit-backup", run_command=run_command, unit_dir=self.unit_dir
            )

        self.assertEqual(status, "Automatic backup timer removed.")
        self.assertFalse((self.unit_dir / "kit-backup.service").exists())
        self.assertFalse((self.unit_dir / "kit-backup.timer").exists())
        self.assertEqual(run_command.call_args_list[0].args[0], ["systemctl", "disable", "--now", "kit-backup.timer"])
        self.assertEqual(run_command.call_args_list[1].args[0], ["systemctl", "daemon-reload"])

    def test_reconcile_rejects_schedule_without_backup_enabled(self):
        self.deploy_yaml.write_text(
            "backup:\n"
            "  enabled: false\n"
            "  schedule:\n"
            "    enabled: true\n"
            "    calendar: daily\n"
        )

        with self.assertRaises(RuntimeError):
            backup_schedule.reconcile(
                self.root, self.deploy_yaml, "kit-backup", run_command=Mock(), unit_dir=self.unit_dir
            )


if __name__ == "__main__":
    unittest.main()