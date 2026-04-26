import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import config_edit


class ConfigEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.deploy_yaml = self.root / "deploy.yaml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_enable_module_in_existing_file(self):
        config = {
            "matrix": {
                "domain": "matrix.example.com",
                "server_name": "example.com",
                "admin_username": "admin",
            },
            "modules": {
                "hookshot": {"enabled": False, "domain": "hookshot.example.com"},
                "whatsapp_bridge": {"enabled": False},
                "slack_bridge": {"enabled": False},
            },
        }
        self.deploy_yaml.write_text(yaml.safe_dump(config, sort_keys=False))

        rc = config_edit.main([
            "--deploy-yaml",
            str(self.deploy_yaml),
            "--enable-module",
            "hookshot",
        ])

        self.assertEqual(rc, 0)
        updated = yaml.safe_load(self.deploy_yaml.read_text())
        self.assertTrue(updated["modules"]["hookshot"]["enabled"])

    def test_enable_module_initializes_file_when_missing(self):
        rc = config_edit.main([
            "--deploy-yaml",
            str(self.deploy_yaml),
            "--enable-module",
            "whatsapp-bridge",
        ])

        self.assertEqual(rc, 0)
        updated = yaml.safe_load(self.deploy_yaml.read_text())
        self.assertTrue(updated["modules"]["whatsapp_bridge"]["enabled"])

    def test_set_core_updates_without_removing_modules(self):
        initial = {
            "matrix": {
                "domain": "matrix.example.com",
                "server_name": "example.com",
                "admin_username": "admin",
            },
            "features": {
                "registration_enabled": False,
                "federation_enabled": True,
                "element": {"enabled": True, "domain": "element.example.com"},
                "calls": {"enabled": True, "livekit_domain": "livekit.example.com"},
                "sso": {"enabled": True, "providers": [{"name": "Google"}]},
            },
            "modules": {
                "hookshot": {"enabled": True, "domain": "hookshot.example.com"},
            },
        }
        self.deploy_yaml.write_text(yaml.safe_dump(initial, sort_keys=False))

        rc = config_edit.main([
            "--deploy-yaml",
            str(self.deploy_yaml),
            "--set-core",
            "--matrix-domain",
            "matrix.new.example.com",
            "--server-name",
            "new.example.com",
            "--admin-username",
            "root",
            "--registration-enabled",
            "true",
            "--federation-enabled",
            "false",
            "--install-element",
            "false",
            "--element-domain",
            "",
            "--calls-enabled",
            "false",
            "--livekit-domain",
            "",
        ])

        self.assertEqual(rc, 0)
        updated = yaml.safe_load(self.deploy_yaml.read_text())
        self.assertEqual(updated["matrix"]["domain"], "matrix.new.example.com")
        self.assertEqual(updated["matrix"]["server_name"], "new.example.com")
        self.assertEqual(updated["matrix"]["admin_username"], "root")
        self.assertTrue(updated["features"]["registration_enabled"])
        self.assertFalse(updated["features"]["federation_enabled"])
        self.assertFalse(updated["features"]["element"]["enabled"])
        self.assertFalse(updated["features"]["calls"]["enabled"])
        self.assertTrue(updated["modules"]["hookshot"]["enabled"])
        self.assertTrue(updated["features"]["sso"]["enabled"])

    def test_set_module_config_updates_whatsapp_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            deploy_yaml = Path(tmp) / "deploy.yaml"
            deploy_yaml.write_text(
                yaml.safe_dump(
                    {
                        "matrix": {
                            "domain": "matrix.example.com",
                            "server_name": "example.com",
                            "admin_username": "admin",
                        },
                        "modules": {
                            "whatsapp_bridge": {
                                "enabled": False,
                                "admin_username": "oldadmin",
                                "db_name": "old_db",
                            }
                        },
                    },
                    sort_keys=False,
                )
            )

            rc = config_edit.main(
                [
                    "--deploy-yaml",
                    str(deploy_yaml),
                    "--set-module-config",
                    "whatsapp-bridge",
                    "--module-enabled",
                    "true",
                    "--module-admin-username",
                    "newadmin",
                    "--module-db-name",
                    "wa_prod",
                ]
            )

            self.assertEqual(rc, 0)
            data = yaml.safe_load(deploy_yaml.read_text())
            module = data["modules"]["whatsapp_bridge"]
            self.assertTrue(module["enabled"])
            self.assertEqual(module["admin_username"], "newadmin")
            self.assertEqual(module["db_name"], "wa_prod")

    def test_print_module_defaults_for_whatsapp(self):
        initial = {
            "matrix": {
                "domain": "matrix.example.com",
                "server_name": "example.com",
                "admin_username": "admin",
            },
            "modules": {
                "whatsapp_bridge": {
                    "enabled": True,
                    "admin_username": "waadmin",
                    "db_name": "wa_prod",
                }
            },
        }
        self.deploy_yaml.write_text(yaml.safe_dump(initial, sort_keys=False))

        config = config_edit.load_or_init(self.deploy_yaml)
        defaults = config_edit.emit_module_defaults(config, "whatsapp-bridge")

        self.assertIn("module_enabled=true", defaults)
        self.assertIn("module_admin_username=waadmin", defaults)
        self.assertIn("module_db_name=wa_prod", defaults)

    def test_print_wizard_defaults_uses_existing_values(self):
        initial = {
            "matrix": {
                "domain": "matrix.dev.local",
                "server_name": "dev.local",
                "admin_username": "operator",
            },
            "features": {
                "registration_enabled": True,
                "federation_enabled": False,
                "element": {"enabled": False, "domain": ""},
                "calls": {"enabled": True, "livekit_domain": "calls.dev.local"},
            },
        }
        self.deploy_yaml.write_text(yaml.safe_dump(initial, sort_keys=False))

        config = config_edit.load_or_init(self.deploy_yaml)
        defaults = config_edit.emit_wizard_defaults(config)

        self.assertIn("config_matrix_domain=matrix.dev.local", defaults)
        self.assertIn("config_server_name=dev.local", defaults)
        self.assertIn("config_admin_username=operator", defaults)
        self.assertIn("config_registration_default=y", defaults)
        self.assertIn("config_federation_default=n", defaults)
        self.assertIn("config_element_default=n", defaults)
        self.assertIn("config_calls_default=y", defaults)

    def test_set_backup_config_updates_backup_fields(self):
        self.deploy_yaml.write_text(
            yaml.safe_dump(
                {
                    "matrix": {
                        "domain": "matrix.example.com",
                        "server_name": "example.com",
                        "admin_username": "admin",
                    },
                    "backup": {
                        "enabled": False,
                        "repository": {"type": "local", "path": "/var/backups/med-kit"},
                        "schedule": {
                            "enabled": False,
                            "calendar": "*-*-* 03:00:00",
                            "persistent": True,
                        },
                        "retention": {
                            "keep_daily": 7,
                            "keep_weekly": 4,
                            "keep_monthly": 6,
                            "keep_yearly": 0,
                        },
                    },
                },
                sort_keys=False,
            )
        )

        rc = config_edit.main(
            [
                "--deploy-yaml",
                str(self.deploy_yaml),
                "--set-backup-config",
                "--backup-enabled",
                "true",
                "--backup-repository-type",
                "local",
                "--backup-repository-path",
                "/srv/med-kit-backups",
                "--backup-schedule-enabled",
                "true",
                "--backup-schedule-calendar",
                "daily",
                "--backup-schedule-persistent",
                "false",
                "--backup-keep-daily",
                "14",
                "--backup-keep-weekly",
                "8",
                "--backup-keep-monthly",
                "12",
                "--backup-keep-yearly",
                "2",
            ]
        )

        self.assertEqual(rc, 0)
        updated = yaml.safe_load(self.deploy_yaml.read_text())
        backup = updated["backup"]
        self.assertTrue(backup["enabled"])
        self.assertEqual(backup["repository"]["type"], "local")
        self.assertEqual(backup["repository"]["path"], "/srv/med-kit-backups")
        self.assertTrue(backup["schedule"]["enabled"])
        self.assertEqual(backup["schedule"]["calendar"], "daily")
        self.assertFalse(backup["schedule"]["persistent"])
        self.assertEqual(backup["retention"]["keep_daily"], 14)
        self.assertEqual(backup["retention"]["keep_weekly"], 8)
        self.assertEqual(backup["retention"]["keep_monthly"], 12)
        self.assertEqual(backup["retention"]["keep_yearly"], 2)

    def test_print_backup_defaults_uses_existing_values(self):
        self.deploy_yaml.write_text(
            yaml.safe_dump(
                {
                    "matrix": {
                        "domain": "matrix.example.com",
                        "server_name": "example.com",
                        "admin_username": "admin",
                    },
                    "backup": {
                        "enabled": True,
                        "repository": {"type": "local", "path": "/srv/med-kit-backups"},
                        "schedule": {
                            "enabled": True,
                            "calendar": "daily",
                            "persistent": False,
                        },
                        "retention": {
                            "keep_daily": 10,
                            "keep_weekly": 5,
                            "keep_monthly": 9,
                            "keep_yearly": 1,
                        },
                    },
                },
                sort_keys=False,
            )
        )

        defaults = config_edit.emit_backup_defaults(config_edit.load_or_init(self.deploy_yaml))
        self.assertIn("backup_enabled=true", defaults)
        self.assertIn("backup_repository_type=local", defaults)
        self.assertIn("backup_repository_path=/srv/med-kit-backups", defaults)
        self.assertIn("backup_schedule_enabled=true", defaults)
        self.assertIn("backup_schedule_calendar=daily", defaults)
        self.assertIn("backup_schedule_persistent=false", defaults)
        self.assertIn("backup_keep_daily=10", defaults)
        self.assertIn("backup_keep_weekly=5", defaults)
        self.assertIn("backup_keep_monthly=9", defaults)
        self.assertIn("backup_keep_yearly=1", defaults)


if __name__ == "__main__":
    unittest.main()
