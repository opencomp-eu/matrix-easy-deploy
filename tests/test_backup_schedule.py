import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import backup_schedule


class BackupScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.unit_dir = self.root / "units"

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_service_uses_repo_root_and_backup_script(self):
        content = backup_schedule.render_service(self.root)
        self.assertIn(f"WorkingDirectory={self.root}", content)
        self.assertIn(f"ExecStart=/usr/bin/env bash {self.root / 'backup.sh'}", content)

    def test_render_timer_uses_calendar_and_persistent(self):
        content = backup_schedule.render_timer("daily", True)
        self.assertIn("OnCalendar=daily", content)
        self.assertIn("Persistent=true", content)

    def test_reconcile_installs_timer_when_enabled(self):
        config = {
            "backup": {
                "enabled": True,
                "schedule": {
                    "enabled": True,
                    "calendar": "daily",
                    "persistent": False,
                },
            }
        }
        run_command = Mock()
        run_command.side_effect = [Mock(returncode=0), Mock(returncode=0), Mock(returncode=0)]

        with patch("scripts.backup_schedule.systemd_available", return_value=True):
            status = backup_schedule.reconcile(self.root, config, run_command=run_command, unit_dir=self.unit_dir)

        self.assertEqual(status, "Automatic backup timer installed or updated.")
        self.assertTrue((self.unit_dir / backup_schedule.SERVICE_NAME).exists())
        self.assertTrue((self.unit_dir / backup_schedule.TIMER_NAME).exists())
        self.assertEqual(run_command.call_args_list[0].args[0], ["systemctl", "daemon-reload"])
        self.assertEqual(run_command.call_args_list[1].args[0], ["systemctl", "enable", "--now", backup_schedule.TIMER_NAME])

    def test_reconcile_removes_timer_when_disabled(self):
        config = {"backup": {"enabled": False, "schedule": {"enabled": False}}}
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        (self.unit_dir / backup_schedule.SERVICE_NAME).write_text("service")
        (self.unit_dir / backup_schedule.TIMER_NAME).write_text("timer")
        run_command = Mock()
        run_command.side_effect = [Mock(returncode=0), Mock(returncode=0), Mock(returncode=0)]

        with patch("scripts.backup_schedule.systemd_available", return_value=True):
            status = backup_schedule.reconcile(self.root, config, run_command=run_command, unit_dir=self.unit_dir)

        self.assertEqual(status, "Automatic backup timer removed.")
        self.assertFalse((self.unit_dir / backup_schedule.SERVICE_NAME).exists())
        self.assertFalse((self.unit_dir / backup_schedule.TIMER_NAME).exists())
        self.assertEqual(run_command.call_args_list[0].args[0], ["systemctl", "disable", "--now", backup_schedule.TIMER_NAME])
        self.assertEqual(run_command.call_args_list[1].args[0], ["systemctl", "daemon-reload"])

    def test_reconcile_rejects_schedule_without_backup_enabled(self):
        config = {"backup": {"enabled": False, "schedule": {"enabled": True, "calendar": "daily"}}}

        with self.assertRaises(RuntimeError):
            backup_schedule.reconcile(self.root, config, run_command=Mock(), unit_dir=self.unit_dir)


if __name__ == "__main__":
    unittest.main()