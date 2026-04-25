import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import yaml

from scripts import apply


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # Minimal project structure expected by apply.py
        (self.root / "caddy").mkdir(parents=True)
        (self.root / "modules/core/synapse").mkdir(parents=True)
        (self.root / "modules/core/element").mkdir(parents=True)
        (self.root / "modules/calls/coturn").mkdir(parents=True)
        (self.root / "modules/calls/livekit").mkdir(parents=True)
        (self.root / "modules/hookshot").mkdir(parents=True)
        (self.root / "modules/core/synapse_data").mkdir(parents=True)
        (self.root / "modules/whatsapp-bridge/whatsapp").mkdir(parents=True)
        (self.root / "modules/slack-bridge/slack").mkdir(parents=True)

        (self.root / "caddy/Caddyfile.template").write_text("{{MATRIX_DOMAIN}} {{CADDY_MATRIX_HOSTS}}")
        (self.root / "caddy/Caddyfile-no-element.template").write_text("no-element {{MATRIX_DOMAIN}}")
        (self.root / "modules/core/synapse/homeserver.yaml.template").write_text("server_name: {{SERVER_NAME}}\npublic_baseurl: https://{{MATRIX_DOMAIN}}")
        (self.root / "modules/core/element/config.json.template").write_text('{"base_url":"https://{{MATRIX_DOMAIN}}"}')
        (self.root / "modules/calls/coturn/turnserver.conf.template").write_text("realm={{MATRIX_DOMAIN}}")
        (self.root / "modules/calls/livekit/livekit.yaml.template").write_text("keys:\n  {{LIVEKIT_KEY}}: {{LIVEKIT_SECRET}}")
        (self.root / "modules/hookshot/module.yaml").write_text(
            "name: hookshot\n"
            "config_key: hookshot\n"
            "runtime:\n"
            "  config_exists: modules/hookshot/hookshot/config.yml\n"
        )
        (self.root / "modules/hookshot/setup.sh").write_text("#!/usr/bin/env bash\nexit 0\n")

    def tearDown(self):
        self.tmp.cleanup()

    def sample_config(self):
        return {
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
                "sso": {"enabled": False, "providers": []},
            },
            "modules": {
                "hookshot": {"enabled": False, "domain": "hookshot.example.com"},
                "whatsapp_bridge": {"enabled": False},
                "slack_bridge": {"enabled": False},
            },
        }

    def write_config(self, cfg):
        (self.root / "deploy.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))

    def test_derive_values_federation_and_modules(self):
        cfg = self.sample_config()
        cfg["features"]["federation_enabled"] = False
        cfg["modules"]["hookshot"]["enabled"] = True

        derived = apply.derive_values(cfg, server_ip="1.2.3.4")

        self.assertEqual(derived["FEDERATION_WHITELIST"], "[]")
        self.assertEqual(derived["ALLOW_PUBLIC_ROOMS_FEDERATION"], "false")
        self.assertEqual(derived["SERVER_IP"], "1.2.3.4")
        self.assertEqual(derived["HOOKSHOT_ENABLED"], "true")
        self.assertEqual(derived["WHATSAPP_BRIDGE_ENABLED"], "false")

    def test_derive_values_with_oidc_providers(self):
        cfg = self.sample_config()
        cfg["features"]["sso"] = {
            "enabled": True,
            "providers": [
                {
                    "name": "Google",
                    "issuer": "https://accounts.google.com/",
                    "client_id": "id-1",
                    "client_secret": "secret-1",
                    "allow_registration": True,
                }
            ],
        }

        derived = apply.derive_values(cfg, server_ip="1.2.3.4")

        self.assertEqual(derived["ENABLE_SSO"], "true")
        self.assertEqual(derived["OIDC_PROVIDER_COUNT"], "1")
        self.assertEqual(derived["OIDC_PROVIDER_NAMES"], "Google")
        self.assertIn('"idp_name":"Google"', derived["OIDC_PROVIDERS_JSON"])

    def test_validate_config_rejects_invalid_modules_shape(self):
        cfg = self.sample_config()
        cfg["modules"] = []
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_invalid_enabled_type(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = "yes"
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_backup_path_when_relative(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": True,
            "repository": {"type": "local", "path": "relative/path"},
            "retention": {"keep_daily": 7},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_backup_type_when_not_local(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": True,
            "repository": {"type": "ssh", "path": "/var/backups/med-kit"},
            "retention": {"keep_daily": 7},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_negative_backup_retention(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": False,
            "repository": {"type": "local", "path": "/var/backups/med-kit"},
            "retention": {"keep_daily": -1},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_empty_backup_schedule_calendar_when_enabled(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": True,
            "repository": {"type": "local", "path": "/var/backups/med-kit"},
            "schedule": {"enabled": True, "calendar": ""},
            "retention": {"keep_daily": 7},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_invalid_backup_schedule_persistent_type(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": False,
            "schedule": {"enabled": False, "persistent": "yes"},
            "repository": {"type": "local", "path": "/var/backups/med-kit"},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_enabled_schedule_when_backup_disabled(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": False,
            "repository": {"type": "local", "path": "/var/backups/med-kit"},
            "schedule": {"enabled": True, "calendar": "daily", "persistent": True},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_secrets_are_idempotent(self):
        ctx = apply.ApplyContext(self.root)
        first = apply.create_or_update_secrets(ctx, {})
        second = apply.create_or_update_secrets(ctx, first)

        for key in apply.DEFAULT_SECRET_KEYS + ["LIVEKIT_KEY"]:
            self.assertIn(key, second)
            self.assertEqual(first[key], second[key])

    def test_rotate_secrets_changes_core_values(self):
        ctx = apply.ApplyContext(self.root)
        first = apply.create_or_update_secrets(ctx, {})
        second = apply.create_or_update_secrets(ctx, first, rotate=True)

        changed = [k for k in apply.DEFAULT_SECRET_KEYS if first[k] != second[k]]
        self.assertTrue(changed)
        self.assertEqual(second["LIVEKIT_KEY"], "matrix")

    def test_apply_configuration_writes_env_and_templates(self):
        self.write_config(self.sample_config())
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6")

        env_text = (self.root / ".env").read_text()
        self.assertIn("MATRIX_DOMAIN=matrix.example.com", env_text)
        self.assertIn("SERVER_IP=9.8.7.6", env_text)
        self.assertIn("HOOKSHOT_ENABLED=false", env_text)

        caddy = (self.root / "caddy/Caddyfile").read_text()
        self.assertIn("matrix.example.com", caddy)
        self.assertNotIn("{{", caddy)

        synapse = (self.root / "modules/core/synapse/homeserver.yaml").read_text()
        self.assertIn("server_name: example.com", synapse)
        self.assertNotIn("{{", synapse)

        modules_state = yaml.safe_load((self.root / ".matrix-easy-deploy/modules.yaml").read_text())
        self.assertIn("hookshot", modules_state)
        self.assertFalse(modules_state["hookshot"]["enabled"])

    def test_apply_configuration_writes_bridge_env_values(self):
        cfg = self.sample_config()
        cfg["modules"]["whatsapp_bridge"] = {
            "enabled": True,
            "admin_username": "waadmin",
            "db_name": "wa_custom",
        }
        cfg["modules"]["slack_bridge"] = {
            "enabled": True,
            "admin_username": "sladmin",
            "db_name": "sl_custom",
        }
        self.write_config(cfg)
        (self.root / "modules/whatsapp-bridge/whatsapp/registration.yaml").write_text("wa-reg\n")
        (self.root / "modules/slack-bridge/slack/registration.yaml").write_text("sl-reg\n")
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        env_text = (self.root / ".env").read_text()
        self.assertIn("WA_ADMIN_USERNAME=waadmin", env_text)
        self.assertIn("WA_DB_NAME=wa_custom", env_text)
        self.assertIn("WA_DB_USER=mautrix_whatsapp", env_text)
        self.assertIn("WA_DB_URI=postgres://mautrix_whatsapp:", env_text)
        self.assertIn("SL_ADMIN_USERNAME=sladmin", env_text)
        self.assertIn("SL_DB_NAME=sl_custom", env_text)
        self.assertIn("SL_DB_USER=mautrix_slack", env_text)
        self.assertIn("SL_DB_URI=postgres://mautrix_slack:", env_text)

    def test_apply_reconciles_enabled_bridge_appservice_files_and_homeserver(self):
        cfg = self.sample_config()
        cfg["modules"]["whatsapp_bridge"]["enabled"] = True
        cfg["modules"]["slack_bridge"]["enabled"] = True
        self.write_config(cfg)

        (self.root / "modules/whatsapp-bridge/whatsapp/registration.yaml").write_text("wa-reg\n")
        (self.root / "modules/slack-bridge/slack/registration.yaml").write_text("sl-reg\n")

        ctx = apply.ApplyContext(self.root)
        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        wa_dest = self.root / "modules/core/synapse_data/whatsapp-registration.yaml"
        sl_dest = self.root / "modules/core/synapse_data/slack-registration.yaml"
        self.assertTrue(wa_dest.exists())
        self.assertTrue(sl_dest.exists())
        self.assertEqual(wa_dest.read_text(), "wa-reg\n")
        self.assertEqual(sl_dest.read_text(), "sl-reg\n")

        homeserver = (self.root / "modules/core/synapse/homeserver.yaml").read_text()
        self.assertIn("/data/whatsapp-registration.yaml", homeserver)
        self.assertIn("/data/slack-registration.yaml", homeserver)

    def test_apply_reconciles_disabled_bridge_appservice_cleanup(self):
        cfg = self.sample_config()
        cfg["modules"]["whatsapp_bridge"]["enabled"] = False
        cfg["modules"]["slack_bridge"]["enabled"] = False
        self.write_config(cfg)

        homeserver = self.root / "modules/core/synapse/homeserver.yaml"
        homeserver.parent.mkdir(parents=True, exist_ok=True)
        homeserver.write_text(
            "server_name: example.com\n"
            "app_service_config_files:\n"
            "  - /data/whatsapp-registration.yaml\n"
            "  - /data/slack-registration.yaml\n"
        )

        (self.root / "modules/core/synapse_data/whatsapp-registration.yaml").write_text("wa-reg\n")
        (self.root / "modules/core/synapse_data/slack-registration.yaml").write_text("sl-reg\n")

        ctx = apply.ApplyContext(self.root)
        apply.reconcile_bridge_appservices(ctx, cfg)

        self.assertFalse((self.root / "modules/core/synapse_data/whatsapp-registration.yaml").exists())
        self.assertFalse((self.root / "modules/core/synapse_data/slack-registration.yaml").exists())
        cleaned = homeserver.read_text()
        self.assertNotIn("/data/whatsapp-registration.yaml", cleaned)
        self.assertNotIn("/data/slack-registration.yaml", cleaned)

    def test_apply_reconciles_enabled_hookshot_appservice_and_caddy(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"] = {
            "enabled": True,
            "domain": "hookshot.example.com",
        }
        self.write_config(cfg)

        (self.root / "modules/hookshot/hookshot").mkdir(parents=True, exist_ok=True)
        (self.root / "modules/hookshot/hookshot/registration.yml").write_text("hookshot-reg\n")

        caddy_path = self.root / "caddy/Caddyfile"
        caddy_path.write_text("matrix.example.com {\n    reverse_proxy matrix_synapse:8008\n}\n")

        ctx = apply.ApplyContext(self.root)
        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        hookshot_dest = self.root / "modules/core/synapse_data/hookshot-registration.yml"
        self.assertTrue(hookshot_dest.exists())
        self.assertEqual(hookshot_dest.read_text(), "hookshot-reg\n")

        homeserver = (self.root / "modules/core/synapse/homeserver.yaml").read_text()
        self.assertIn("/data/hookshot-registration.yml", homeserver)

        caddy = caddy_path.read_text()
        self.assertIn("# BEGIN MED-HOOKSHOT BLOCK", caddy)
        self.assertIn("hookshot.example.com {", caddy)

    def test_apply_reconciles_disabled_hookshot_appservice_and_caddy_cleanup(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"] = {
            "enabled": False,
            "domain": "hookshot.example.com",
        }
        self.write_config(cfg)

        homeserver = self.root / "modules/core/synapse/homeserver.yaml"
        homeserver.parent.mkdir(parents=True, exist_ok=True)
        homeserver.write_text(
            "server_name: example.com\n"
            "app_service_config_files:\n"
            "  - /data/hookshot-registration.yml\n"
        )

        (self.root / "modules/core/synapse_data/hookshot-registration.yml").write_text("hookshot-reg\n")

        caddy_path = self.root / "caddy/Caddyfile"
        caddy_path.write_text(
            "matrix.example.com {\n    reverse_proxy matrix_synapse:8008\n}\n\n"
            "# BEGIN MED-HOOKSHOT BLOCK\n"
            "hookshot.example.com {\n    reverse_proxy matrix-hookshot:9000\n}\n"
            "# END MED-HOOKSHOT BLOCK\n"
        )

        ctx = apply.ApplyContext(self.root)
        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        self.assertFalse((self.root / "modules/core/synapse_data/hookshot-registration.yml").exists())
        cleaned_hs = homeserver.read_text()
        self.assertNotIn("/data/hookshot-registration.yml", cleaned_hs)

        cleaned_caddy = caddy_path.read_text()
        self.assertNotIn("# BEGIN MED-HOOKSHOT BLOCK", cleaned_caddy)
        self.assertNotIn("hookshot.example.com {", cleaned_caddy)

    def test_apply_reconciles_disabled_hookshot_legacy_caddy_using_env_domain_fallback(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"] = {
            "enabled": False,
        }
        self.write_config(cfg)

        # Simulate prior deploy state where domain exists in generated .env.
        (self.root / ".env").write_text("HOOKSHOT_DOMAIN=hookshot.example.com\n")

        caddy_path = self.root / "caddy/Caddyfile"
        caddy_path.write_text(
            "hookshot.example.com {\n"
            "    reverse_proxy matrix-hookshot:9000\n"
            "}\n"
            "\n"
            "matrix.example.com {\n"
            "    reverse_proxy matrix_synapse:8008\n"
            "}\n"
        )

        ctx = apply.ApplyContext(self.root)
        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        cleaned_caddy = caddy_path.read_text()
        self.assertNotIn("hookshot.example.com {", cleaned_caddy)
        self.assertIn("matrix.example.com", cleaned_caddy)

    def test_apply_reuses_existing_secrets(self):
        self.write_config(self.sample_config())
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6")
        saved_1 = yaml.safe_load((self.root / ".matrix-easy-deploy/secrets.yaml").read_text())

        apply.apply_configuration(ctx, server_ip="9.8.7.6")
        saved_2 = yaml.safe_load((self.root / ".matrix-easy-deploy/secrets.yaml").read_text())

        self.assertEqual(saved_1, saved_2)

    def test_apply_configuration_reconciles_backup_schedule(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": True,
            "repository": {"type": "local", "path": "/var/backups/med-kit"},
            "schedule": {"enabled": True, "calendar": "daily", "persistent": True},
            "retention": {"keep_daily": 7},
        }
        self.write_config(cfg)
        ctx = apply.ApplyContext(self.root)

        with patch("scripts.apply.backup_schedule.reconcile", return_value="Automatic backup timer installed or updated.") as mock_reconcile:
            apply.apply_configuration(ctx, server_ip="9.8.7.6")

        mock_reconcile.assert_called_once_with(ctx.project_root, cfg)

    def test_run_runtime_reconcile_invokes_stop_then_start(self):
        ctx = apply.ApplyContext(self.root)
        with patch("scripts.apply.subprocess.run") as mock_run:
            apply.run_runtime_reconcile(ctx)

        self.assertEqual(mock_run.call_count, 2)
        first_args = mock_run.call_args_list[0].args[0]
        second_args = mock_run.call_args_list[1].args[0]
        self.assertTrue(str(first_args[1]).endswith("stop.sh"))
        self.assertTrue(str(second_args[1]).endswith("start.sh"))

    def test_module_bootstrap_invoked_when_enabled_and_missing_config(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = True
        cfg["modules"]["hookshot"]["domain"] = "hookshot.example.com"

        ctx = apply.ApplyContext(self.root)
        def _mock_setup(*args, **kwargs):
            hookshot_dir = self.root / "modules/hookshot/hookshot"
            hookshot_dir.mkdir(parents=True, exist_ok=True)
            (hookshot_dir / "config.yml").write_text("ok\n")
            (hookshot_dir / "registration.yml").write_text("ok\n")

        with patch("scripts.apply.subprocess.run") as mock_run:
            mock_run.side_effect = _mock_setup
            apply.reconcile_module_bootstrap(ctx, cfg)

        self.assertEqual(mock_run.call_count, 1)
        call = mock_run.call_args
        cmd = call.args[0]
        env = call.kwargs["env"]
        self.assertTrue(str(cmd[1]).endswith("modules/hookshot/setup.sh"))
        self.assertEqual(env.get("MED_NON_INTERACTIVE"), "1")
        self.assertEqual(env.get("MODULE_HOOKSHOT_DOMAIN"), "hookshot.example.com")

    def test_module_bootstrap_skips_when_config_exists(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = True

        (self.root / "modules/hookshot/hookshot").mkdir(parents=True)
        (self.root / "modules/hookshot/hookshot/config.yml").write_text("ok\n")

        ctx = apply.ApplyContext(self.root)
        with patch("scripts.apply.subprocess.run") as mock_run:
            apply.reconcile_module_bootstrap(ctx, cfg)

        self.assertEqual(mock_run.call_count, 0)

    def test_module_bootstrap_checks_generated_files_not_only_config_exists(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = True

        (self.root / "modules/hookshot/module.yaml").write_text(
            "name: hookshot\n"
            "config_key: hookshot\n"
            "generated_files:\n"
            "  - modules/hookshot/hookshot/config.yml\n"
            "  - modules/hookshot/hookshot/registration.yml\n"
            "runtime:\n"
            "  config_exists: modules/hookshot/hookshot/config.yml\n"
        )

        (self.root / "modules/hookshot/hookshot").mkdir(parents=True)
        (self.root / "modules/hookshot/hookshot/config.yml").write_text("ok\n")

        ctx = apply.ApplyContext(self.root)
        def _mock_setup(*args, **kwargs):
            hookshot_dir = self.root / "modules/hookshot/hookshot"
            hookshot_dir.mkdir(parents=True, exist_ok=True)
            (hookshot_dir / "registration.yml").write_text("ok\n")

        with patch("scripts.apply.subprocess.run") as mock_run:
            mock_run.side_effect = _mock_setup
            apply.reconcile_module_bootstrap(ctx, cfg)

        self.assertEqual(mock_run.call_count, 1)

    def test_module_bootstrap_raises_when_setup_missing_and_files_missing(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = True

        (self.root / "modules/hookshot/setup.sh").unlink()

        ctx = apply.ApplyContext(self.root)
        with self.assertRaises(RuntimeError):
            apply.reconcile_module_bootstrap(ctx, cfg)


if __name__ == "__main__":
    unittest.main()
